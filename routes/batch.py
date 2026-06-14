"""
Flask routes for batch compression research:
  POST /batch/run    — start batch job (SSE streaming)
  GET  /batch/csv/<token> — download CSV results
"""
from __future__ import annotations

import json
import os
import queue
import tempfile
import threading
import time
import uuid
from typing import Dict, List

from flask import Blueprint, Response, jsonify, request, send_file
import io

from core.batch import run_batch, results_to_csv, default_grid

batch_bp = Blueprint("batch", __name__)

# In-memory result store
_batch_results: Dict[str, dict] = {}
_batch_lock = threading.Lock()
_RESULT_TTL = 600  # 10 minutes


def _evict_expired() -> None:
    now = time.time()
    with _batch_lock:
        for k in [k for k, v in _batch_results.items() if v["expires"] < now]:
            del _batch_results[k]


@batch_bp.route("/batch/run", methods=["POST"])
def batch_run():
    """
    Accept multiple PDF files + optional param overrides.
    Streams progress via SSE, returns token for CSV download when done.
    """
    files = request.files.getlist("pdfs")
    if not files:
        return jsonify({"error": "No files uploaded."}), 400

    # Parse optional param grid from JSON body
    grid_json = request.form.get("param_grid")
    try:
        param_grid = json.loads(grid_json) if grid_json else None
    except Exception:
        param_grid = None

    evaluate_quality = request.form.get("evaluate_quality", "true").lower() in ("true", "1", "yes")

    # Save uploaded files to temp dir
    tmp_dir = tempfile.mkdtemp(prefix="batch_upload_")
    pdf_paths: List[str] = []
    for f in files:
        if not f.filename.lower().endswith(".pdf"):
            continue
        dest = os.path.join(tmp_dir, f.filename)
        f.save(dest)
        pdf_paths.append(dest)

    if not pdf_paths:
        return jsonify({"error": "No valid PDF files found."}), 400

    def generate():
        def sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data)}\n\n"

        evt_queue: queue.Queue = queue.Queue()
        _SENTINEL = object()

        def on_progress(step: str, pct: int, detail: str) -> None:
            evt_queue.put(sse("progress", {"step": step, "pct": pct, "detail": detail}))

        result_holder: dict = {}
        error_holder:  dict = {}

        def run():
            try:
                results = run_batch(
                    pdf_paths=pdf_paths,
                    param_grid=param_grid,
                    evaluate_quality=evaluate_quality,
                    progress_cb=on_progress,
                )
                result_holder["results"] = results
            except Exception as exc:
                error_holder["msg"] = str(exc)
            finally:
                # Clean up uploaded files
                for p in pdf_paths:
                    try: os.remove(p)
                    except: pass
                try: os.rmdir(tmp_dir)
                except: pass
                evt_queue.put(_SENTINEL)

        threading.Thread(target=run, daemon=True).start()

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
        csv_str = results_to_csv(result_holder["results"])
        with _batch_lock:
            _batch_results[token] = {
                "csv":     csv_str,
                "count":   len(result_holder["results"]),
                "expires": time.time() + _RESULT_TTL,
            }

        yield sse("done", {
            "token":   token,
            "count":   len(result_holder["results"]),
            "summary": _summarize(result_holder["results"]),
            "rows":    result_holder["results"],
        })
        

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@batch_bp.route("/batch/csv/<token>")
def batch_csv(token: str):
    _evict_expired()
    with _batch_lock:
        # Use get() instead of pop() — allow multiple downloads until TTL expires
        entry = _batch_results.get(token)
    if not entry:
        return jsonify({"error": "Token invalid or expired."}), 404
    buf = io.BytesIO(entry["csv"].encode("utf-8"))
    buf.seek(0)
    return send_file(
        buf,
        mimetype="text/csv",
        as_attachment=True,
        download_name="batch_results.csv",
    )


@batch_bp.route("/batch/default_grid")
def get_default_grid():
    """Return the default and adaptive grids as JSON."""
    from core.batch import default_grid, _grid_scan, _grid_digital, _grid_hybrid
    return jsonify({
        "note": "Grid is adaptive per document type when no custom grid is provided.",
        "SCAN":    {"count": len(_grid_scan()),    "grid": _grid_scan()},
        "DIGITAL": {"count": len(_grid_digital()), "grid": _grid_digital()},
        "HYBRID":  {"count": len(_grid_hybrid()),  "grid": _grid_hybrid()},
        "fallback": {"count": len(default_grid()), "grid": default_grid()},
    })


def _summarize(results: List[dict]) -> dict:
    """Compute summary statistics from batch results."""
    valid = [r for r in results if r.get("saving_pct") is not None and r.get("error") is None]
    if not valid:
        return {}
    savings = [r["saving_pct"] for r in valid]
    ssims   = [r["ssim_avg"] for r in valid if r.get("ssim_avg") is not None]
    psnrs   = [r["psnr_avg"] for r in valid if r.get("psnr_avg") is not None]
    return {
        "total_combinations": len(results),
        "successful":         len(valid),
        "saving_avg":         round(sum(savings) / len(savings), 2),
        "saving_max":         round(max(savings), 2),
        "saving_min":         round(min(savings), 2),
        "ssim_avg":           round(sum(ssims) / len(ssims), 4) if ssims else None,
        "psnr_avg":           round(sum(psnrs) / len(psnrs), 2) if psnrs else None,
    }
