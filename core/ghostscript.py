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

# ---------------------------------------------------------------------------
# Base flags — applied to every GS run
# ---------------------------------------------------------------------------

_GS_BASE_FLAGS = [
    "-dCompatibilityLevel=1.7",
    "-dFastWebView=false",
    "-dDetectDuplicateImages=true",
    "-dOptimize=true",
    "-dPrinted=false",
    "-dCompressFonts=true",
    "-dPreserveEPSInfo=false",
    "-dPreserveOPIComments=false",
    "-dPreserveHalftoneInfo=false",
]

# ---------------------------------------------------------------------------
# Default DPI per PDFSETTINGS preset
# ---------------------------------------------------------------------------

_GS_PRESET_DPI: Dict[str, int] = {
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
    color_dpi: Optional[int] = None,
    gray_dpi: Optional[int] = None,
    mono_dpi: Optional[int] = None,
    jpeg_quality: Optional[int] = None,
    is_scan: bool = False,
) -> Dict[str, float]:
    """
    Run Ghostscript PDF compression.

    Parameters
    ----------
    in_path       : input PDF path
    out_path      : output PDF path
    pdf_setting   : GS -dPDFSETTINGS preset (/screen /ebook /printer /prepress)
    grayscale     : convert all colour to grayscale
    color_dpi     : downsample DPI for colour images (overrides preset default)
    gray_dpi      : downsample DPI for grayscale images (overrides preset default)
    mono_dpi      : downsample DPI for monochrome/bilevel images (overrides preset default)
    jpeg_quality  : JPEG quality 20-100 (auto-selected from DPI if None)
    is_scan       : enable scan-optimized flags (forces DCTEncode, disables auto-filter,
                    uses Subsample for mono, applies stricter downsampling thresholds)

    Raises RuntimeError if Ghostscript is unavailable or exits non-zero.
    """
    if not GS_EXECUTABLE:
        raise RuntimeError("Ghostscript not found. Install Ghostscript.")

    preset_dpi = _GS_PRESET_DPI.get(pdf_setting, 150)
    c_dpi = color_dpi if color_dpi is not None else preset_dpi
    g_dpi = gray_dpi  if gray_dpi  is not None else preset_dpi

    # Mono DPI: scan documents benefit from higher mono DPI to preserve text sharpness.
    # For scan mode, default to 2× color DPI (capped at 600); for digital, cap at 300.
    if mono_dpi is not None:
        m_dpi = mono_dpi
    elif is_scan:
        m_dpi = min(c_dpi * 2, 600)
    else:
        m_dpi = min(preset_dpi * 2, 300)

    # Auto-select JPEG quality based on DPI.
    # Scan documents tolerate lower quality because the source is already lossy (camera/scanner).
    if jpeg_quality is None:
        if is_scan:
            # More aggressive for scan: source is already lossy, lower quality is acceptable
            if c_dpi <= 72:  jpeg_quality = 50
            elif c_dpi <= 100: jpeg_quality = 55
            elif c_dpi <= 150: jpeg_quality = 65
            else:              jpeg_quality = 75
        else:
            if c_dpi <= 100:   jpeg_quality = 60
            elif c_dpi <= 200: jpeg_quality = 75
            else:              jpeg_quality = 85

    cmd = [
        GS_EXECUTABLE,
        "-sDEVICE=pdfwrite",
        "-dSubsetFonts=true",
        "-dEmbedAllFonts=false",
        f"-dPDFSETTINGS={pdf_setting}",
        # Image downsampling
        "-dDownsampleColorImages=true",
        "-dDownsampleGrayImages=true",
        "-dDownsampleMonoImages=true",
        f"-dColorImageResolution={c_dpi}",
        f"-dGrayImageResolution={g_dpi}",
        f"-dMonoImageResolution={m_dpi}",
        # Downsample all images without threshold (threshold=1.0 means always downsample)
        "-dColorImageDownsampleThreshold=1.0",
        "-dGrayImageDownsampleThreshold=1.0",
        "-dMonoImageDownsampleThreshold=1.0",
        *_GS_BASE_FLAGS,
    ]

    if is_scan:
        # Scan-optimized image flags:
        # - Disable auto-filter so GS always uses DCTEncode (not Flate) for color/gray
        # - Bicubic for color/gray (best quality at lower DPI)
        # - Subsample for mono/bilevel (preserves sharp edges better than Bicubic for 1-bit)
        cmd += [
            "-dAutoFilterColorImages=false",
            "-dAutoFilterGrayImages=false",
            "-dColorImageFilter=/DCTEncode",
            "-dGrayImageFilter=/DCTEncode",
            "-dColorImageDownsampleType=/Bicubic",
            "-dGrayImageDownsampleType=/Bicubic",
            "-dMonoImageDownsampleType=/Subsample",
            f"-dJPEGQ={jpeg_quality}",
        ]
    else:
        # Digital/hybrid: allow auto-filter, use Bicubic for all
        cmd += [
            "-dColorImageFilter=/DCTEncode",
            "-dGrayImageFilter=/DCTEncode",
            "-dColorImageDownsampleType=/Bicubic",
            "-dGrayImageDownsampleType=/Bicubic",
            "-dMonoImageDownsampleType=/Subsample",
            f"-dJPEGQ={jpeg_quality}",
        ]

    if grayscale:
        cmd += [
            "-sColorConversionStrategy=Gray",
            "-dProcessColorModel=/DeviceGray",
        ]
    else:
        cmd += [
            "-sColorConversionStrategy=sRGB",
            "-dProcessColorModel=/DeviceRGB",
        ]

    cmd += ["-dNOPAUSE", "-dQUIET", "-dBATCH", f"-sOutputFile={out_path}", in_path]

    t0      = time.perf_counter()
    result  = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = (time.perf_counter() - t0) * 1000.0

    if result.returncode != 0:
        raise RuntimeError(f"Ghostscript error: {result.stderr.strip()}")

    return {"time_ms": elapsed, "jpeg_quality_used": jpeg_quality}
