"""PDF feature extraction — fast, sample-based for large documents."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Tuple

import fitz  # PyMuPDF


def _union_area(rects: List[fitz.Rect]) -> float:
    """
    Compute the total area covered by a list of (possibly overlapping) rects.

    Uses a simple sweep: sort by x0, then greedily merge overlapping rects
    into a coverage set.  For the small number of images per page this is
    fast enough without a full interval-tree.
    """
    if not rects:
        return 0.0
    # Sort by top-left x then y for deterministic merging
    rects = sorted(rects, key=lambda r: (r.x0, r.y0))
    merged: List[fitz.Rect] = []
    for r in rects:
        if not merged:
            merged.append(fitz.Rect(r))
            continue
        last = merged[-1]
        # Check if r overlaps with last (simple 2-D overlap test)
        if r.x0 < last.x1 and r.y0 < last.y1 and r.x1 > last.x0 and r.y1 > last.y0:
            # Expand last to cover both
            merged[-1] = fitz.Rect(
                min(last.x0, r.x0), min(last.y0, r.y0),
                max(last.x1, r.x1), max(last.y1, r.y1),
            )
        else:
            merged.append(fitz.Rect(r))
    return sum(r.width * r.height for r in merged)



@dataclass
class PdfFeatures:
    """Extracted features used for pipeline routing and classification."""

    pages: int
    file_size_bytes: int
    total_text_len: int
    total_images: int
    avg_text_len_per_page: float
    avg_images_per_page: float
    # Dominant image encoding: "ccitt" | "jbig2" | "jpeg" | "jpeg2000" | "flate" | "mixed" | "none"
    dominant_image_encoding: str = "none"
    # Fraction of images that are 1-bit bilevel (CCITT fax / JBIG2)
    bilevel_image_ratio: float = 0.0
    # Average fraction of page area covered by images (0.0–1.0).
    # Computed from image bounding boxes — no pixel decoding required.
    avg_image_area_ratio: float = 0.0
    # Average fraction of page area that contains text blocks (0.0–1.0).
    avg_text_area_ratio: float = 0.0


# Docs with more pages than this are sampled instead of fully iterated.
_FULL_SCAN_LIMIT = 100
_SAMPLE_SIZE     = 30


def _detect_image_encodings(doc: fitz.Document) -> Tuple[str, float]:
    """
    Detect dominant image encoding by reading raw xref dicts — no pixel decoding.
    Returns (dominant_label, bilevel_ratio).
    """
    pages = doc.page_count
    step  = max(1, pages // _SAMPLE_SIZE)
    counts: Dict[str, int] = {}
    total = bilevel = 0
    seen: set = set()

    for i in range(0, pages, step):
        page = doc.load_page(i)
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            if xref in seen:
                continue
            seen.add(xref)
            try:
                xdict = doc.xref_object(xref, compressed=False)
                bpc   = img_info[7] if len(img_info) > 7 else 8

                if   "CCITTFaxDecode" in xdict: label = "ccitt"
                elif "JBIG2Decode"    in xdict: label = "jbig2"
                elif "DCTDecode"      in xdict: label = "jpeg"
                elif "JPXDecode"      in xdict: label = "jpeg2000"
                else:                           label = "flate"

                counts[label] = counts.get(label, 0) + 1
                total += 1
                if bpc == 1 or label in ("ccitt", "jbig2"):
                    bilevel += 1
            except Exception:
                continue

    if not total:
        return "none", 0.0

    dominant = max(counts, key=lambda k: counts[k])
    if counts[dominant] / total < 0.6:
        dominant = "mixed"

    return dominant, bilevel / total


def _compute_area_ratios(doc: fitz.Document, sample_indices: List[int]) -> Tuple[float, float]:
    """
    Compute average image-area and text-area coverage ratios across sampled pages.

    Uses bounding boxes only — no pixel rendering, no image decoding.
    Image rects come from get_image_rects(); text rects from get_text("dict").

    Returns (avg_image_area_ratio, avg_text_area_ratio), each in [0.0, 1.0].
    """
    img_ratios:  List[float] = []
    text_ratios: List[float] = []

    for i in sample_indices:
        try:
            page      = doc.load_page(i)
            page_area = page.rect.width * page.rect.height
            if page_area <= 0:
                continue

            # ── Image area ────────────────────────────────────────────────
            # get_image_rects returns the on-page bounding boxes of all images.
            # We union overlapping rects before summing to avoid double-counting.
            img_rects: List[fitz.Rect] = []
            for img_info in page.get_images(full=True):
                xref = img_info[0]
                try:
                    rects = page.get_image_rects(xref)
                    for r in rects:
                        clipped = r & page.rect
                        if not clipped.is_empty:
                            img_rects.append(clipped)
                except Exception:
                    continue

            # Union-merge overlapping rects to avoid double-counting.
            img_area = _union_area(img_rects)
            img_ratios.append(min(img_area / page_area, 1.0))

            # ── Text area ─────────────────────────────────────────────────
            # Sum bounding boxes of all text spans from the structured dict.
            text_area = 0.0
            try:
                blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE).get("blocks", [])
                for block in blocks:
                    if block.get("type") != 0:   # 0 = text block
                        continue
                    r = fitz.Rect(block["bbox"])
                    clipped = r & page.rect
                    if not clipped.is_empty:
                        text_area += clipped.width * clipped.height
            except Exception:
                pass
            text_ratios.append(min(text_area / page_area, 1.0))

        except Exception:
            continue

    avg_img  = sum(img_ratios)  / len(img_ratios)  if img_ratios  else 0.0
    avg_text = sum(text_ratios) / len(text_ratios) if text_ratios else 0.0
    return avg_img, avg_text


def extract_features(pdf_path: str) -> PdfFeatures:
    """
    Extract PDF features for classification and pipeline routing.

    Fully iterates docs ≤ 100 pages; samples and extrapolates for larger ones.
    Area ratios are computed on the same sample set — no extra passes needed.
    """
    file_size = os.path.getsize(pdf_path)
    doc = fitz.open(pdf_path)
    try:
        pages = doc.page_count

        if pages <= _FULL_SCAN_LIMIT:
            sample_indices = list(range(pages))
            total_text = total_imgs = 0
            for i in sample_indices:
                pg = doc.load_page(i)
                total_text += len((pg.get_text("text") or "").strip())
                total_imgs += len(pg.get_images(full=True))
        else:
            step           = max(1, pages // _SAMPLE_SIZE)
            sample_indices = list(range(0, pages, step))[:_SAMPLE_SIZE]
            sample_text    = sample_imgs = 0
            for i in sample_indices:
                pg = doc.load_page(i)
                sample_text += len((pg.get_text("text") or "").strip())
                sample_imgs += len(pg.get_images(full=True))
            scale      = pages / len(sample_indices)
            total_text = int(sample_text * scale)
            total_imgs = int(sample_imgs * scale)

        dominant_enc, bilevel_ratio = _detect_image_encodings(doc)
        avg_img_area, avg_text_area = _compute_area_ratios(doc, sample_indices)

        return PdfFeatures(
            pages=pages,
            file_size_bytes=file_size,
            total_text_len=total_text,
            total_images=total_imgs,
            avg_text_len_per_page=total_text / max(pages, 1),
            avg_images_per_page=total_imgs / max(pages, 1),
            dominant_image_encoding=dominant_enc,
            bilevel_image_ratio=bilevel_ratio,
            avg_image_area_ratio=round(avg_img_area, 3),
            avg_text_area_ratio=round(avg_text_area, 3),
        )
    finally:
        doc.close()
