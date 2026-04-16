"""Ghostscript detection and PDF compression."""

from __future__ import annotations

import shutil
import subprocess
import time
from typing import Dict, Optional


def _find_ghostscript() -> Optional[str]:
    """Return the first Ghostscript executable found on PATH, or None."""
    for name in ("gs", "gswin64c", "gswin32c"):
        found = shutil.which(name)
        if found:
            return found
    return None


GS_EXECUTABLE: Optional[str] = _find_ghostscript()

# Extra flags applied on top of -dPDFSETTINGS for ilovepdf-level aggressiveness.
_GS_EXTRA_FLAGS = [
    "-dCompatibilityLevel=1.5",         # object streams → smaller xref
    "-dFastWebView=false",              # skip linearisation overhead
    "-dDetectDuplicateImages=true",     # deduplicate identical image streams
    "-dColorImageDownsampleType=/Bicubic",
    "-dGrayImageDownsampleType=/Bicubic",
    "-dMonoImageDownsampleType=/Subsample",
    "-dOptimize=true",
    "-dPrinted=false",
]

# Explicit downsample DPI per PDFSETTINGS preset.
_GS_DPI: Dict[str, int] = {
    "/screen":   72,
    "/ebook":    150,
    "/printer":  300,
    "/prepress": 300,
}


def font_subsetting_gs(
    in_path: str,
    out_path: str,
    pdf_setting: str = "/ebook",
    grayscale: bool = False,
    bilevel: bool = False,
) -> Dict[str, float]:
    """
    Run Ghostscript PDF optimisation with aggressive image downsampling.

    bilevel=True forces /screen + grayscale conversion, which is optimal
    for CCITT/JBIG2 fax-encoded scan documents.

    Raises RuntimeError if Ghostscript is unavailable or exits non-zero.
    """
    if not GS_EXECUTABLE:
        raise RuntimeError("Ghostscript not found. Install Ghostscript.")

    effective_setting = "/screen" if bilevel else pdf_setting
    dpi = _GS_DPI.get(effective_setting, 150)

    cmd = [
        GS_EXECUTABLE,
        "-sDEVICE=pdfwrite",
        "-dSubsetFonts=true",
        "-dEmbedAllFonts=false",
        f"-dPDFSETTINGS={effective_setting}",
        f"-dColorImageResolution={dpi}",
        f"-dGrayImageResolution={dpi}",
        f"-dMonoImageResolution={min(dpi * 2, 300)}",
        "-dDownsampleColorImages=true",
        "-dDownsampleGrayImages=true",
        "-dDownsampleMonoImages=true",
        *_GS_EXTRA_FLAGS,
    ]

    if grayscale or bilevel:
        cmd += [
            "-sColorConversionStrategy=Gray",
            "-dProcessColorModel=/DeviceGray",
        ]

    cmd += ["-dNOPAUSE", "-dQUIET", "-dBATCH", f"-sOutputFile={out_path}", in_path]

    t0     = time.perf_counter()
    result = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = (time.perf_counter() - t0) * 1000.0

    if result.returncode != 0:
        raise RuntimeError(f"Ghostscript error: {result.stderr.strip()}")

    return {"time_ms": elapsed}
