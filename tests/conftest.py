"""
Shared pytest fixtures — generates synthetic PDF files in-memory.
No external PDF files required.
"""

from __future__ import annotations

import io
import os
import tempfile

import fitz
import pytest
from PIL import Image


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pdf_bytes(pages: int = 3, text: str = "", add_image: bool = False) -> bytes:
    """Create a minimal PDF in memory using PyMuPDF."""
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=595, height=842)  # A4
        if text:
            page.insert_text((72, 100 + i * 20), text, fontsize=12)
        if add_image:
            # Embed a small RGB JPEG image
            img = Image.new("RGB", (100, 100), color=(200, 100, 50))
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            rect = fitz.Rect(72, 200, 172, 300)
            page.insert_image(rect, stream=buf.getvalue())
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_dir(tmp_path):
    """Provide a temporary directory (pytest built-in tmp_path)."""
    return tmp_path


@pytest.fixture
def digital_pdf(tmp_path) -> str:
    """PDF with lots of text, no images → DIGITAL."""
    long_text = "Lorem ipsum dolor sit amet. " * 50  # ~1400 chars/page
    data = _make_pdf_bytes(pages=3, text=long_text, add_image=False)
    path = tmp_path / "digital.pdf"
    path.write_bytes(data)
    return str(path)


@pytest.fixture
def scan_pdf(tmp_path) -> str:
    """PDF with images and minimal text → SCAN."""
    data = _make_pdf_bytes(pages=3, text="", add_image=True)
    path = tmp_path / "scan.pdf"
    path.write_bytes(data)
    return str(path)


@pytest.fixture
def hybrid_pdf(tmp_path) -> str:
    """PDF with both text and images → HYBRID."""
    medium_text = "Some text content. " * 5  # short text
    data = _make_pdf_bytes(pages=3, text=medium_text, add_image=True)
    path = tmp_path / "hybrid.pdf"
    path.write_bytes(data)
    return str(path)


@pytest.fixture
def empty_pdf(tmp_path) -> str:
    """Single blank page PDF."""
    data = _make_pdf_bytes(pages=1, text="", add_image=False)
    path = tmp_path / "empty.pdf"
    path.write_bytes(data)
    return str(path)
