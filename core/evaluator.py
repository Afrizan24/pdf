"""
Quality evaluation for compressed PDFs.

Metrics computed per document type:
  SCAN    : PSNR + SSIM (full-page rasterized comparison)
  DIGITAL : text integrity (Sequence Matcher ratio) + rasterized SSIM
  HYBRID  : rasterized SSIM + text integrity

Rasterized SSIM is used as the primary visual fidelity metric for all types.
Rendering the PDF to pixels before comparison captures font substitution,
antialiasing changes, and image degradation — exactly what a reader sees.

Sweet spot thresholds (recommended for TA):
  SSIM > 0.99  → Low compression  (print/archive quality)
  SSIM > 0.95  → Medium compression (e-book/screen quality)
  SSIM > 0.90  → High compression  (draft/web quality)

  Text integrity (Sequence Matcher) = 1.0 for lossless text operations.
  Minimum acceptable: 0.98 if GS performs font outlining.

No external dependencies beyond PyMuPDF, numpy, and scikit-image.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

import fitz
import numpy as np

try:
    from skimage.metrics import structural_similarity as ssim_metric
    HAS_SKIMAGE = True
except ImportError:
    HAS_SKIMAGE = False


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class QualityResult:
    doc_type: str
    pages_evaluated: int = 0
    # Rasterized image quality (all types)
    psnr_avg: Optional[float] = None
    psnr_min: Optional[float] = None
    ssim_avg: Optional[float] = None
    ssim_min: Optional[float] = None
    # Text integrity (DIGITAL / HYBRID)
    # text_preserved_pct: simple character count ratio (fast)
    # text_sequence_ratio: SequenceMatcher ratio (accurate, detects reordering)
    text_chars_original: int = 0
    text_chars_compressed: int = 0
    text_preserved_pct: Optional[float] = None
    text_sequence_ratio: Optional[float] = None   # 0.0–1.0, 1.0 = identical
    # Page structure
    pages_original: int = 0
    pages_compressed: int = 0
    pages_match: bool = True
    # Per-page detail
    per_page: List[Dict] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "doc_type":              self.doc_type,
            "pages_evaluated":       self.pages_evaluated,
            "psnr_avg":              round(self.psnr_avg, 2) if self.psnr_avg is not None else None,
            "psnr_min":              round(self.psnr_min, 2) if self.psnr_min is not None else None,
            "ssim_avg":              round(self.ssim_avg, 4) if self.ssim_avg is not None else None,
            "ssim_min":              round(self.ssim_min, 4) if self.ssim_min is not None else None,
            "text_chars_original":   self.text_chars_original,
            "text_chars_compressed": self.text_chars_compressed,
            "text_preserved_pct":    round(self.text_preserved_pct, 2) if self.text_preserved_pct is not None else None,
            "text_sequence_ratio":   round(self.text_sequence_ratio, 4) if self.text_sequence_ratio is not None else None,
            "pages_original":        self.pages_original,
            "pages_compressed":      self.pages_compressed,
            "pages_match":           self.pages_match,
        }


# ---------------------------------------------------------------------------
# Low-level image metrics (numpy only)
# ---------------------------------------------------------------------------

def _psnr(a: np.ndarray, b: np.ndarray) -> float:
    """Compute PSNR between two uint8 arrays. Returns 60.0 if identical."""
    mse = float(np.mean((a.astype(np.float32) - b.astype(np.float32)) ** 2))
    if mse == 0.0:
        return 60.0
    return 20.0 * math.log10(255.0 / math.sqrt(mse))


def _ssim(a: np.ndarray, b: np.ndarray) -> float:
    """
    Compute SSIM between two uint8 2-D (grayscale) arrays.
    Uses standard windowed SSIM from scikit-image if available.
    Otherwise falls back to Global SSIM calculation.
    """
    if HAS_SKIMAGE:
        # Check if one of the dimensions is smaller than window_size (usually 7)
        if min(a.shape[0], a.shape[1], b.shape[0], b.shape[1]) < 7:
             return 1.0 # too small to compute standard windowed SSIM
        return float(ssim_metric(a, b, data_range=255))

    af = a.astype(np.float64)
    bf = b.astype(np.float64)
    C1 = (0.01 * 255) ** 2
    C2 = (0.03 * 255) ** 2
    mu_a = float(np.mean(af))
    mu_b = float(np.mean(bf))
    sigma_a = float(np.std(af))
    sigma_b = float(np.std(bf))
    sigma_ab = float(np.mean((af - mu_a) * (bf - mu_b)))
    num = (2.0 * mu_a * mu_b + C1) * (2.0 * sigma_ab + C2)
    den = (mu_a**2 + mu_b**2 + C1) * (sigma_a**2 + sigma_b**2 + C2)
    return float(num / den) if den != 0 else 1.0


def _render_page_gray(doc: fitz.Document, page_idx: int, dpi: int = 100) -> np.ndarray:
    """Render a PDF page to a grayscale numpy array at given DPI."""
    page = doc.load_page(page_idx)
    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom),
                          colorspace=fitz.csGRAY, alpha=False)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).copy()


def _resize_to_match(a: np.ndarray, b: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Crop both arrays to the smaller of the two shapes."""
    h = min(a.shape[0], b.shape[0])
    w = min(a.shape[1], b.shape[1])
    return a[:h, :w], b[:h, :w]


