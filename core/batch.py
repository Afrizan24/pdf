"""
Batch compression + quality evaluation for research.

Runs an adaptive parameter grid per document type across multiple PDF files,
measures compression ratio and quality (PSNR/SSIM/text integrity),
and exports results to CSV for analysis.

Grid strategy per doc type:
  SCAN    — DPI sweep + JPEG quality sweep (image params dominate)
  DIGITAL — pdf_setting sweep + pikepdf on/off (structure params dominate)
  HYBRID  — combined: DPI + pdf_setting + JPEG quality
"""
from __future__ import annotations

import csv
import io
import os
import tempfile
from typing import Callable, Dict, List, Optional

from core.compressor import compress, COMPRESSION_LEVELS
from core.evaluator import evaluate
from core.features import extract_features
from core.classifier import classify_pdf_with_confidence

from core.compressor import compress, COMPRESSION_LEVELS
from core.evaluator import evaluate
from core.features import extract_features
from core.classifier import classify_pdf_with_confidence


# ---------------------------------------------------------------------------
# Adaptive parameter grids per document type
# ---------------------------------------------------------------------------

def _grid_scan() -> List[Dict]:
    """
    Grid for SCAN documents (image-heavy).
    Varies: GS preset + DPI. JPEG quality auto from DPI.
    9 combinations: 3 levels × 3 DPI points each.
    """
    grid = []
    for level, dpi_list in [
        ("HIGH",   [36, 72, 100]),
        ("MEDIUM", [100, 120, 150]),
        ("LOW",    [150, 200, 300]),
    ]:
        for dpi in dpi_list:
            grid.append({
                "level":            level,
                "pdf_setting":      None,
                "color_dpi":        dpi,
                "gray_dpi":         dpi,
                "mono_dpi":         min(dpi * 2, 600),
                "jpeg_quality":     None,
                "grayscale":        False,
                "pikepdf_optimize": True,
                "label":            f"SCAN_{level}_dpi{dpi}",
            })
    return grid


def _grid_digital() -> List[Dict]:
    """
    Grid for DIGITAL documents (text-heavy, vector).
    Varies: GS preset + pikepdf on/off.
    DPI has no effect on text — only preset and structure matter.
    8 combinations: 4 presets × pikepdf on/off.
    """
    grid = []
    for setting in ("/screen", "/ebook", "/printer", "/prepress"):
        for pikepdf in (True, False):
            label_suffix = "" if pikepdf else "_nopike"
            grid.append({
                "level":            "MEDIUM",
                "pdf_setting":      setting,
                "color_dpi":        None,
                "gray_dpi":         None,
                "mono_dpi":         None,
                "jpeg_quality":     None,
                "grayscale":        False,
                "pikepdf_optimize": pikepdf,
                "label":            f"DIGITAL_{setting.strip('/')}{label_suffix}",
            })
    return grid


def _grid_hybrid() -> List[Dict]:
    """
    Grid for HYBRID documents (mixed text + images).
    Varies: GS preset + DPI. JPEG quality auto from DPI.
    9 combinations: 3 presets × 3 DPI points each.
    """
    grid = []
    for setting, dpi_list in [
        ("/screen",  [72, 100, 120]),
        ("/ebook",   [100, 150, 200]),
        ("/printer", [150, 200, 300]),
    ]:
        for dpi in dpi_list:
            grid.append({
                "level":            "MEDIUM",
                "pdf_setting":      setting,
                "color_dpi":        dpi,
                "gray_dpi":         dpi,
                "mono_dpi":         min(dpi * 2, 600),
                "jpeg_quality":     None,
                "grayscale":        False,
                "pikepdf_optimize": True,
                "label":            f"HYBRID_{setting.strip('/')}_dpi{dpi}",
            })
    return grid


def adaptive_grid(doc_type: str) -> List[Dict]:
    """Return the appropriate parameter grid for a given document type."""
    if doc_type == "SCAN":
        return _grid_scan()
    elif doc_type == "DIGITAL":
        return _grid_digital()
    else:  # HYBRID or unknown
        return _grid_hybrid()


