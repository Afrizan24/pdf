"""Rule-based PDF classifier — encoding-aware with area-coverage signals."""

from __future__ import annotations

from core.features import PdfFeatures


def classify_pdf(
    feats: PdfFeatures,
    text_scan_threshold: int = 20,
    text_digital_threshold: int = 200,
    min_images_for_scan: float = 1.0,
) -> str:
    """
    Classify a PDF into SCAN / DIGITAL / HYBRID.

    Decision priority (highest to lowest):

    1. Encoding-aware hard rules
       bilevel_ratio >= 0.5              → SCAN  (CCITT/JBIG2 fax — always)
       dominant JPEG/JPEG2000 + images   → SCAN  (photo scan)

    2. Area-coverage rules  (tiebreaker for ambiguous content)
       image_area >= 0.80                → SCAN  (page is mostly image)
       text_area  >= 0.80, image < 0.20  → DIGITAL (page is mostly text)

    3. Character-count heuristics  (fast fallback)
       low avg_text + enough images      → SCAN
       high avg_text + few images        → DIGITAL
       everything else                   → HYBRID
    """
    avg_text  = feats.avg_text_len_per_page
    avg_imgs  = feats.avg_images_per_page
    enc       = feats.dominant_image_encoding
    img_area  = feats.avg_image_area_ratio
    text_area = feats.avg_text_area_ratio

    # ── 1. Encoding-aware hard rules ──────────────────────────────────────
    if feats.bilevel_image_ratio >= 0.5:
        return "SCAN"

    if (enc in ("jpeg", "jpeg2000")
            and avg_text < text_digital_threshold
            and avg_imgs >= min_images_for_scan):
        return "SCAN"

    # ── 2. Area-coverage rules ────────────────────────────────────────────
    # A page where images cover ≥ 80% of the area is a scan regardless of
    # how much extracted text it has (OCR layer on top of a scan image).
    if img_area >= 0.80:
        return "SCAN"

    # A page where text blocks cover ≥ 80% and images cover < 20% is digital.
    if text_area >= 0.80 and img_area < 0.20:
        return "DIGITAL"

    # ── 3. Character-count heuristics ────────────────────────────────────
    if avg_text < text_scan_threshold and avg_imgs >= min_images_for_scan:
        return "SCAN"
    if avg_text >= text_digital_threshold and avg_imgs < 1.0:
        return "DIGITAL"
    return "HYBRID"
