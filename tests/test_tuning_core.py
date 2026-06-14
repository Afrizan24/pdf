# Feature: batch-parameter-tuning
# Tests for core/tuning.py property-based tests (Properties 1, 3, 4, 5, 6, 7)

import csv
import io
import os
import tempfile
from itertools import product
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.strategies import composite

from core.tuning import (
    _make_range,
    _normalize_pikepdf,
    compute_sweet_spot,
    generate_grid_from_config,
    results_to_csv,
    run_tuning,
)

# ---------------------------------------------------------------------------
# Composite strategies
# ---------------------------------------------------------------------------

PDF_SETTINGS_POOL = ["/screen", "/ebook", "/printer", "/prepress"]


@composite
def st_param_range(draw, lo=1, hi=300):
    """Generate a valid {min, max, step} dict where min <= max and step >= 1."""
    min_val = draw(st.integers(min_value=lo, max_value=hi - 1))
    max_val = draw(st.integers(min_value=min_val, max_value=hi))
    step = draw(st.integers(min_value=1, max_value=max(1, (max_val - min_val) // 2 + 1)))
    return {"min": min_val, "max": max_val, "step": step}


@composite
def st_level_config(draw):
    """Generate a single enabled level config."""
    return {
        "enabled": True,
        "color_dpi": draw(st_param_range(lo=36, hi=300)),
        "gray_dpi": draw(st_param_range(lo=36, hi=300)),
        "mono_dpi": draw(st_param_range(lo=72, hi=600)),
        "jpeg_quality": draw(st_param_range(lo=1, hi=100)),
        "pdf_settings": draw(
            st.lists(
                st.sampled_from(PDF_SETTINGS_POOL),
                min_size=1,
                max_size=4,
                unique=True,
            )
        ),
        "pikepdf_optimize": draw(
            st.one_of(
                st.booleans(),
                st.just([True, False]),
                st.just([True]),
                st.just([False]),
            )
        ),
    }


@composite
def st_tuning_config(draw):
    """Generate a config with 1-3 enabled levels."""
    levels = draw(
        st.lists(
            st.sampled_from(["HIGH", "MEDIUM", "LOW"]),
            min_size=1,
            max_size=3,
            unique=True,
        )
    )
    config = {}
    for level in ["HIGH", "MEDIUM", "LOW"]:
        if level in levels:
            config[level] = draw(st_level_config())
        else:
            config[level] = {"enabled": False}
    return config


@composite
def st_tuning_result(draw, level="HIGH"):
    """Generate a single TuningResult-like dict with random values."""
    ssim_avg = draw(st.one_of(st.none(), st.floats(min_value=0.0, max_value=1.0, allow_nan=False)))
    saving_pct = draw(st.one_of(st.none(), st.floats(min_value=0.0, max_value=99.9, allow_nan=False)))
    psnr_avg = draw(st.one_of(st.none(), st.floats(min_value=0.0, max_value=60.0, allow_nan=False)))
    return {
        "filename": draw(st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="_-."))) + ".pdf",
        "level": level,
        "param_label": draw(st.text(min_size=1, max_size=30, alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd"), whitelist_characters="_-"))),
        "pdf_setting": draw(st.sampled_from(PDF_SETTINGS_POOL)),
        "color_dpi": draw(st.integers(min_value=36, max_value=300)),
        "gray_dpi": draw(st.integers(min_value=36, max_value=300)),
        "mono_dpi": draw(st.integers(min_value=72, max_value=600)),
        "jpeg_quality": draw(st.integers(min_value=1, max_value=100)),
        "pikepdf_optimize": draw(st.booleans()),
        "original_size_bytes": draw(st.integers(min_value=1000, max_value=10_000_000)),
        "compressed_size_bytes": draw(st.one_of(st.none(), st.integers(min_value=100, max_value=9_000_000))),
        "saving_pct": round(saving_pct, 2) if saving_pct is not None else None,
        "ratio": draw(st.one_of(st.none(), st.floats(min_value=0.01, max_value=1.0, allow_nan=False))),
        "time_ms": draw(st.one_of(st.none(), st.floats(min_value=0.0, max_value=60000.0, allow_nan=False))),
        "ssim_avg": round(ssim_avg, 4) if ssim_avg is not None else None,
        "ssim_min": draw(st.one_of(st.none(), st.floats(min_value=0.0, max_value=1.0, allow_nan=False))),
        "psnr_avg": round(psnr_avg, 2) if psnr_avg is not None else None,
        "psnr_min": draw(st.one_of(st.none(), st.floats(min_value=0.0, max_value=60.0, allow_nan=False))),
        "text_preserved_pct": draw(st.one_of(st.none(), st.floats(min_value=0.0, max_value=100.0, allow_nan=False))),
        "text_sequence_ratio": draw(st.one_of(st.none(), st.floats(min_value=0.0, max_value=1.0, allow_nan=False))),
        "pages_match": draw(st.one_of(st.none(), st.booleans())),
        "error": draw(st.one_of(st.none(), st.text(min_size=1, max_size=50))),
    }


# ---------------------------------------------------------------------------
# Property 1: Grid Generation Completeness (Task 1.2)
# Feature: batch-parameter-tuning, Property 1: Grid Generation Completeness
# Validates: Requirements 4.1
# ---------------------------------------------------------------------------

@given(config=st_tuning_config())
@settings(max_examples=100)
def test_property1_grid_generation_completeness(config):
    """
    For any valid tuning config, len(grid) must equal the Cartesian product
    of all parameter value counts across all enabled levels.
    """
    grid = generate_grid_from_config(config)

    expected_count = 0
    for level in ("HIGH", "MEDIUM", "LOW"):
        level_cfg = config.get(level, {})
        if not level_cfg.get("enabled", False):
            continue

        color_dpi_vals = _make_range(level_cfg["color_dpi"])
        gray_dpi_vals = _make_range(level_cfg["gray_dpi"])
        mono_dpi_vals = _make_range(level_cfg["mono_dpi"])
        jpeg_quality_vals = _make_range(level_cfg["jpeg_quality"])
        pdf_settings = list(level_cfg.get("pdf_settings", ["/screen"]))
        pikepdf_vals = _normalize_pikepdf(level_cfg.get("pikepdf_optimize", True))

        level_count = (
            len(color_dpi_vals)
            * len(gray_dpi_vals)
            * len(mono_dpi_vals)
            * len(jpeg_quality_vals)
            * len(pdf_settings)
            * len(pikepdf_vals)
        )
        expected_count += level_count

    assert len(grid) == expected_count, (
        f"Expected {expected_count} combinations, got {len(grid)}"
    )


# ---------------------------------------------------------------------------
# Property 4: Error Isolation (Task 1.4)
# Feature: batch-parameter-tuning, Property 4: Error Isolation
# Validates: Requirements 4.5
# ---------------------------------------------------------------------------

@composite
def st_error_isolation_inputs(draw):
    """
    Generate a simple config with 1 enabled level and 2-4 combinations,
    plus a set of combination indices that should raise exceptions.
    """
    # Build a config that produces exactly 2-4 combinations
    n_pdf_settings = draw(st.integers(min_value=1, max_value=2))
    pdf_settings = draw(
        st.lists(
            st.sampled_from(PDF_SETTINGS_POOL),
            min_size=n_pdf_settings,
            max_size=n_pdf_settings,
            unique=True,
        )
    )
    pikepdf_val = draw(st.one_of(st.booleans(), st.just([True, False])))

    config = {
        "HIGH": {
            "enabled": True,
            "color_dpi": {"min": 72, "max": 72, "step": 1},
            "gray_dpi": {"min": 72, "max": 72, "step": 1},
            "mono_dpi": {"min": 144, "max": 144, "step": 1},
            "jpeg_quality": {"min": 60, "max": 60, "step": 1},
            "pdf_settings": pdf_settings,
            "pikepdf_optimize": pikepdf_val,
        },
        "MEDIUM": {"enabled": False},
        "LOW": {"enabled": False},
    }

    grid = generate_grid_from_config(config)
    n_combos = len(grid)

    # Pick a random subset of combo indices to fail (at least 1, not all)
    if n_combos > 1:
        n_fail = draw(st.integers(min_value=1, max_value=n_combos - 1))
    else:
        n_fail = draw(st.integers(min_value=0, max_value=1))

    fail_indices = draw(
        st.lists(
            st.integers(min_value=0, max_value=n_combos - 1),
            min_size=n_fail,
            max_size=n_fail,
            unique=True,
        )
    )

    return config, set(fail_indices)


@given(inputs=st_error_isolation_inputs())
@settings(max_examples=100)
def test_property4_error_isolation(inputs):
    """
    For any combination that raises an exception during compress(),
    the result row must have a non-empty 'error' field, and the sweep
    must continue past the error (all combinations are present).
    """
    config, fail_indices = inputs

    grid = generate_grid_from_config(config)
    n_combos = len(grid)

    call_counter = {"count": 0}

    def mock_compress(**kwargs):
        idx = call_counter["count"]
        call_counter["count"] += 1
        if idx in fail_indices:
            raise RuntimeError(f"Simulated compress failure at index {idx}")
        return (b"fake_pdf_bytes", {
            "saving_pct": 30.0,
            "ratio": 0.7,
            "time_ms": 100.0,
            "gs_used": True,
            "jpeg_quality_used": 60,
        })

    mock_eval = MagicMock()
    mock_eval.ssim_avg = 0.9
    mock_eval.ssim_min = 0.85
    mock_eval.psnr_avg = 35.0
    mock_eval.psnr_min = 30.0
    mock_eval.text_preserved_pct = 100.0
    mock_eval.text_sequence_ratio = 1.0
    mock_eval.pages_match = True

    # Create a temporary fake PDF file (compress is mocked so content doesn't matter)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(b"fake")
        tmp_path = tmp.name

    try:
        with patch("core.tuning.compress", side_effect=mock_compress), \
             patch("core.tuning.evaluate", return_value=mock_eval), \
             patch("core.tuning.extract_features", return_value={}), \
             patch("core.tuning.classify_pdf_with_confidence", return_value=("HYBRID", 0.9)), \
             patch("os.path.getsize", return_value=100000):

            results = run_tuning([tmp_path], config, evaluate_quality=True)

    finally:
        os.unlink(tmp_path)

    # Total results == 1 file × n_combos
    assert len(results) == n_combos, (
        f"Expected {n_combos} results, got {len(results)}"
    )

    # Rows where compress raised exception must have non-empty error
    for i, row in enumerate(results):
        if i in fail_indices:
            assert row["error"] is not None and row["error"] != "", (
                f"Row {i} should have error but got: {row['error']!r}"
            )
        else:
            # Rows where compress succeeded should have no compress error
            # (may have quality_eval error, but saving_pct should be set)
            assert row["saving_pct"] is not None, (
                f"Row {i} should have saving_pct set but got None"
            )


# ---------------------------------------------------------------------------
# Property 3: Result Completeness (Task 1.5)
# Feature: batch-parameter-tuning, Property 3: Result Completeness
# Validates: Requirements 4.3
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = [
    "filename", "level", "param_label", "pdf_setting",
    "color_dpi", "gray_dpi", "mono_dpi", "jpeg_quality", "pikepdf_optimize",
    "saving_pct", "ssim_avg", "psnr_avg", "time_ms", "error",
]


@composite
def st_result_completeness_inputs(draw):
    """Generate a simple single-level config and random compress/evaluate return values."""
    saving_pct = draw(st.floats(min_value=0.0, max_value=99.0, allow_nan=False))
    ssim_avg = draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False))
    psnr_avg = draw(st.floats(min_value=0.0, max_value=60.0, allow_nan=False))
    time_ms = draw(st.floats(min_value=0.0, max_value=60000.0, allow_nan=False))

    config = {
        "HIGH": {
            "enabled": True,
            "color_dpi": {"min": 72, "max": 72, "step": 1},
            "gray_dpi": {"min": 72, "max": 72, "step": 1},
            "mono_dpi": {"min": 144, "max": 144, "step": 1},
            "jpeg_quality": {"min": 60, "max": 60, "step": 1},
            "pdf_settings": ["/screen"],
            "pikepdf_optimize": True,
        },
        "MEDIUM": {"enabled": False},
        "LOW": {"enabled": False},
    }

    return config, saving_pct, ssim_avg, psnr_avg, time_ms


@given(inputs=st_result_completeness_inputs())
@settings(max_examples=100)
def test_property3_result_completeness(inputs):
    """
    For any combination that runs successfully, every result row must contain
    all required fields.
    """
    config, saving_pct, ssim_avg, psnr_avg, time_ms = inputs

    def mock_compress(**kwargs):
        return (b"fake_pdf_bytes", {
            "saving_pct": saving_pct,
            "ratio": 0.5,
            "time_ms": time_ms,
            "gs_used": True,
            "jpeg_quality_used": 60,
        })

    mock_eval = MagicMock()
    mock_eval.ssim_avg = ssim_avg
    mock_eval.ssim_min = ssim_avg * 0.9
    mock_eval.psnr_avg = psnr_avg
    mock_eval.psnr_min = psnr_avg * 0.9
    mock_eval.text_preserved_pct = 100.0
    mock_eval.text_sequence_ratio = 1.0
    mock_eval.pages_match = True

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(b"fake")
        tmp_path = tmp.name

    try:
        with patch("core.tuning.compress", side_effect=mock_compress), \
             patch("core.tuning.evaluate", return_value=mock_eval), \
             patch("core.tuning.extract_features", return_value={}), \
             patch("core.tuning.classify_pdf_with_confidence", return_value=("HYBRID", 0.9)), \
             patch("os.path.getsize", return_value=100000):

            results = run_tuning([tmp_path], config, evaluate_quality=True)

    finally:
        os.unlink(tmp_path)

    assert len(results) == 1, f"Expected 1 result, got {len(results)}"

    row = results[0]
    for field in REQUIRED_FIELDS:
        assert field in row, f"Required field '{field}' missing from result row"


# ---------------------------------------------------------------------------
# Property 5: Sweet Spot Optimality — Constraint Met (Task 1.7)
# Feature: batch-parameter-tuning, Property 5: Sweet Spot Optimality (Constraint Met)
# Validates: Requirements 7.2
# ---------------------------------------------------------------------------

@composite
def st_results_with_passing(draw):
    """
    Generate a list of result dicts for a single level where at least one
    has ssim_avg >= 0.85.
    """
    # Generate N results, at least one with ssim_avg >= 0.85
    n = draw(st.integers(min_value=1, max_value=20))
    results = []

    for i in range(n):
        ssim_avg = draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False))
        saving_pct = draw(st.floats(min_value=0.0, max_value=99.0, allow_nan=False))
        results.append({
            "filename": "test.pdf",
            "level": "HIGH",
            "param_label": f"HIGH_screen_cdpi72_jq60_pike1_{i}",
            "pdf_setting": "/screen",
            "color_dpi": 72,
            "gray_dpi": 72,
            "mono_dpi": 144,
            "jpeg_quality": 60,
            "pikepdf_optimize": True,
            "saving_pct": round(saving_pct, 2),
            "ssim_avg": round(ssim_avg, 4),
            "psnr_avg": 35.0,
            "error": None,
        })

    # Ensure at least one result has ssim_avg >= 0.85
    forced_ssim = draw(st.floats(min_value=0.85, max_value=1.0, allow_nan=False))
    forced_saving = draw(st.floats(min_value=0.0, max_value=99.0, allow_nan=False))
    results.append({
        "filename": "test.pdf",
        "level": "HIGH",
        "param_label": "HIGH_screen_cdpi72_jq60_pike1_forced",
        "pdf_setting": "/screen",
        "color_dpi": 72,
        "gray_dpi": 72,
        "mono_dpi": 144,
        "jpeg_quality": 60,
        "pikepdf_optimize": True,
        "saving_pct": round(forced_saving, 2),
        "ssim_avg": round(forced_ssim, 4),
        "psnr_avg": 35.0,
        "error": None,
    })

    return results


