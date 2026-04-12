"""Rule-based PDF classifier."""

from __future__ import annotations

from core.features import PdfFeatures


def classify_pdf(
    feats: PdfFeatures,
    text_scan_threshold: int = 20,
    text_digital_threshold: int = 200,
    min_images_for_scan: float = 1.0,
) -> str:
    """
    Classify a PDF based on heuristics:
      SCAN    : low text + avg >= 1 image/page
      DIGITAL : high text + few images
      HYBRID  : everything else
    """
    avg_text = feats.avg_text_len_per_page
    avg_imgs = feats.avg_images_per_page

    if avg_text < text_scan_threshold and avg_imgs >= min_images_for_scan:
        return "SCAN"
    if avg_text >= text_digital_threshold and avg_imgs < 1.0:
        return "DIGITAL"
    return "HYBRID"
