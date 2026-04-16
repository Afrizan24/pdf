"""Flask route: /status — reports available compression tools."""

from __future__ import annotations

from flask import Blueprint, jsonify

from core.ghostscript import GS_EXECUTABLE
from core.jbig2 import JBIG2_EXECUTABLE

files_bp = Blueprint("files", __name__)


@files_bp.route("/status")
def status():
    return jsonify({
        "gs_available":     GS_EXECUTABLE is not None,
        "gs_executable":    GS_EXECUTABLE,
        "jbig2_available":  JBIG2_EXECUTABLE is not None,
        "jbig2_executable": JBIG2_EXECUTABLE,
    })