@given(results=st_results_with_passing())
@settings(max_examples=100)
def test_property5_sweet_spot_optimality_constraint_met(results):
    """
    When at least one result has ssim_avg >= 0.85, the sweet spot must have
    the maximum saving_pct among all results with ssim_avg >= 0.85.
    """
    sweet_spots = compute_sweet_spot(results, ssim_threshold=0.85)

    assert "HIGH" in sweet_spots, "Expected 'HIGH' level in sweet spots"

    spot = sweet_spots["HIGH"]
    assert spot["constraint_met"] is True, (
        "constraint_met should be True when at least one result passes the threshold"
    )

    # Compute expected best saving_pct among passing results
    passing = [r for r in results if r.get("ssim_avg") is not None and r["ssim_avg"] >= 0.85]
    assert len(passing) > 0, "Test setup error: no passing results"

    expected_best_saving = max(r.get("saving_pct") or 0.0 for r in passing)
    assert spot["saving_pct"] == expected_best_saving, (
        f"Sweet spot saving_pct {spot['saving_pct']} != expected {expected_best_saving}"
    )


# ---------------------------------------------------------------------------
# Property 6: Sweet Spot Fallback — Constraint Not Met (Task 1.8)
# Feature: batch-parameter-tuning, Property 6: Sweet Spot Fallback (Constraint Not Met)
# Validates: Requirements 7.4
# ---------------------------------------------------------------------------