def default_grid() -> List[Dict]:
    """
    Fallback grid used when doc_type is unknown.
    Covers preset + DPI combinations. 12 combinations.
    """
    grid = []
    for setting, dpi_list in [
        ("/screen",   [72, 100]),
        ("/ebook",    [100, 150]),
        ("/printer",  [150, 200]),
        ("/prepress", [200, 300]),
    ]:
        for dpi in dpi_list:
            grid.append({
                "level":            "MEDIUM",
                "pdf_setting":      setting,
                "color_dpi":        dpi,
                "gray_dpi":         dpi,
                "mono_dpi":         min(dpi * 2, 600),
                "jpeg_quality":     None,
                "grayscale":        False,
                "pikepdf_optimize": True,
                "label":            f"{setting.strip('/')}_dpi{dpi}",
            })
    return grid


# ---------------------------------------------------------------------------
# Single file + single param set
# ---------------------------------------------------------------------------

def run_one(
    pdf_path: str,
    params: Dict,
    evaluate_quality: bool = True,
    progress_cb: Optional[Callable] = None,
) -> Dict:
    """
    Compress one PDF with given params and evaluate quality.
    Returns a flat dict suitable for CSV export.
    """
    filename = os.path.basename(pdf_path)
    original_size = os.path.getsize(pdf_path)

    # Feature extraction for classification info
    feats = extract_features(pdf_path)
    detected, confidence = classify_pdf_with_confidence(feats)

    row: Dict = {
        "filename":              filename,
        "original_size_bytes":   original_size,
        "original_size_mb":      round(original_size / 1_048_576, 3),
        "pages":                 feats.pages,
        "doc_type_detected":     detected,
        "classification_conf":   confidence,
        "dominant_encoding":     feats.dominant_image_encoding,
        "bilevel_ratio":         round(feats.bilevel_image_ratio, 3),
        "image_area_ratio":      feats.avg_image_area_ratio,
        "text_area_ratio":       feats.avg_text_area_ratio,
        # Params used
        "param_label":           params.get("label", "custom"),
        "level":                 params.get("level", "MEDIUM"),
        "pdf_setting":           params.get("pdf_setting") or COMPRESSION_LEVELS.get(params.get("level","MEDIUM"),{}).get("pdf_setting",""),
        "color_dpi":             params.get("color_dpi") or COMPRESSION_LEVELS.get(params.get("level","MEDIUM"),{}).get("color_dpi",""),
        "gray_dpi":              params.get("gray_dpi") or COMPRESSION_LEVELS.get(params.get("level","MEDIUM"),{}).get("gray_dpi",""),
        "mono_dpi":              params.get("mono_dpi") or COMPRESSION_LEVELS.get(params.get("level","MEDIUM"),{}).get("mono_dpi",""),
        "jpeg_quality":          params.get("jpeg_quality") or "auto",
        "grayscale":             params.get("grayscale", False),
        "pikepdf_optimize":      params.get("pikepdf_optimize", True),
        # Results (filled below)
        "compressed_size_bytes": None,
        "compressed_size_mb":    None,
        "saving_pct":            None,
        "ratio":                 None,
        "time_ms":               None,
        "gs_used":               None,
        "jpeg_quality_used":     None,
        # Quality metrics (filled below)
        "psnr_avg":              None,
        "psnr_min":              None,
        "ssim_avg":              None,
        "ssim_min":              None,
        "text_preserved_pct":    None,
        "text_sequence_ratio":   None,
        "pages_match":           None,
        "error":                 None,
    }

    tmp_dir = tempfile.mkdtemp(prefix="batch_")
    compressed_path = os.path.join(tmp_dir, "compressed.pdf")

    try:
        pdf_bytes, info = compress(
            in_path=pdf_path,
            mode="AUTO",
            level=params.get("level", "MEDIUM"),
            pdf_setting=params.get("pdf_setting"),
            color_dpi=params.get("color_dpi"),
            gray_dpi=params.get("gray_dpi"),
            mono_dpi=params.get("mono_dpi"),
            jpeg_quality=params.get("jpeg_quality"),
            grayscale=params.get("grayscale", False),
            pikepdf_optimize=params.get("pikepdf_optimize", True),
            progress_cb=progress_cb,
        )

        with open(compressed_path, "wb") as f:
            f.write(pdf_bytes)

        comp_size = len(pdf_bytes)
        row["compressed_size_bytes"] = comp_size
        row["compressed_size_mb"]    = round(comp_size / 1_048_576, 3)
        row["saving_pct"]            = round(info.get("saving_pct", 0), 2)
        row["ratio"]                 = round(info.get("ratio", 1), 4)
        row["time_ms"]               = round(info.get("time_ms", 0), 0)
        row["gs_used"]               = info.get("gs_used", False)
        row["jpeg_quality_used"]     = info.get("jpeg_quality_used")

        # Quality evaluation — always run if enabled, regardless of size change
        if evaluate_quality:
            try:
                q = evaluate(pdf_path, compressed_path, detected)
                row["psnr_avg"]              = round(q.psnr_avg, 2) if q.psnr_avg is not None else None
                row["psnr_min"]              = round(q.psnr_min, 2) if q.psnr_min is not None else None
                row["ssim_avg"]              = round(q.ssim_avg, 4) if q.ssim_avg is not None else None
                row["ssim_min"]              = round(q.ssim_min, 4) if q.ssim_min is not None else None
                row["text_preserved_pct"]    = q.text_preserved_pct
                row["text_sequence_ratio"]   = round(q.text_sequence_ratio, 4) if q.text_sequence_ratio is not None else None
                row["pages_match"]           = q.pages_match
                # Note: PSNR=60/SSIM=1.0 on DIGITAL/HYBRID is expected —
                # vector text renders identically before and after compression.
                # Quality degradation only shows on image-heavy pages.
            except Exception as qe:
                row["error"] = f"quality_eval: {qe}"

    except Exception as e:
        row["error"] = str(e)
    finally:
        try:
            if os.path.exists(compressed_path):
                os.remove(compressed_path)
            os.rmdir(tmp_dir)
        except Exception:
            pass

    return row


