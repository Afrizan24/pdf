"""
Batch Parameter Tuning — core logic for parameter sweep and sweet spot analysis.

Provides three main functions:
  generate_grid_from_config(config)  — Cartesian product of all parameter values
  run_tuning(pdf_paths, config, ...)  — sweep all files × all combinations
  compute_sweet_spot(results, ...)    — pick optimal params per level
  results_to_csv(results)             — serialize results to CSV string

Helper functions:
  _make_range(param_dict)     — convert {"min", "max", "step"} to list of values
  _normalize_pikepdf(val)     — convert bool or list to list of bools
"""

from __future__ import annotations

import csv
import io
import os
import tempfile
from itertools import product
from typing import Callable, Dict, List, Optional

from core.classifier import classify_pdf_with_confidence
from core.compressor import compress
from core.evaluator import evaluate
from core.features import extract_features


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _make_range(param_dict: Dict) -> List[int]:
    """
    Convert a {"min": x, "max": y, "step": z} dict to a list of integer values.

    Edge cases:
    - step == 0  → return [min]
    - range would be empty (min > max) → return [min]
    - normal case → list(range(min, max + step, step))
    """
    lo   = int(param_dict["min"])
    hi   = int(param_dict["max"])
    step = int(param_dict.get("step", 1))

    if step <= 0:
        return [lo]

    values = list(range(lo, hi + step, step))
    # Filter out values that exceed max (range overshoot)
    values = [v for v in values if v <= hi]

    if not values:
        return [lo]

    return values


def _normalize_pikepdf(val) -> List[bool]:
    """
    Convert pikepdf_optimize value to a list of bools.

    - True  → [True]
    - False → [False]
    - [True, False] → [True, False]
    - [True] → [True]
    """
    if isinstance(val, bool):
        return [val]
    if isinstance(val, list):
        return [bool(v) for v in val]
    # Fallback: treat as bool
    return [bool(val)]


# ---------------------------------------------------------------------------
# Grid generation
# ---------------------------------------------------------------------------

def generate_grid_from_config(config: Dict) -> List[Dict]:
    """
    Generate all parameter combinations from a tuning configuration.

    Iterates over each level (HIGH/MEDIUM/LOW) where ``enabled: True``.
    For each level, computes the Cartesian product of all parameter value lists.

    Parameters
    ----------
    config : dict
        Tuning configuration with structure::

            {
                "HIGH": {
                    "enabled": True,
                    "color_dpi":    {"min": 36, "max": 100, "step": 18},
                    "gray_dpi":     {"min": 36, "max": 100, "step": 18},
                    "mono_dpi":     {"min": 72, "max": 200, "step": 36},
                    "jpeg_quality": {"min": 40, "max": 80,  "step": 20},
                    "pdf_settings": ["/screen"],
                    "pikepdf_optimize": True   # bool or [True, False]
                },
                ...
            }

    Returns
    -------
    list of dicts, each with keys:
        level, pdf_setting, color_dpi, gray_dpi, mono_dpi,
        jpeg_quality, pikepdf_optimize, label
    """
    grid: List[Dict] = []

    for level in ("HIGH", "MEDIUM", "LOW"):
        level_cfg = config.get(level, {})
        if not level_cfg.get("enabled", False):
            continue

        # Build value lists for each dimension
        color_dpi_values  = _make_range(level_cfg["color_dpi"])
        gray_dpi_values   = _make_range(level_cfg["gray_dpi"])
        mono_dpi_values   = _make_range(level_cfg["mono_dpi"])
        jpeg_quality_values = _make_range(level_cfg["jpeg_quality"])
        pdf_settings      = list(level_cfg.get("pdf_settings", ["/screen"]))
        pikepdf_values    = _normalize_pikepdf(level_cfg.get("pikepdf_optimize", True))

        # Cartesian product: pdf_setting × color_dpi × gray_dpi × mono_dpi × jpeg_quality × pikepdf
        for pdf_setting, color_dpi, gray_dpi, mono_dpi, jpeg_quality, pikepdf_opt in product(
            pdf_settings,
            color_dpi_values,
            gray_dpi_values,
            mono_dpi_values,
            jpeg_quality_values,
            pikepdf_values,
        ):
            # Label format: {LEVEL}_{setting_without_slash}_cdpi{color_dpi}_jq{jpeg_quality}_pike{0|1}
            setting_slug = pdf_setting.lstrip("/")
            pike_flag    = 1 if pikepdf_opt else 0
            label = f"{level}_{setting_slug}_cdpi{color_dpi}_jq{jpeg_quality}_pike{pike_flag}"

            grid.append({
                "level":            level,
                "pdf_setting":      pdf_setting,
                "color_dpi":        color_dpi,
                "gray_dpi":         gray_dpi,
                "mono_dpi":         mono_dpi,
                "jpeg_quality":     jpeg_quality,
                "pikepdf_optimize": pikepdf_opt,
                "label":            label,
            })

    return grid


