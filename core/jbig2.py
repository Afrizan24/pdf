"""
JBIG2 encoding via the external `jbig2` binary (jbig2enc).

JBIG2 is typically 3–5× smaller than CCITT Group 4 for 1-bit scanned documents.

Install:
  Windows : pacman -S mingw-w64-x86_64-jbig2enc  (MSYS2)
            Add C:\\msys64\\mingw64\\bin to system PATH.
  Linux   : apt install jbig2enc
  macOS   : brew install jbig2enc

CLI contract (MSYS2 / agl/jbig2enc build):
  Sequential mode : jbig2 -p <input.pbm>          → JBIG2 bytes on stdout
  Symbol mode     : jbig2 -s -p -b <base> <input.pbm>
                    → <base>.sym (global dict) + <base>.0000 (page stream)

When not installed, all functions return None and the pipeline falls back
to leaving 1-bit images untouched (Ghostscript handles them via CCITT).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Optional, Tuple

from PIL import Image


def _find_jbig2() -> Optional[str]:
    """Return path to jbig2 binary, or None if not found."""
    for name in ("jbig2", "jbig2.exe", "jbig2.EXE"):
        found = shutil.which(name)
        if found:
            return found
    return None


JBIG2_EXECUTABLE: Optional[str] = _find_jbig2()


def jbig2_available() -> bool:
    """Return True if the jbig2 binary is available on PATH."""
    return JBIG2_EXECUTABLE is not None


def encode_image_jbig2_sequential(
    img: Image.Image,
    tmp_dir: str,
    idx: int,
) -> Optional[bytes]:
    """
    Encode a 1-bit PIL image as a self-contained JBIG2 stream (sequential/generic mode).

    Command: jbig2 -p <input.pbm>  — PDF-ready JBIG2 bytes written to stdout.
    The result can be embedded directly as a PDF image stream with /JBIG2Decode filter.

    Returns raw JBIG2 bytes, or None on failure.
    """
    if not JBIG2_EXECUTABLE:
        return None

    pbm_path = os.path.join(tmp_dir, f"jbig2_{idx:05d}.pbm")
    try:
        img.convert("1").save(pbm_path)
        result = subprocess.run(
            [JBIG2_EXECUTABLE, "-p", pbm_path],
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0 or not result.stdout:
            return None
        return result.stdout
    except Exception:
        return None
    finally:
        try:
            os.remove(pbm_path)
        except OSError:
            pass


def encode_image_jbig2_symbol(
    img: Image.Image,
    tmp_dir: str,
    idx: int,
) -> Optional[Tuple[bytes, bytes]]:
    """
    Encode a 1-bit PIL image using symbol dictionary mode (~20% smaller than sequential).

    Command: jbig2 -s -p -b <base> <input.pbm>
    Returns (page_stream_bytes, global_dict_bytes), or None on failure.

    Note: embedding symbol-mode JBIG2 in PDF requires writing both the global
    dictionary and the page stream as separate objects. Use sequential mode
    for simpler single-stream embedding.
    """
    if not JBIG2_EXECUTABLE:
        return None

    pbm_path  = os.path.join(tmp_dir, f"jbig2s_{idx:05d}.pbm")
    out_base  = os.path.join(tmp_dir, f"jbig2s_{idx:05d}")
    sym_path  = out_base + ".sym"
    page_path = out_base + ".0000"

    try:
        img.convert("1").save(pbm_path)
        result = subprocess.run(
            [JBIG2_EXECUTABLE, "-s", "-p", "-b", out_base, pbm_path],
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0 or not os.path.exists(page_path):
            return None

        page_bytes   = open(page_path, "rb").read()
        global_bytes = open(sym_path,  "rb").read() if os.path.exists(sym_path) else b""
        return page_bytes, global_bytes
    except Exception:
        return None
    finally:
        for p in (pbm_path, sym_path, page_path):
            try:    os.remove(p)
            except: pass
