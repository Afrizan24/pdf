"""
Rule-based PDF classifier — strict single-pass routing per spec.

Classification priority (highest to lowest):
  1. Only text (text_area > 0 and image_area == 0) -> DIGITAL
  2. Only image (image_area > 0 and text_area == 0) -> SCAN
  3. Both text and image (text_area > 0 and image_area > 0) -> HYBRID
  4. Empty / unknown -> HYBRID
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

    Rule 1 — text_area > 0 AND image_area == 0
        Page is mostly text with no imagery.
        Born-digital document. → DIGITAL (hard)

    Rule 2 — image_area > 0 AND text_area == 0
        Page is almost entirely image with no text.
        Treat as a photo scan. → SCAN (hard)

    Rule 3 — text_area > 0 AND image_area > 0
        Has both text and images. → HYBRID (medium)

    Rule 4 — everything else (e.g. empty) → HYBRID (weak)
    """
    bilevel   = feats.bilevel_image_ratio
    img_area  = feats.avg_image_area_ratio
    text_area = feats.avg_text_area_ratio
    avg_text  = feats.avg_text_len_per_page

    if text_area > 0 and img_area == 0:
        return "DIGITAL", "hard"

    if img_area > 0 and text_area == 0:
        return "SCAN", "hard"

    if text_area > 0 and img_area > 0:
        return "HYBRID", "medium"

    return "HYBRID", "weak"