# ---------------------------------------------------------------------------
# Tuning runner
# ---------------------------------------------------------------------------

def run_tuning(
    pdf_paths: List[str],
    config: Dict,
    evaluate_quality: bool = True,
    progress_cb: Optional[Callable[[str, int, str], None]] = None,
) -> List[Dict]:
    """
    Run parameter sweep for all files × all parameter combinations.

    For each file × combination:
    1. Calls ``compress()`` from ``core/compressor.py``
    2. Calls ``evaluate()`` from ``core/evaluator.py`` (if evaluate_quality=True)
    3. Catches exceptions per combination — fills ``error`` field, continues

    Parameters
    ----------
    pdf_paths        : list of PDF file paths to process
    config           : tuning configuration (same format as generate_grid_from_config)
    evaluate_quality : whether to compute PSNR/SSIM after compression
    progress_cb      : optional callback(step, pct, detail)

    Returns
    -------
    list of TuningResult dicts
    """
    grid = generate_grid_from_config(config)
    total = len(pdf_paths) * len(grid)
    done  = 0
    results: List[Dict] = []

    for pdf_path in pdf_paths:
        fname = os.path.basename(pdf_path)
        original_size = os.path.getsize(pdf_path) if os.path.exists(pdf_path) else 0

        # Classify once per file for evaluate() doc_type
        try:
            feats = extract_features(pdf_path)
            doc_type, _ = classify_pdf_with_confidence(feats)
        except Exception:
            doc_type = "HYBRID"

        for params in grid:
            label = params["label"]

            if progress_cb and total > 0:
                pct = int(done / total * 100)
                progress_cb("tuning", pct, f"[{done}/{total}] {fname} | {label}")

            row: Dict = {
                "filename":              fname,
                "level":                 params["level"],
                "param_label":           label,
                "pdf_setting":           params["pdf_setting"],
                "color_dpi":             params["color_dpi"],
                "gray_dpi":              params["gray_dpi"],
                "mono_dpi":              params["mono_dpi"],
                "jpeg_quality":          params["jpeg_quality"],
                "pikepdf_optimize":      params["pikepdf_optimize"],
                "original_size_bytes":   original_size,
                "compressed_size_bytes": None,
                "saving_pct":            None,
                "ratio":                 None,
                "time_ms":               None,
                "ssim_avg":              None,
                "ssim_min":              None,
                "psnr_avg":              None,
                "psnr_min":              None,
                "text_preserved_pct":    None,
                "text_sequence_ratio":   None,
                "pages_match":           None,
                "error":                 None,
            }

            tmp_dir = tempfile.mkdtemp(prefix="tuning_")
            compressed_path = os.path.join(tmp_dir, "compressed.pdf")

            try:
                pdf_bytes, info = compress(
                    in_path=pdf_path,
                    mode="AUTO",
                    level=params["level"],
                    pdf_setting=params["pdf_setting"],
                    color_dpi=params["color_dpi"],
                    gray_dpi=params["gray_dpi"],
                    mono_dpi=params["mono_dpi"],
                    jpeg_quality=params["jpeg_quality"],
                    pikepdf_optimize=params["pikepdf_optimize"],
                )

                with open(compressed_path, "wb") as f:
                    f.write(pdf_bytes)

                comp_size = len(pdf_bytes)
                row["compressed_size_bytes"] = comp_size
                row["saving_pct"]            = round(info.get("saving_pct", 0.0), 2)
                row["ratio"]                 = round(info.get("ratio", 1.0), 4)
                row["time_ms"]               = round(info.get("time_ms", 0.0), 2)

                if evaluate_quality:
                    try:
                        q = evaluate(pdf_path, compressed_path, doc_type)
                        row["ssim_avg"]           = round(q.ssim_avg, 4) if q.ssim_avg is not None else None
                        row["ssim_min"]           = round(q.ssim_min, 4) if q.ssim_min is not None else None
                        row["psnr_avg"]           = round(q.psnr_avg, 2) if q.psnr_avg is not None else None
                        row["psnr_min"]           = round(q.psnr_min, 2) if q.psnr_min is not None else None
                        row["text_preserved_pct"] = q.text_preserved_pct
                        row["text_sequence_ratio"] = (
                            round(q.text_sequence_ratio, 4)
                            if q.text_sequence_ratio is not None else None
                        )
                        row["pages_match"] = q.pages_match
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

            results.append(row)
            done += 1

    if progress_cb:
        progress_cb("tuning", 100, f"Done — {done} combinations across {len(pdf_paths)} file(s)")

    return results


