"""
Routes: /preview, /compress/stream (SSE), /compress/download/<token>
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import threading
import time
import uuid
from typing import Dict

from flask import Blueprint, Response, jsonify, request, send_file

from core.compressor import compress as pdf_compress
from core.features import extract_features

compress_bp = Blueprint("compress", __name__)

# In-memory store: token → {"bytes": ..., "expires": float}
# Entries expire after 5 minutes — user must download before then.
_results: Dict[str, dict] = {}
_results_lock = threading.Lock()
_RESULT_TTL = 300  # seconds


def _evict_expired() -> None:
    now = time.time()
    with _results_lock:
        expired = [k for k, v in _results.items() if v["expires"] < now]
        for k in expired:
            del _results[k]


# ---------------------------------------------------------------------------
# /preview
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
            "pages": feats.pages,
            "file_size_bytes": feats.file_size_bytes,
            "total_text_len": feats.total_text_len,
            "total_images": feats.total_images,
            "avg_text_len_per_page": round(feats.avg_text_len_per_page, 2),
            "avg_images_per_page": round(feats.avg_images_per_page, 2),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


# ---------------------------------------------------------------------------
# /compress/stream  — Server-Sent Events
# ---------------------------------------------------------------------------

@compress_bp.route("/compress/stream", methods=["POST"])
def compress_stream():
    file = request.files.get("pdf")
    if not file or not file.filename:
        return jsonify({"error": "No file uploaded."}), 400
    if not file.filename.lower().endswith(".pdf"):
        return jsonify({"error": "File must be a .pdf"}), 400

    # ── Parse ALL params here, before any threading ──────────────────────
    # request context is NOT available inside threads or generators in Flask dev server
    def _int(key, default):
        try:   return int(request.form.get(key, default))
        except: return default

    def _float(key, default):
        try:   return float(request.form.get(key, default))
        except: return default

    def _bool(key, default=False):
        val = request.form.get(key)
        if val is None: return default
        return val.lower() in ("true", "1", "yes", "on")

    params = {
        "mode":        request.form.get("mode", "AUTO").upper(),
        "dpi":         _int("dpi", 150),
        "jpeg_q":      _int("jpeg_q", 75),
        "grayscale":   _bool("grayscale", False),
        "garbage":     _int("garbage", 4),
        "deflate":     _bool("deflate", True),
        "clean":       _bool("clean", True),
        "pdf_setting": request.form.get("pdf_setting", "/ebook"),
        "scan_th":     _int("scan_th", 20),
        "digital_th":  _int("digital_th", 200),
        "min_img":     _float("min_img", 1.0),
        "max_size_gs": _float("max_size_gs", 50.0),
    }

    if params["mode"] not in ("AUTO", "DIGITAL", "SCAN", "HYBRID"):
        return jsonify({"error": f"Invalid mode: {params['mode']}"}), 400

    # Save upload to disk before streaming — file object is not thread-safe
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        file.save(tmp.name)
        tmp_path = tmp.name

    def generate():
        def sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data)}\n\n"

        def on_progress(step: str, pct: int, detail: str) -> None:
            yield_queue.append(sse("progress", {
                "step": step, "pct": pct, "detail": detail
            }))

        yield_queue = []

        # Run compression in a thread so we can yield SSE from generator
        result_holder = {}
        error_holder = {}

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
                result_holder["info"] = info
            except Exception as e:
                error_holder["msg"] = str(e)
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                result_holder["done"] = True

        t = threading.Thread(target=run, daemon=True)
        t.start()

        # Stream progress events while thread runs
        while not result_holder.get("done") and not error_holder:
            while yield_queue:
                yield yield_queue.pop(0)
            time.sleep(0.05)

        # Drain remaining events
        while yield_queue:
            yield yield_queue.pop(0)

        if error_holder:
            yield sse("error", {"message": error_holder["msg"]})
            return

        # Store result for download
        _evict_expired()
        token = str(uuid.uuid4())
        with _results_lock:
            _results[token] = {
                "bytes": result_holder["bytes"],
                "expires": time.time() + _RESULT_TTL,
            }

        yield sse("done", {"token": token, "info": result_holder["info"]})

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------
# /compress/download/<token>
# ---------------------------------------------------------------------------

@compress_bp.route("/compress/download/<token>")
def compress_download(token: str):
    _evict_expired()
    with _results_lock:
        entry = _results.pop(token, None)

    if not entry:
        return jsonify({"error": "Token tidak valid atau sudah kadaluarsa."}), 404

    buf = io.BytesIO(entry["bytes"])
    buf.seek(0)
    return send_file(buf, mimetype="application/pdf",
                     as_attachment=True, download_name="compressed.pdf")