@composite
def st_results_all_failing(draw):
    """
    Generate a list of result dicts for a single level where ALL have ssim_avg < 0.85.
    """
    n = draw(st.integers(min_value=1, max_value=20))
    results = []

    for i in range(n):
        # ssim_avg strictly below threshold
        ssim_avg = draw(st.floats(min_value=0.0, max_value=0.8499, allow_nan=False))
        saving_pct = draw(st.floats(min_value=0.0, max_value=99.0, allow_nan=False))
        results.append({
            "filename": "test.pdf",
            "level": "HIGH",
            "param_label": f"HIGH_screen_cdpi72_jq60_pike1_{i}",
            "pdf_setting": "/screen",
            "color_dpi": 72,
            "gray_dpi": 72,
            "mono_dpi": 144,
            "jpeg_quality": 60,
            "pikepdf_optimize": True,
            "saving_pct": round(saving_pct, 2),
            "ssim_avg": round(ssim_avg, 4),
            "psnr_avg": 35.0,
            "error": None,
        })

    return results


@given(results=st_results_all_failing())
@settings(max_examples=100)
def test_property6_sweet_spot_fallback_constraint_not_met(results):
    """
    When all results have ssim_avg < 0.85, the sweet spot must have the
    maximum ssim_avg among all results, and constraint_met must be False.
    """
    sweet_spots = compute_sweet_spot(results, ssim_threshold=0.85)

    assert "HIGH" in sweet_spots, "Expected 'HIGH' level in sweet spots"

    spot = sweet_spots["HIGH"]
    assert spot["constraint_met"] is False, (
        "constraint_met should be False when no result passes the threshold"
    )

    # Compute expected best ssim_avg
    with_ssim = [r for r in results if r.get("ssim_avg") is not None]
    assert len(with_ssim) > 0, "Test setup error: no results with ssim_avg"

    expected_best_ssim = max(r["ssim_avg"] for r in with_ssim)
    assert spot["ssim_avg"] == expected_best_ssim, (
        f"Sweet spot ssim_avg {spot['ssim_avg']} != expected {expected_best_ssim}"
    )