# ---------------------------------------------------------------------------
# Sweet spot analysis
# ---------------------------------------------------------------------------

def compute_sweet_spot(
    results: List[Dict],
    ssim_threshold: float = 0.85,
) -> Dict[str, Dict]:
    """
    Compute the optimal parameter combination per compression level.

    Algorithm per level:
    1. Filter results where ``ssim_avg >= ssim_threshold``
    2. From filtered results, pick the one with highest ``saving_pct``
       → ``constraint_met: True``
    3. If no results pass the filter, pick the one with highest ``ssim_avg``
       → ``constraint_met: False``, fill ``warning`` field

    Parameters
    ----------
    results        : list of TuningResult dicts (from run_tuning)
    ssim_threshold : minimum acceptable SSIM (default 0.85)

    Returns
    -------
    dict keyed by level ("HIGH", "MEDIUM", "LOW"), each value::

        {
            "params": {
                "pdf_setting": str,
                "color_dpi": int,
                "gray_dpi": int,
                "mono_dpi": int,
                "jpeg_quality": int | None,
                "pikepdf_optimize": bool,
            },
            "saving_pct":     float,
            "ssim_avg":       float,
            "psnr_avg":       float | None,
            "constraint_met": bool,
            "warning":        str | None,
        }
    """
    # Group results by level
    by_level: Dict[str, List[Dict]] = {}
    for row in results:
        lvl = row.get("level", "UNKNOWN")
        by_level.setdefault(lvl, []).append(row)

    sweet_spots: Dict[str, Dict] = {}

    for level, level_results in by_level.items():
        if not level_results:
            continue

        # Filter: only rows with ssim_avg available and >= threshold
        passing = [
            r for r in level_results
            if r.get("ssim_avg") is not None and r["ssim_avg"] >= ssim_threshold
        ]

        if passing:
            # Pick highest saving_pct among passing
            best = max(passing, key=lambda r: r.get("saving_pct") or 0.0)
            constraint_met = True
            warning = None
        else:
            # Fallback: pick highest ssim_avg among all results with ssim_avg available
            with_ssim = [r for r in level_results if r.get("ssim_avg") is not None]
            if with_ssim:
                best = max(with_ssim, key=lambda r: r["ssim_avg"])
            else:
                # No ssim data at all — pick highest saving_pct
                best = max(level_results, key=lambda r: r.get("saving_pct") or 0.0)
            constraint_met = False
            warning = (
                f"No combination met ssim_avg >= {ssim_threshold} for level {level}. "
                f"Showing best available (ssim_avg = {best.get('ssim_avg')})."
            )

        sweet_spots[level] = {
            "params": {
                "pdf_setting":      best.get("pdf_setting"),
                "color_dpi":        best.get("color_dpi"),
                "gray_dpi":         best.get("gray_dpi"),
                "mono_dpi":         best.get("mono_dpi"),
                "jpeg_quality":     best.get("jpeg_quality"),
                "pikepdf_optimize": best.get("pikepdf_optimize"),
            },
            "saving_pct":     best.get("saving_pct"),
            "ssim_avg":       best.get("ssim_avg"),
            "psnr_avg":       best.get("psnr_avg"),
            "constraint_met": constraint_met,
            "warning":        warning,
        }

    return sweet_spots


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

# Canonical column order for TuningResult CSV
_TUNING_RESULT_FIELDS = [
    "filename",
    "level",
    "param_label",
    "pdf_setting",
    "color_dpi",
    "gray_dpi",
    "mono_dpi",
    "jpeg_quality",
    "pikepdf_optimize",
    "original_size_bytes",
    "compressed_size_bytes",
    "saving_pct",
    "ratio",
    "time_ms",
    "ssim_avg",
    "ssim_min",
    "psnr_avg",
    "psnr_min",
    "text_preserved_pct",
    "text_sequence_ratio",
    "pages_match",
    "error",
]


def results_to_csv(results: List[Dict]) -> str:
    """
    Serialize a list of TuningResult dicts to a CSV string.

    Uses the canonical TuningResult field order. If a result dict contains
    extra keys, they are appended after the canonical columns.

    Parameters
    ----------
    results : list of TuningResult dicts (from run_tuning)

    Returns
    -------
    CSV string, or empty string if results is empty.
    """
    if not results:
        return ""

    # Build fieldnames: canonical order first, then any extra keys
    extra_keys = []
    for row in results:
        for k in row.keys():
            if k not in _TUNING_RESULT_FIELDS and k not in extra_keys:
                extra_keys.append(k)

    fieldnames = _TUNING_RESULT_FIELDS + extra_keys

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in results:
        # Fill missing canonical fields with None
        complete_row = {f: row.get(f) for f in fieldnames}
        writer.writerow(complete_row)

    return buf.getvalue()
