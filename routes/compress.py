"""
Flask routes: /preview, /compress/stream (SSE), /compress/download/<token>
"""

from __future__ import annotations

import io
import json
import os
import queue
import tempfile
import threading
import time
import uuid
from typing import Dict

from flask import Blueprint, Response, jsonify, request, send_file

from core.compressor import compress as pdf_compress
from core.features import extract_features

compress_bp = Blueprint("compress", __name__)

# In-memory result store: token → {bytes, filename, expires}.
# Tokens expire after 5 minutes — client must download before then.
_results: Dict[str, dict] = {}
_results_lock = threading.Lock()
_RESULT_TTL       = 300    # seconds
_MAX_RESULTS_MB   = 500    # total in-memory cap across all pending tokens


def _evict_expired() -> None:
    """Remove tokens whose TTL has elapsed."""
    now = time.time()
    with _results_lock:
        for k in [k for k, v in _results.items() if v["expires"] < now]:
            del _results[k]


def _evict_to_fit(new_bytes: int) -> None:
    """
    If adding new_bytes would exceed _MAX_RESULTS_MB, evict the oldest
    entries (by expiry time) until there is room.
    """
    cap = _MAX_RESULTS_MB * 1024 * 1024
    with _results_lock:
        current = sum(len(v["bytes"]) for v in _results.values())
        if current + new_bytes <= cap:
            return
        # Sort by expiry ascending — evict soonest-to-expire first
        ordered = sorted(_results.items(), key=lambda kv: kv[1]["expires"])
        for k, v in ordered:
            del _results[k]
            current -= len(v["bytes"])
            if current + new_bytes <= cap:
                break


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

    def _int_or_none(key):
        val = request.form.get(key)
        if val is None or val.strip() == "" or val.strip() == "0":
            return None
        try:   return int(val)
        except: return None

    def _str_or_none(key):
        val = request.form.get(key)
        return val if val and val.strip() else None

    return {
        "mode":             request.form.get("mode", "AUTO").upper(),
        "level":            request.form.get("level", "MEDIUM").upper(),
        "pdf_setting":      _str_or_none("pdf_setting"),
        "color_dpi":        _int_or_none("color_dpi"),
        "gray_dpi":         _int_or_none("gray_dpi"),
        "mono_dpi":         _int_or_none("mono_dpi"),
        "jpeg_quality":     _int_or_none("jpeg_quality"),
        "grayscale":        _bool("grayscale"),
        "pikepdf_optimize": _bool("pikepdf_optimize", True),
        "scan_th":          _int("scan_th", 20),
        "digital_th":       _int("digital_th", 200),
        "min_img":          _float("min_img", 1.0),
        "max_size_gs":      _float("max_size_gs", 200.0),
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

    # Derive a safe download filename from the original upload name.
    orig_name = file.filename or "document.pdf"
    stem = orig_name.rsplit(".", 1)[0] if "." in orig_name else orig_name
    download_name = f"{stem}_compressed.pdf"

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        file.save(tmp.name)
        tmp_path = tmp.name

    def generate():
        def sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data)}\n\n"

        # Use a Queue instead of a deque + busy-wait.
        # The worker puts events; the generator blocks on get() with a timeout.
        evt_queue: queue.Queue = queue.Queue()
        _SENTINEL = object()  # signals worker completion

        def on_progress(step: str, pct: int, detail: str) -> None:
            evt_queue.put(sse("progress", {"step": step, "pct": pct, "detail": detail}))

        result_holder: dict = {}
        error_holder:  dict = {}

        def run():
            try:
                pdf_bytes, info = pdf_compress(
                    in_path=tmp_path,
                    mode=params["mode"],
                    level=params["level"],
                    pdf_setting=params["pdf_setting"],
                    color_dpi=params["color_dpi"],
                    gray_dpi=params["gray_dpi"],
                    mono_dpi=params["mono_dpi"],
                    jpeg_quality=params["jpeg_quality"],
                    grayscale=params["grayscale"],
                    pikepdf_optimize=params["pikepdf_optimize"],
                    scan_text_threshold=params["scan_th"],
                    digital_text_threshold=params["digital_th"],
                    min_images_for_scan=params["min_img"],
                    max_size_for_gs_mb=params["max_size_gs"],
                    progress_cb=on_progress,
                )
                result_holder["bytes"]    = pdf_bytes
                result_holder["info"]     = info
            except Exception as exc:
                error_holder["msg"] = str(exc)
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                evt_queue.put(_SENTINEL)

        threading.Thread(target=run, daemon=True).start()

        # Drain the queue until the sentinel arrives.
        while True:
            try:
                item = evt_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if item is _SENTINEL:
                break
            yield item

        if error_holder:
            yield sse("error", {"message": error_holder["msg"]})
            return

        _evict_expired()
        token = str(uuid.uuid4())
        pdf_bytes = result_holder["bytes"]
        _evict_to_fit(len(pdf_bytes))
        with _results_lock:
            _results[token] = {
                "bytes":         pdf_bytes,
                "download_name": download_name,
                "expires":       time.time() + _RESULT_TTL,
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
    return send_file(
        buf,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=entry.get("download_name", "compressed.pdf"),
    )
