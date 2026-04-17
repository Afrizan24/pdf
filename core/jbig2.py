"""
JBIG2 encoding via the external `jbig2` binary (jbig2enc).

Used only for detecting whether jbig2enc is installed (JBIG2_EXECUTABLE).
The pipeline reports its availability in the info dict.

Install:
  Windows : pacman -S mingw-w64-x86_64-jbig2enc  (MSYS2)
            Add C:\\msys64\\mingw64\\bin to system PATH.
  Linux   : apt install jbig2enc
  macOS   : brew install jbig2enc
"""

from __future__ import annotations

import shutil
from typing import Optional


def _find_jbig2() -> Optional[str]:
    """Return path to jbig2 binary, or None if not found."""
    for name in ("jbig2", "jbig2.exe", "jbig2.EXE"):
        found = shutil.which(name)
        if found:
            return found
    return None


JBIG2_EXECUTABLE: Optional[str] = _find_jbig2()