# ---------------------------------------------------------------------------
# Per-page evaluation
# ---------------------------------------------------------------------------

def _eval_page_image(orig_doc: fitz.Document,
                     comp_doc: fitz.Document,
                     page_idx: int,
                     dpi: int = 100) -> Dict:
    """Render and compare one page. Returns dict with psnr and ssim."""
    try:
        a = _render_page_gray(orig_doc, page_idx, dpi)
        b = _render_page_gray(comp_doc, page_idx, dpi)
        a, b = _resize_to_match(a, b)
        psnr = _psnr(a, b)
        ssim = _ssim(a, b)
        return {"page": page_idx + 1, "psnr": round(psnr, 2), "ssim": round(ssim, 4)}
    except Exception as exc:
        return {"page": page_idx + 1, "psnr": None, "ssim": None, "error": str(exc)}


# ---------------------------------------------------------------------------
# Main evaluation entry point
# ---------------------------------------------------------------------------

_EVAL_DPI = 150       # render DPI for SSIM — 150 balances accuracy and speed
                      # 100 = fast (batch), 150 = recommended (TA), 300 = publication
_MAX_EVAL_PAGES = 20  # max pages to sample for large documents


def evaluate(
    original_path: str,
    compressed_path: str,
    doc_type: str,
    max_pages: int = _MAX_EVAL_PAGES,
    eval_dpi: int = _EVAL_DPI,
) -> QualityResult:
    """
    Evaluate quality of compressed PDF vs original.

    Parameters
    ----------
    original_path   : path to original PDF
    compressed_path : path to compressed PDF
    doc_type        : 'SCAN' | 'DIGITAL' | 'HYBRID'
    max_pages       : max pages to sample for image metrics
    eval_dpi        : render DPI for PSNR/SSIM computation

    Returns QualityResult with all applicable metrics.
    """
    result = QualityResult(doc_type=doc_type)

    orig_doc = fitz.open(original_path)
    comp_doc = fitz.open(compressed_path)

    try:
        result.pages_original   = orig_doc.page_count
        result.pages_compressed = comp_doc.page_count
        result.pages_match      = (orig_doc.page_count == comp_doc.page_count)

        n_pages = min(orig_doc.page_count, comp_doc.page_count)

        # ── Text integrity (DIGITAL / HYBRID) ────────────────────────────
        # Two metrics:
        # 1. text_preserved_pct — fast character count ratio
        # 2. text_sequence_ratio — SequenceMatcher ratio (detects reordering,
        #    substitution, font outlining). This is the primary metric for TA.
        if doc_type in ("DIGITAL", "HYBRID"):
            orig_pages_text = [
                (orig_doc.load_page(i).get_text("text") or "").strip()
                for i in range(orig_doc.page_count)
            ]
            comp_pages_text = [
                (comp_doc.load_page(i).get_text("text") or "").strip()
                for i in range(comp_doc.page_count)
            ]
            orig_full = "\n".join(orig_pages_text)
            comp_full = "\n".join(comp_pages_text)

            orig_len = len(orig_full)
            comp_len = len(comp_full)
            result.text_chars_original   = orig_len
            result.text_chars_compressed = comp_len

            if orig_len > 0:
                result.text_preserved_pct = min(comp_len / orig_len * 100.0, 100.0)
                # SequenceMatcher on full text — use autojunk=False for accuracy
                # Sample if text is very long to keep it fast
                if orig_len > 50_000:
                    # Sample first 25k + last 25k chars
                    orig_sample = orig_full[:25_000] + orig_full[-25_000:]
                    comp_sample = comp_full[:25_000] + comp_full[-25_000:]
                else:
                    orig_sample = orig_full
                    comp_sample = comp_full
                result.text_sequence_ratio = SequenceMatcher(
                    None, orig_sample, comp_sample, autojunk=False
                ).ratio()
            else:
                result.text_preserved_pct  = 100.0
                result.text_sequence_ratio = 1.0

        # ── Image quality (all types) ─────────────────────────────────────
        if n_pages <= max_pages:
            sample_indices = list(range(n_pages))
        else:
            step = n_pages / max_pages
            sample_indices = [int(i * step) for i in range(max_pages)]

        psnr_values: List[float] = []
        ssim_values: List[float] = []

        for idx in sample_indices:
            page_result = _eval_page_image(orig_doc, comp_doc, idx, eval_dpi)
            result.per_page.append(page_result)
            if page_result.get("psnr") is not None:
                psnr_values.append(min(page_result["psnr"], 60.0))
                ssim_values.append(page_result["ssim"])

        result.pages_evaluated = len(psnr_values)

        if psnr_values:
            result.psnr_avg = sum(psnr_values) / len(psnr_values)
            result.psnr_min = min(psnr_values)
        if ssim_values:
            result.ssim_avg = sum(ssim_values) / len(ssim_values)
            result.ssim_min = min(ssim_values)

    finally:
        orig_doc.close()
        comp_doc.close()

    return result
