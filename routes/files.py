"""Routes: /status."""

from __future__ import annotations

from flask import Blueprint, jsonify

from core.ghostscript import GS_EXECUTABLE

files_bp = Blueprint("files", __name__)


@files_bp.route("/status")
def status():
    return jsonify({
        "gs_available": GS_EXECUTABLE is not None,
        "gs_executable": GS_EXECUTABLE,
    })
