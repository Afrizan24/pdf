"""
PDF compression — two-pass pipeline: Ghostscript then pikepdf structural optimize.

Classification (SCAN / DIGITAL / HYBRID) determines the default GS preset.
Three compression levels map to concrete parameter bundles for research:

  HIGH   — /screen,  72 dpi colour/gray,  144 dpi mono, aggressive
  MEDIUM — /ebook,  150 dpi colour/gray,  300 dpi mono, balanced
  LOW    — /printer, 300 dpi colour/gray,  600 dpi mono, conservative

All parameters can be overridden individually for fine-grained tuning.

Pass A  Ghostscript  — image downsample, font subset, stream recompress
Pass B  pikepdf      — ObjStm packing, metadata strip (applied to GS output)

Safety: if a pass produces a larger file than the previous best, it is discarded.
        If all passes exceed the original size, the original is returned unchanged.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from typing import Callable, Dict, Optional, Tuple

import pikepdf

from core.classifier import classify_pdf_with_confidence
from core.features import extract_features
from core.ghostscript import GS_EXECUTABLE, font_subsetting_gs
from core.jbig2 import JBIG2_EXECUTABLE

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

ProgressCb = Optional[Callable[[str, int, str], None]]

# ---------------------------------------------------------------------------
# Compression level presets
# Maps level name -> (pdf_setting, color_dpi, gray_dpi, mono_dpi)
# ---------------------------------------------------------------------------

COMPRESSION_LEVELS: Dict[str, Dict] = {
    "HIGH": {
        "pdf_setting": "/screen",
        "color_dpi":   72,
        "gray_dpi":    72,
        "mono_dpi":    144,
        "label":       "High — /screen 72 dpi",
    },
    "MEDIUM": {
        "pdf_setting": "/ebook",
        "color_dpi":   150,
        "gray_dpi":    150,
        "mono_dpi":    300,
        "label":       "Medium — /ebook 150 dpi",
    },
    "LOW": {
        "pdf_setting": "/printer",
        "color_dpi":   300,
        "gray_dpi":    300,
        "mono_dpi":    600,
        "label":       "Low — /printer 300 dpi",
    },
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _emit(cb: ProgressCb, step: str, pct: int, detail: str = "") -> None:
    if cb:
        cb(step, pct, detail)


def _fmt(b: int) -> str:
    if b < 1024:      return f"{b} B"
    if b < 1_048_576: return f"{b / 1024:.1f} KB"
    return f"{b / 1_048_576:.2f} MB"


def _size(path: str) -> int:
    return os.path.getsize(path) if os.path.exists(path) else 0


def _tmp(d: str, name: str) -> str:
    return os.path.join(d, name)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def compute_metrics(before: int, after: int, time_ms: float) -> Dict:
    ratio = after / before if before > 0 else 0.0
    return {
        "before_bytes":    float(before),
        "after_bytes":     float(after),
        "ratio":           ratio,
        "saving_pct":      (1.0 - ratio) * 100.0,
        "time_ms":         time_ms,
        "throughput_mb_s": (before / 1_048_576) / (time_ms / 1000.0) if time_ms > 0 else 0.0,
    }


# ---------------------------------------------------------------------------
# Ghostscript compression pass
# ---------------------------------------------------------------------------

def gs_compress(
    in_path: str,
    out_path: str,
    pdf_setting: str = "/ebook",
    grayscale: bool = False,
    color_dpi: Optional[int] = None,
    gray_dpi: Optional[int] = None,
    mono_dpi: Optional[int] = None,
    progress_cb: ProgressCb = None,
) -> Dict:
    """Run Ghostscript compression and return timing info."""
    c = color_dpi or 150
    g = gray_dpi  or 150
    m = mono_dpi  or 300
    _emit(progress_cb, "ghostscript", 10,
          f"Ghostscript {pdf_setting} | color {c} dpi | gray {g} dpi | mono {m} dpi"
          + (" | grayscale" if grayscale else "") + "...")
    result = font_subsetting_gs(
        in_path, out_path,
        pdf_setting=pdf_setting,
        grayscale=grayscale,
        color_dpi=color_dpi,
        gray_dpi=gray_dpi,
        mono_dpi=mono_dpi,
    )
    _emit(progress_cb, "ghostscript", 100, f"Done ({result['time_ms']:.0f} ms)")
    return result


# ---------------------------------------------------------------------------
# pikepdf structural optimisation pass
# ---------------------------------------------------------------------------

def pikepdf_structural_optimize(
    in_path: str,
    out_path: str,
    progress_cb: ProgressCb = None,
) -> Dict:
    """
    Structural PDF optimisation via pikepdf, applied to GS output.

    GS compresses streams and subsets fonts but never emits ObjStm entries
    and leaves metadata intact.  This pass adds:
      - Object stream packing (ObjStm)  — saves 5-15% on xref overhead
      - Metadata strip                  — removes XMP/Info bloat GS leaves behind
      - recompress_flate on small files — re-deflate any streams GS left uncompressed

    Deliberately does NOT re-encode images — GS already handled that.
    """
    t0     = time.perf_counter()
    in_mb  = os.path.getsize(in_path) / 1_048_576
    _emit(progress_cb, "pikepdf", 10, f"pikepdf structural optimize ({in_mb:.1f} MB)...")

    with pikepdf.open(in_path, suppress_warnings=True) as pdf:
        # Strip metadata bloat GS leaves behind
        with pdf.open_metadata() as meta:
            keep = {"dc:title", "dc:creator"}
            for k in [k for k in meta if k not in keep]:
                try:    del meta[k]
                except: pass

        pdf.save(
            out_path,
            compress_streams=True,
            object_stream_mode=pikepdf.ObjectStreamMode.generate,
            # recompress_flate only worth it on small files; GS already deflated
            # streams on larger ones so the gain is negligible vs the CPU cost.
            recompress_flate=in_mb <= 20.0,
            linearize=False,
        )

    elapsed = (time.perf_counter() - t0) * 1000.0
    _emit(progress_cb, "pikepdf", 100, f"Done ({elapsed:.0f} ms)")
    return {"time_ms": elapsed}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def compress(
    in_path: str,
    mode: str = "AUTO",
    level: str = "MEDIUM",
    pdf_setting: Optional[str] = None,
    color_dpi: Optional[int] = None,
    gray_dpi: Optional[int] = None,
    mono_dpi: Optional[int] = None,
    jpeg_quality: Optional[int] = None,
    grayscale: bool = False,
    pikepdf_optimize: bool = True,
    scan_text_threshold: int = 20,
    digital_text_threshold: int = 200,
    min_images_for_scan: float = 1.0,
    max_size_for_gs_mb: float = 200.0,
    progress_cb: ProgressCb = None,
    # kept for API compatibility
    dpi: int = 150,
    garbage: int = 4,
    deflate: bool = True,
    clean: bool = True,
) -> Tuple[bytes, Dict]:
    """
    Compress a PDF using Ghostscript + optional pikepdf structural optimize.

    level        : 'HIGH' | 'MEDIUM' | 'LOW' — preset parameter bundle
    pdf_setting  : override GS preset (e.g. '/screen', '/ebook', '/printer', '/prepress')
    color_dpi    : override colour image downsample DPI
    gray_dpi     : override grayscale image downsample DPI
    mono_dpi     : override monochrome image downsample DPI
    grayscale    : convert all colour to grayscale
    pikepdf_optimize : run pikepdf ObjStm + metadata strip on GS output

    When an override is None, the value from the selected level preset is used.
    """
    if not os.path.exists(in_path):
        raise FileNotFoundError(f"File not found: {in_path}")
    if GS_EXECUTABLE is None:
        raise RuntimeError("Ghostscript not found. Install Ghostscript and ensure it is on PATH.")

    # ── Feature extraction ────────────────────────────────────────────────
    _emit(progress_cb, "init", 0, "Extracting PDF features...")
    before = os.path.getsize(in_path)
    feats  = extract_features(in_path)

    detected, classification_confidence = classify_pdf_with_confidence(
        feats,
        text_scan_threshold=scan_text_threshold,
        text_digital_threshold=digital_text_threshold,
        min_images_for_scan=min_images_for_scan,
    )
    used_mode = detected if mode == "AUTO" else mode.upper()
    if used_mode not in ("SCAN", "DIGITAL", "HYBRID"):
        raise ValueError(f"Unknown mode: {used_mode}")

    # ── Resolve parameters from level preset + overrides ─────────────────
    lvl_key = level.upper() if level.upper() in COMPRESSION_LEVELS else "MEDIUM"
    preset  = COMPRESSION_LEVELS[lvl_key]

    eff_setting      = pdf_setting    if pdf_setting    else preset["pdf_setting"]
    eff_color_dpi    = color_dpi      if color_dpi      else preset["color_dpi"]
    eff_gray_dpi     = gray_dpi       if gray_dpi       else preset["gray_dpi"]
    eff_mono_dpi     = mono_dpi       if mono_dpi       else preset["mono_dpi"]
    # jpeg_quality: user override → auto from DPI (handled inside font_subsetting_gs)
    eff_jpeg_quality = jpeg_quality   # None = auto-select inside GS function

    if classification_confidence == "weak":
        _emit(progress_cb, "classification_weak", 0,
              f"Classification '{detected}' is weak — consider setting mode manually.")

    _emit(progress_cb, "init", 100,
          f"Class: {detected} ({classification_confidence}) | Mode: {used_mode} | "
          f"Level: {lvl_key} | {eff_setting} | "
          f"color {eff_color_dpi} dpi | gray {eff_gray_dpi} dpi | mono {eff_mono_dpi} dpi | "
          f"JPEG q={eff_jpeg_quality if eff_jpeg_quality else 'auto'} | "
          f"{_fmt(before)}")

    # ── Two-pass pipeline ─────────────────────────────────────────────────
    tmp_dir   = tempfile.mkdtemp(prefix="pdfcomp_")
    gs_path   = _tmp(tmp_dir, "gs.pdf")
    pike_path = _tmp(tmp_dir, "pike.pdf")
    time_ms   = 0.0
    gs_used   = False
    jpeg_quality_used = None

    try:
        if before > max_size_for_gs_mb * 1_048_576:
            raise RuntimeError(
                f"File size {_fmt(before)} exceeds limit {max_size_for_gs_mb} MB. "
                f"Increase max_size_for_gs_mb to process this file."
            )

        # ── Pass A: Ghostscript ───────────────────────────────────────────
        _emit(progress_cb, "pass_start", 0,
              f"Pass A — Ghostscript {eff_setting} "
              f"(color {eff_color_dpi} | gray {eff_gray_dpi} | mono {eff_mono_dpi} dpi, "
              f"JPEG q={eff_jpeg_quality if eff_jpeg_quality else 'auto'})...")
        gs_result = font_subsetting_gs(
            in_path, gs_path,
            pdf_setting=eff_setting,
            grayscale=grayscale,
            color_dpi=eff_color_dpi,
            gray_dpi=eff_gray_dpi,
            mono_dpi=eff_mono_dpi,
            jpeg_quality=eff_jpeg_quality,
            is_scan=(used_mode == "SCAN"),
        )
        time_ms += gs_result.get("time_ms", 0.0)
        gs_size  = _size(gs_path)
        jpeg_quality_used = gs_result.get("jpeg_quality_used")

        if gs_size == 0:
            raise RuntimeError("Ghostscript produced an empty output file.")

        _emit(progress_cb, "pass_done", 50,
              f"Pass A done — {_fmt(gs_size)} ({gs_result['time_ms']:.0f} ms)")

        if gs_size < before:
            best_path = gs_path
            best_size = gs_size
            gs_used   = True
        else:
            _emit(progress_cb, "pass_skip", 50,
                  f"GS output ({_fmt(gs_size)}) >= original — skipping as base for Pass B")
            best_path = in_path
            best_size = before

        # ── Pass B: pikepdf structural optimize (on GS output) ───────────
        if gs_used and pikepdf_optimize:
            _emit(progress_cb, "pass_start", 55,
                  "Pass B — pikepdf ObjStm + metadata strip...")
            try:
                pike_result = pikepdf_structural_optimize(
                    gs_path, pike_path,
                    progress_cb=progress_cb,
                )
                time_ms  += pike_result.get("time_ms", 0.0)
                pike_size = _size(pike_path)

                if pike_size > 0 and pike_size < best_size:
                    best_path = pike_path
                    best_size = pike_size
                    saving    = round((1 - pike_size / before) * 100, 1)
                    _emit(progress_cb, "pass_done", 95,
                          f"Pass B done — {_fmt(pike_size)} "
                          f"(total saving {saving}%, {pike_result['time_ms']:.0f} ms)")
                else:
                    _emit(progress_cb, "pass_done", 95,
                          f"Pass B — no further gain ({_fmt(pike_size)}), keeping GS output")
            except Exception as exc:
                _emit(progress_cb, "pass_err", 95,
                      f"Pass B failed: {exc} — keeping GS output")
        elif not pikepdf_optimize:
            _emit(progress_cb, "pass_skip", 95, "Pass B — pikepdf disabled by user")

        # ── Safety fallback ───────────────────────────────────────────────
        if best_size >= before:
            _emit(progress_cb, "fallback", 100,
                  f"All passes >= original ({_fmt(before)}) — returning original unchanged")
            best_path = in_path
            best_size = before
            gs_used   = False
            time_ms   = 0.0
        else:
            saving = round((1 - best_size / before) * 100, 1)
            _emit(progress_cb, "select_best", 100,
                  f"Final: {_fmt(before)} → {_fmt(best_size)} (saved {saving}%)")

        with open(best_path, "rb") as f:
            pdf_bytes = f.read()

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    m = compute_metrics(before, best_size, time_ms)
    return pdf_bytes, {
        "pages":                     feats.pages,
        "file_size_bytes":           feats.file_size_bytes,
        "total_text_len":            feats.total_text_len,
        "total_images":              feats.total_images,
        "avg_text_len_per_page":     round(feats.avg_text_len_per_page, 2),
        "avg_images_per_page":       round(feats.avg_images_per_page, 2),
        "dominant_image_encoding":   feats.dominant_image_encoding,
        "bilevel_image_ratio":       round(feats.bilevel_image_ratio, 2),
        "avg_image_area_ratio":      feats.avg_image_area_ratio,
        "avg_text_area_ratio":       feats.avg_text_area_ratio,
        "detected_class":            detected,
        "classification_confidence": classification_confidence,
        "mode_used":                 used_mode,
        "level_used":                lvl_key,
        "pdf_setting_used":          eff_setting,
        "color_dpi_used":            eff_color_dpi,
        "gray_dpi_used":             eff_gray_dpi,
        "mono_dpi_used":             eff_mono_dpi,
        "grayscale":                 grayscale,
        "pikepdf_optimize":          pikepdf_optimize,
        "jpeg_quality_used":         jpeg_quality_used,
        "dpi_used":                  None,
        "text_loss_warning":         False,
        "before_bytes":              int(m["before_bytes"]),
        "after_bytes":               int(best_size),
        "ratio":                     round(m["ratio"], 4),
        "saving_pct":                round(m["saving_pct"], 2),
        "time_ms":                   round(m["time_ms"], 2),
        "throughput_mb_s":           round(m["throughput_mb_s"], 2),
        "gs_available":              True,
        "gs_used":                   gs_used,
        "gs_executable":             GS_EXECUTABLE,
        "jbig2_available":           JBIG2_EXECUTABLE is not None,
        "jbig2_executable":          JBIG2_EXECUTABLE,
    }