# ---------------------------------------------------------------------------
# Batch runner
# ---------------------------------------------------------------------------

def run_batch(
    pdf_paths: List[str],
    param_grid: Optional[List[Dict]] = None,
    evaluate_quality: bool = True,
    progress_cb: Optional[Callable[[str, int, str], None]] = None,
) -> List[Dict]:
    """
    Run compression + evaluation for all files × adaptive parameter grid.

    When param_grid is None (default), each file gets its own adaptive grid
    based on its detected document type:
      SCAN    → DPI sweep + JPEG quality sweep  (12 combinations)
      DIGITAL → pdf_setting sweep + pikepdf on/off  (8 combinations)
      HYBRID  → preset + DPI + JPEG quality  (12 combinations)

    When param_grid is provided explicitly, all files use the same grid.

    Parameters
    ----------
    pdf_paths        : list of PDF file paths
    param_grid       : explicit grid (None = adaptive per file)
    evaluate_quality : whether to compute PSNR/SSIM after compression
    progress_cb      : optional callback(step, pct, detail)
    """
    use_adaptive = param_grid is None
    results: List[Dict] = []

    # Pre-classify all files to compute total combinations for progress
    file_grids: List[tuple] = []
    for pdf_path in pdf_paths:
        if use_adaptive:
            feats = extract_features(pdf_path)
            doc_type, _ = classify_pdf_with_confidence(feats)
            grid = adaptive_grid(doc_type)
        else:
            grid = param_grid
        file_grids.append((pdf_path, grid))

    total = sum(len(g) for _, g in file_grids)
    done  = 0

    for pdf_path, grid in file_grids:
        fname = os.path.basename(pdf_path)
        for params in grid:
            label = params.get("label", "?")
            if progress_cb:
                pct = int(done / total * 100)
                progress_cb("batch", pct,
                            f"[{done+1}/{total}] {fname} | {label}")
            row = run_one(pdf_path, params, evaluate_quality)
            results.append(row)
            done += 1

    if progress_cb:
        progress_cb("batch_done", 100, f"Done — {done} combinations across {len(pdf_paths)} file(s)")

    return results


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

def results_to_csv(results: List[Dict]) -> str:
    """Convert list of result dicts to CSV string."""
    if not results:
        return ""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(results[0].keys()))
    writer.writeheader()
    writer.writerows(results)
    return buf.getvalue()
