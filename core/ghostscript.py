"""Ghostscript detection and font-subsetting compression."""

from __future__ import annotations

import shutil
import subprocess
import time
from typing import Dict, Optional


def _find_ghostscript() -> Optional[str]:
    """Find the Ghostscript executable available on the system."""
    for name in ["gs", "gswin64c", "gswin32c"]:
        if shutil.which(name):
            return name
    return None


GS_EXECUTABLE: Optional[str] = _find_ghostscript()


def font_subsetting_gs(
    in_path: str,
    out_path: str,
    pdf_setting: str = "/ebook",
    grayscale: bool = False,
) -> Dict[str, float]:
    """
    Font subsetting + optimisation via Ghostscript.
    Raises RuntimeError if Ghostscript is not available.
    """
    if not GS_EXECUTABLE:
        raise RuntimeError(
            "Ghostscript not found. Install Ghostscript or use SCAN mode."
        )

    t0 = time.perf_counter()
    cmd = [
        GS_EXECUTABLE,
        "-sDEVICE=pdfwrite",
        "-dSubsetFonts=true",
        "-dEmbedAllFonts=false",
    ]

    if grayscale:
        cmd.extend([
            "-sColorConversionStrategy=Gray",
            "-dProcessColorModel=/DeviceGray",
        ])

    cmd.extend([
        f"-dPDFSETTINGS={pdf_setting}",
        "-dNOPAUSE",
        "-dQUIET",
        "-dBATCH",
        f"-sOutputFile={out_path}",
        in_path,
    ])

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Ghostscript error: {result.stderr.strip()}")

    return {"time_ms": (time.perf_counter() - t0) * 1000.0}
