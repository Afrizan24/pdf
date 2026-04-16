"""
Flask routes: /preview, /compress/stream (SSE), /compress/download/<token>
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import threading
import time
import uuid
from collections import deque
from typing import Dict

from flask import Blueprint, Response, jsonify, request, send_file

from core.compressor import compress as pdf_compress
from core.features import extract_features

compress_bp = Blueprint("compress", __name__)

# In-memory result store: token → {bytes, expires}.
# Tokens expire after 5 minutes — client must download before then.
_results: Dict[str, dict] = {}
_results_lock = threading.Lock()
_RESULT_TTL   = 300  # seconds


def _evict_expired() -> None:
    now = time.time()
    with _results_lock:
        for k in [k for k, v in _results.items() if v["expires"] < now]:
            del _results[k]


# ---------------------------------------------------------------------------
# /preview — extract features without compressing
# ---------------------------------------------------------------------------

@compress_bp.route("/preview", methods=["POST"])
def preview():
    file = request.files.get("pdf")
    if not file or not file.filename:
        return jsonify({"error": "No file uploaded."}), 400
    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "File must be a .pdf"}), 400

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        file.save(tmp.name)
        tmp_path = tmp.name

    try:
        feats = extract_features(tmp_path)
        return jsonify({
            "pages":                 feats.pages,
            "file_size_bytes":       feats.file_size_bytes,
            "total_text_len":        feats.total_text_len,
            "total_images":          feats.total_images,
            "avg_text_len_per_page": round(feats.avg_text_len_per_page, 2),
            "avg_images_per_page":   round(feats.avg_images_per_page, 2),
            "avg_image_area_ratio":  feats.avg_image_area_ratio,
            "avg_text_area_ratio":   feats.avg_text_area_ratio,
            "dominant_image_encoding": feats.dominant_image_encoding,
            "bilevel_image_ratio":   round(feats.bilevel_image_ratio, 2),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


# ---------------------------------------------------------------------------
# /compress/stream — Server-Sent Events
# ---------------------------------------------------------------------------

def _parse_params() -> dict:
    """Parse all compression parameters from the current request form."""
    def _int(key, default):
        try:   return int(request.form.get(key, default))
        except: return default

    def _float(key, default):
        try:   return float(request.form.get(key, default))
        except: return default

    def _bool(key, default=False):
        val = request.form.get(key)
        if val is None:
            return default
        return val.lower() in ("true", "1", "yes", "on")

    return {
        "mode":        request.form.get("mode", "AUTO").upper(),
        "dpi":         _int("dpi", 150),
        "jpeg_q":      _int("jpeg_q", 75),
        "grayscale":   _bool("grayscale"),
        "garbage":     _int("garbage", 4),
        "deflate":     _bool("deflate", True),
        "clean":       _bool("clean", True),
        "pdf_setting": request.form.get("pdf_setting", "/ebook"),
        "scan_th":     _int("scan_th", 20),
        "digital_th":  _int("digital_th", 200),
        "min_img":     _float("min_img", 1.0),
        "max_size_gs": _float("max_size_gs", 200.0),
    }


@compress_bp.route("/compress/stream", methods=["POST"])
def compress_stream():
    file = request.files.get("pdf")
    if not file or not file.filename:
        return jsonify({"error": "No file uploaded."}), 400
    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "File must be a .pdf"}), 400

    # Parse params before spawning threads — request context is not thread-safe.
    params = _parse_params()
    if params["mode"] not in ("AUTO", "DIGITAL", "SCAN", "HYBRID"):
        return jsonify({"error": f"Invalid mode: {params['mode']}"}), 400

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        file.save(tmp.name)
        tmp_path = tmp.name

    def generate():
        def sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data)}\n\n"

        queue: deque = deque()  # thread-safe for append/popleft

        def on_progress(step: str, pct: int, detail: str) -> None:
            queue.append(sse("progress", {"step": step, "pct": pct, "detail": detail}))

        result_holder: dict = {}
        error_holder:  dict = {}

        def run():
            try:
                pdf_bytes, info = pdf_compress(
                    in_path=tmp_path,
                    mode=params["mode"],
                    dpi=params["dpi"],
                    jpeg_quality=params["jpeg_q"],
                    grayscale=params["grayscale"],
                    garbage=params["garbage"],
                    deflate=params["deflate"],
                    clean=params["clean"],
                    pdf_setting=params["pdf_setting"],
                    scan_text_threshold=params["scan_th"],
                    digital_text_threshold=params["digital_th"],
                    min_images_for_scan=params["min_img"],
                    max_size_for_gs_mb=params["max_size_gs"],
                    progress_cb=on_progress,
                )
                result_holder["bytes"] = pdf_bytes
                result_holder["info"]  = info
            except Exception as exc:
                error_holder["msg"] = str(exc)
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                result_holder["done"] = True

        threading.Thread(target=run, daemon=True).start()

        while not result_holder.get("done") and not error_holder:
            while queue:
                yield queue.popleft()
            time.sleep(0.05)

        while queue:
            yield queue.popleft()

        if error_holder:
            yield sse("error", {"message": error_holder["msg"]})
            return

        _evict_expired()
        token = str(uuid.uuid4())
        with _results_lock:
            _results[token] = {
                "bytes":   result_holder["bytes"],
                "expires": time.time() + _RESULT_TTL,
            }

        yield sse("done", {"token": token, "info": result_holder["info"]})

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# /compress/download/<token>
# ---------------------------------------------------------------------------

@compress_bp.route("/compress/download/<token>")
def compress_download(token: str):
    _evict_expired()
    with _results_lock:
        entry = _results.pop(token, None)

    if not entry:
        return jsonify({"error": "Token invalid or expired."}), 404

    buf = io.BytesIO(entry["bytes"])
    buf.seek(0)
    return send_file(buf, mimetype="application/pdf",
                     as_attachment=True, download_name="compressed.pdf")
