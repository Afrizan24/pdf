"""PDF feature extraction."""

from __future__ import annotations

import os
from dataclasses import dataclass

import fitz  # PyMuPDF


@dataclass
class PdfFeatures:
    pages: int
    file_size_bytes: int
    total_text_len: int
    total_images: int
    avg_text_len_per_page: float
    avg_images_per_page: float


def extract_features(pdf_path: str) -> PdfFeatures:
    """Read a PDF and extract features for classification."""
    file_size = os.path.getsize(pdf_path)
    doc = fitz.open(pdf_path)
    try:
        pages = doc.page_count
        total_text_len = 0
        total_images = 0

        for i in range(pages):
            page = doc.load_page(i)
            txt = page.get_text("text") or ""
            total_text_len += len(txt.strip())
            total_images += len(page.get_images(full=True))

        avg_text = total_text_len / max(pages, 1)
        avg_imgs = total_images / max(pages, 1)

        return PdfFeatures(
            pages=pages,
            file_size_bytes=file_size,
            total_text_len=total_text_len,
            total_images=total_images,
            avg_text_len_per_page=avg_text,
            avg_images_per_page=avg_imgs,
        )
    finally:
        doc.close()