# ---------------------------------------------------------------------------
# Property 7: CSV Round-Trip (Task 1.10)
# Feature: batch-parameter-tuning, Property 7: CSV Round-Trip
# Validates: Requirements 9.1
# ---------------------------------------------------------------------------

@composite
def st_csv_result_list(draw):
    """Generate a non-empty list of TuningResult-like dicts for CSV round-trip testing."""
    n = draw(st.integers(min_value=1, max_value=10))
    level = draw(st.sampled_from(["HIGH", "MEDIUM", "LOW"]))
    return [draw(st_tuning_result(level=level)) for _ in range(n)]


@given(results=st_csv_result_list())
@settings(max_examples=100)
def test_property7_csv_round_trip(results):
    """
    Serializing results to CSV and parsing back must yield equivalent data
    for all string-representable fields.
    """
    csv_str = results_to_csv(results)
    assert csv_str != "", "results_to_csv should not return empty string for non-empty results"

    reader = csv.DictReader(io.StringIO(csv_str))
    parsed_rows = list(reader)

    assert len(parsed_rows) == len(results), (
        f"CSV has {len(parsed_rows)} rows, expected {len(results)}"
    )

    # Fields to check in round-trip (all canonical fields that are string-representable)
    check_fields = [
        "filename", "level", "param_label", "pdf_setting",
        "color_dpi", "gray_dpi", "mono_dpi", "jpeg_quality",
        "pikepdf_optimize",
    ]

    for i, (original, parsed) in enumerate(zip(results, parsed_rows)):
        for field in check_fields:
            original_val = original.get(field)
            parsed_val = parsed.get(field, "")

            # CSV stores everything as strings; None becomes empty string
            if original_val is None:
                expected_str = ""
            else:
                expected_str = str(original_val)

            assert parsed_val == expected_str, (
                f"Row {i}, field '{field}': CSV has {parsed_val!r}, expected {expected_str!r}"
            )

        # Numeric fields: compare as strings (None → "")
        for num_field in ["saving_pct", "ssim_avg", "psnr_avg", "time_ms"]:
            original_val = original.get(num_field)
            parsed_val = parsed.get(num_field, "")
            if original_val is None:
                assert parsed_val == "", (
                    f"Row {i}, field '{num_field}': expected empty string for None, got {parsed_val!r}"
                )
            else:
                # The value was written as str(original_val); it should round-trip
                assert parsed_val == str(original_val), (
                    f"Row {i}, field '{num_field}': CSV has {parsed_val!r}, expected {str(original_val)!r}"
                )

        # Error field: None → ""
        original_error = original.get("error")
        parsed_error = parsed.get("error", "")
        if original_error is None:
            assert parsed_error == "", (
                f"Row {i}, field 'error': expected empty string for None, got {parsed_error!r}"
            )
        else:
            assert parsed_error == str(original_error), (
                f"Row {i}, field 'error': CSV has {parsed_error!r}, expected {str(original_error)!r}"
            )
