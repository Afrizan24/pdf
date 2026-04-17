"""
Rule-based PDF classifier — strict single-pass routing per spec.

Classification priority (highest to lowest):
  1. bilevel_ratio >= 0.5                          → SCAN  (CCITT/JBIG2 fax)
  2. image_area >= 0.9 AND text_length ≈ 0         → SCAN  (pure photo scan)
  3. text_area  >= 0.8 AND image_area < 0.2        → DIGITAL
  4. everything else                               → HYBRID
"""

from __future__ import annotations

from typing import Tuple

from core.features import PdfFeatures

# "text_length ≈ 0" ceiling for the image-dominant SCAN rule.
# Kept tight so HYBRID docs with moderate text are never silently rasterized.
_SCAN_TEXT_ZERO_CEILING = 50


def classify_pdf(
    feats: PdfFeatures,
    text_scan_threshold: int = 20,
    text_digital_threshold: int = 200,
    min_images_for_scan: float = 1.0,
) -> str:
    cls, _ = classify_pdf_with_confidence(
        feats,
        text_scan_threshold=text_scan_threshold,
        text_digital_threshold=text_digital_threshold,
        min_images_for_scan=min_images_for_scan,
    )
    return cls


def classify_pdf_with_confidence(
    feats: PdfFeatures,
    text_scan_threshold: int = 20,
    text_digital_threshold: int = 200,
    min_images_for_scan: float = 1.0,
) -> Tuple[str, str]:
    """
    Classify a PDF and return (class, confidence).

    confidence: "hard" | "medium" | "weak"

    Rules applied in strict priority order:

    Rule 1 — bilevel_ratio >= 0.5
        Majority of images are 1-bit (CCITT fax / JBIG2).
        These are scanned documents. → SCAN (hard)

    Rule 2 — image_area >= 0.9 AND avg_text_len_per_page < _SCAN_TEXT_ZERO_CEILING
        Page is almost entirely image with negligible text.
        Treat as a photo scan. → SCAN (hard)

    Rule 3 — text_area >= 0.8 AND image_area < 0.2
        Page is mostly text with very little imagery.
        Born-digital document. → DIGITAL (medium)

    Rule 4 — everything else → HYBRID (weak)
    """
    bilevel   = feats.bilevel_image_ratio
    img_area  = feats.avg_image_area_ratio
    text_area = feats.avg_text_area_ratio
    avg_text  = feats.avg_text_len_per_page

    # Rule 1 — bilevel majority
    if bilevel >= 0.5:
        return "SCAN", "hard"

    # Rule 2 — image-dominant with no meaningful text
    if img_area >= 0.9 and avg_text < _SCAN_TEXT_ZERO_CEILING:
        return "SCAN", "hard"

    # Rule 3 — text-dominant
    if text_area >= 0.8 and img_area < 0.2:
        return "DIGITAL", "medium"

    # Rule 4 — mixed / ambiguous
    return "HYBRID", "weak"
