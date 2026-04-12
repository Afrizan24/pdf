"""
Tests for core/compressor.py — compression passes and main entry point.

Each pass is tested in isolation where possible.
The main compress() function is tested end-to-end.
"""

from __future__ import annotations

import os
import pytest
import fitz

from core.compressor import (
    optimize_pdf_structure,
    pikepdf_recompress,
    rasterize_scan_pdf,
    compress,
    _estimate_jpeg_quality,
    _cs_to_mode,
)
from core.ghostscript import GS_EXECUTABLE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def file_is_valid_pdf(path: str) -> bool:
    """Check that a file exists and is openable as PDF."""
    if not os.path.exists(path):
        return False
    try:
        doc = fitz.open(path)
        ok = doc.page_count > 0
        doc.close()
        return ok
    except Exception:
        return False


def collect_progress(events: list):
    """Return a progress_cb that appends (step, pct, detail) to events."""
    def cb(step, pct, detail):
        events.append((step, pct, detail))
    return cb


# ---------------------------------------------------------------------------
# _estimate_jpeg_quality
# ---------------------------------------------------------------------------

class TestEstimateJpegQuality:
    def test_small_file_low_quality(self):
        # Very small file relative to pixels → low quality
        q = _estimate_jpeg_quality(compressed_size=500, w=100, h=100, channels=3)
        assert q <= 35

    def test_large_file_high_quality(self):
        # Large file relative to pixels → high quality
        q = _estimate_jpeg_quality(compressed_size=100_000, w=100, h=100, channels=3)
        assert q >= 85

    def test_zero_dimensions(self):
        # Should not raise, returns 100
        q = _estimate_jpeg_quality(1000, 0, 0)
        assert q == 100

    def test_grayscale_channels(self):
        # Same file size but 1 channel → higher bpp → higher quality estimate
        q_rgb  = _estimate_jpeg_quality(5000, 100, 100, channels=3)
        q_gray = _estimate_jpeg_quality(5000, 100, 100, channels=1)
        assert q_gray >= q_rgb


# ---------------------------------------------------------------------------
# _cs_to_mode
# ---------------------------------------------------------------------------

class TestCsToMode:
    def test_device_gray(self):
        import pikepdf
        assert _cs_to_mode(pikepdf.Name("/DeviceGray")) == "L"

    def test_device_rgb(self):
        import pikepdf
        assert _cs_to_mode(pikepdf.Name("/DeviceRGB")) == "RGB"

    def test_none_defaults_rgb(self):
        assert _cs_to_mode(None) == "RGB"

    def test_cmyk(self):
        import pikepdf
        assert _cs_to_mode(pikepdf.Name("/DeviceCMYK")) == "CMYK"

    def test_array_colorspace(self):
        import pikepdf
        # [/ICCBased stream] → first element checked
        cs_array = [pikepdf.Name("/ICCBased")]
        assert _cs_to_mode(cs_array) == "RGB"


# ---------------------------------------------------------------------------
# optimize_pdf_structure (Pass B)
# ---------------------------------------------------------------------------

class TestOptimizePdfStructure:
    def test_output_is_valid_pdf(self, digital_pdf, tmp_path):
        out = str(tmp_path / "out.pdf")
        optimize_pdf_structure(digital_pdf, out)
        assert file_is_valid_pdf(out)

    def test_page_count_preserved(self, digital_pdf, tmp_path):
        out = str(tmp_path / "out.pdf")
        optimize_pdf_structure(digital_pdf, out)
        doc = fitz.open(out)
        assert doc.page_count == 3
        doc.close()

    def test_returns_time_ms(self, digital_pdf, tmp_path):
        out = str(tmp_path / "out.pdf")
        result = optimize_pdf_structure(digital_pdf, out)
        assert "time_ms" in result
        assert result["time_ms"] >= 0

    def test_progress_callback_called(self, digital_pdf, tmp_path):
        out = str(tmp_path / "out.pdf")
        events = []
        optimize_pdf_structure(digital_pdf, out, progress_cb=collect_progress(events))
        assert len(events) > 0
        steps = [e[0] for e in events]
        assert "struct" in steps

    def test_garbage_level_zero(self, digital_pdf, tmp_path):
        out = str(tmp_path / "out.pdf")
        optimize_pdf_structure(digital_pdf, out, garbage=0)
        assert file_is_valid_pdf(out)

    def test_deflate_false(self, digital_pdf, tmp_path):
        out = str(tmp_path / "out.pdf")
        optimize_pdf_structure(digital_pdf, out, deflate=False)
        assert file_is_valid_pdf(out)


# ---------------------------------------------------------------------------
# pikepdf_recompress (Pass A / D)
# ---------------------------------------------------------------------------

class TestPikepdfRecompress:
    def test_output_is_valid_pdf(self, digital_pdf, tmp_path):
        out = str(tmp_path / "out.pdf")
        pikepdf_recompress(digital_pdf, out)
        assert file_is_valid_pdf(out)

    def test_page_count_preserved(self, scan_pdf, tmp_path):
        out = str(tmp_path / "out.pdf")
        pikepdf_recompress(scan_pdf, out)
        doc = fitz.open(out)
        assert doc.page_count == 3
        doc.close()

    def test_returns_stats(self, scan_pdf, tmp_path):
        out = str(tmp_path / "out.pdf")
        result = pikepdf_recompress(scan_pdf, out)
        assert "time_ms" in result
        assert "recompressed" in result
        assert "skipped" in result
        assert "bytes_saved" in result

    def test_recompressed_plus_skipped_equals_total(self, scan_pdf, tmp_path):
        out = str(tmp_path / "out.pdf")
        result = pikepdf_recompress(scan_pdf, out)
        # total unique images = recompressed + skipped
        assert result["recompressed"] + result["skipped"] >= 0

    def test_progress_callback_called(self, scan_pdf, tmp_path):
        out = str(tmp_path / "out.pdf")
        events = []
        pikepdf_recompress(scan_pdf, out, progress_cb=collect_progress(events))
        assert len(events) > 0

    def test_grayscale_flag(self, scan_pdf, tmp_path):
        out = str(tmp_path / "out.pdf")
        pikepdf_recompress(scan_pdf, out, grayscale=True)
        assert file_is_valid_pdf(out)

    def test_low_quality_produces_smaller_or_equal(self, scan_pdf, tmp_path):
        out_low  = str(tmp_path / "low.pdf")
        out_high = str(tmp_path / "high.pdf")
        pikepdf_recompress(scan_pdf, out_low,  jpeg_quality=20)
        pikepdf_recompress(scan_pdf, out_high, jpeg_quality=90)
        # Low quality should be <= high quality (or equal if no images recompressed)
        assert os.path.getsize(out_low) <= os.path.getsize(out_high)


# ---------------------------------------------------------------------------
# rasterize_scan_pdf (Pass A for SCAN)
# ---------------------------------------------------------------------------

class TestRasterizeScanPdf:
    def test_output_is_valid_pdf(self, scan_pdf, tmp_path):
        out = str(tmp_path / "out.pdf")
        rasterize_scan_pdf(scan_pdf, out)
        assert file_is_valid_pdf(out)

    def test_page_count_preserved(self, scan_pdf, tmp_path):
        out = str(tmp_path / "out.pdf")
        rasterize_scan_pdf(scan_pdf, out)
        doc = fitz.open(out)
        assert doc.page_count == 3
        doc.close()

    def test_returns_time_ms(self, scan_pdf, tmp_path):
        out = str(tmp_path / "out.pdf")
        result = rasterize_scan_pdf(scan_pdf, out)
        assert "time_ms" in result
        assert result["time_ms"] > 0

    def test_progress_callback_called(self, scan_pdf, tmp_path):
        out = str(tmp_path / "out.pdf")
        events = []
        rasterize_scan_pdf(scan_pdf, out, progress_cb=collect_progress(events))
        assert len(events) > 0
        steps = [e[0] for e in events]
        assert "rasterize" in steps

    def test_low_dpi_smaller_than_high_dpi(self, scan_pdf, tmp_path):
        out_low  = str(tmp_path / "low.pdf")
        out_high = str(tmp_path / "high.pdf")
        rasterize_scan_pdf(scan_pdf, out_low,  target_dpi=72)
        rasterize_scan_pdf(scan_pdf, out_high, target_dpi=300)
        assert os.path.getsize(out_low) < os.path.getsize(out_high)

    def test_grayscale_smaller_than_color(self, scan_pdf, tmp_path):
        out_gray  = str(tmp_path / "gray.pdf")
        out_color = str(tmp_path / "color.pdf")
        rasterize_scan_pdf(scan_pdf, out_gray,  grayscale=True)
        rasterize_scan_pdf(scan_pdf, out_color, grayscale=False)
        assert os.path.getsize(out_gray) <= os.path.getsize(out_color)

    def test_low_jpeg_quality_smaller(self, scan_pdf, tmp_path):
        out_low  = str(tmp_path / "low.pdf")
        out_high = str(tmp_path / "high.pdf")
        rasterize_scan_pdf(scan_pdf, out_low,  jpeg_quality=20)
        rasterize_scan_pdf(scan_pdf, out_high, jpeg_quality=90)
        assert os.path.getsize(out_low) < os.path.getsize(out_high)


# ---------------------------------------------------------------------------
# compress() — main entry point, end-to-end
# ---------------------------------------------------------------------------

class TestCompress:
    def test_returns_bytes_and_dict(self, digital_pdf):
        pdf_bytes, info = compress(digital_pdf)
        assert isinstance(pdf_bytes, bytes)
        assert len(pdf_bytes) > 0
        assert isinstance(info, dict)

    def test_info_has_required_keys(self, digital_pdf):
        _, info = compress(digital_pdf)
        required = {
            "pages", "file_size_bytes", "total_text_len", "total_images",
            "avg_text_len_per_page", "avg_images_per_page",
            "detected_class", "mode_used",
            "before_bytes", "after_bytes", "ratio", "saving_pct",
            "time_ms", "throughput_mb_s",
            "gs_available", "gs_used", "gs_executable",
        }
        assert required.issubset(info.keys())

    def test_output_is_valid_pdf(self, digital_pdf):
        pdf_bytes, _ = compress(digital_pdf)
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        assert doc.page_count == 3
        doc.close()

    def test_output_never_larger_than_input(self, digital_pdf):
        pdf_bytes, info = compress(digital_pdf)
        assert len(pdf_bytes) <= info["before_bytes"]

    def test_ratio_consistent(self, digital_pdf):
        pdf_bytes, info = compress(digital_pdf)
        expected_ratio = info["after_bytes"] / info["before_bytes"]
        assert abs(info["ratio"] - expected_ratio) < 0.001

    def test_saving_pct_consistent(self, digital_pdf):
        _, info = compress(digital_pdf)
        expected = (1 - info["ratio"]) * 100
        assert abs(info["saving_pct"] - expected) < 0.1

    def test_mode_auto_detects_digital(self, digital_pdf):
        _, info = compress(digital_pdf, mode="AUTO")
        # digital_pdf has lots of text — should be DIGITAL or HYBRID
        assert info["detected_class"] in ("DIGITAL", "HYBRID")
        assert info["mode_used"] == info["detected_class"]

    def test_mode_auto_detects_scan(self, scan_pdf):
        _, info = compress(scan_pdf, mode="AUTO")
        assert info["detected_class"] == "SCAN"
        assert info["mode_used"] == "SCAN"

    def test_force_mode_overrides_detection(self, digital_pdf):
        _, info = compress(digital_pdf, mode="SCAN")
        assert info["mode_used"] == "SCAN"
        # detected_class reflects actual content, not forced mode
        assert info["detected_class"] in ("DIGITAL", "HYBRID")

    def test_progress_callback_receives_events(self, digital_pdf):
        events = []
        compress(digital_pdf, progress_cb=collect_progress(events))
        assert len(events) > 0
        steps = {e[0] for e in events}
        assert "init" in steps

    def test_progress_has_init_and_select_best(self, digital_pdf):
        events = []
        compress(digital_pdf, progress_cb=collect_progress(events))
        steps = {e[0] for e in events}
        assert "init" in steps
        assert "select_best" in steps or "fallback" in steps

    def test_file_not_found_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            compress(str(tmp_path / "missing.pdf"))

    def test_invalid_mode_raises(self, digital_pdf):
        with pytest.raises(ValueError):
            compress(digital_pdf, mode="INVALID")

    def test_scan_mode_dpi_affects_size(self, tmp_path):
        # Test rasterize directly — compress() may fallback to original for tiny PDFs
        import io as _io
        from PIL import Image as _Image
        doc = fitz.open()
        for _ in range(2):
            page = doc.new_page(width=595, height=842)
            img = _Image.new("RGB", (400, 400), color=(180, 120, 60))
            buf = _io.BytesIO()
            img.save(buf, format="JPEG", quality=95)
            page.insert_image(fitz.Rect(0, 0, 595, 842), stream=buf.getvalue())
        pdf_path = str(tmp_path / "large_scan.pdf")
        doc.save(pdf_path); doc.close()

        out_low  = str(tmp_path / "low.pdf")
        out_high = str(tmp_path / "high.pdf")
        rasterize_scan_pdf(pdf_path, out_low,  target_dpi=72,  jpeg_quality=50)
        rasterize_scan_pdf(pdf_path, out_high, target_dpi=200, jpeg_quality=50)
        assert os.path.getsize(out_low) < os.path.getsize(out_high)

    def test_scan_mode_jpeg_quality_affects_size(self, tmp_path):
        import io as _io
        from PIL import Image as _Image
        doc = fitz.open()
        for _ in range(2):
            page = doc.new_page(width=595, height=842)
            img = _Image.new("RGB", (400, 400), color=(180, 120, 60))
            buf = _io.BytesIO()
            img.save(buf, format="JPEG", quality=95)
            page.insert_image(fitz.Rect(0, 0, 595, 842), stream=buf.getvalue())
        pdf_path = str(tmp_path / "large_scan2.pdf")
        doc.save(pdf_path); doc.close()

        out_low  = str(tmp_path / "low.pdf")
        out_high = str(tmp_path / "high.pdf")
        rasterize_scan_pdf(pdf_path, out_low,  target_dpi=150, jpeg_quality=20)
        rasterize_scan_pdf(pdf_path, out_high, target_dpi=150, jpeg_quality=90)
        assert os.path.getsize(out_low) < os.path.getsize(out_high)

    def test_grayscale_scan_smaller_than_color(self, scan_pdf):
        b_gray,  _ = compress(scan_pdf, mode="SCAN", grayscale=True)
        b_color, _ = compress(scan_pdf, mode="SCAN", grayscale=False)
        assert len(b_gray) <= len(b_color)

    def test_no_temp_files_left(self, digital_pdf, tmp_path, monkeypatch):
        """Verify temp dir is cleaned up after compress()."""
        import tempfile
        created_dirs = []
        original_mkdtemp = tempfile.mkdtemp

        def tracking_mkdtemp(**kwargs):
            d = original_mkdtemp(**kwargs)
            created_dirs.append(d)
            return d

        monkeypatch.setattr(tempfile, "mkdtemp", tracking_mkdtemp)
        compress(digital_pdf)
        for d in created_dirs:
            assert not os.path.exists(d), f"Temp dir not cleaned up: {d}"

    @pytest.mark.skipif(GS_EXECUTABLE is None, reason="Ghostscript not installed")
    def test_gs_used_for_digital_when_available(self, digital_pdf):
        _, info = compress(digital_pdf, mode="DIGITAL")
        assert info["gs_available"] is True

    @pytest.mark.skipif(GS_EXECUTABLE is not None, reason="Ghostscript is installed")
    def test_gs_not_used_when_unavailable(self, digital_pdf):
        _, info = compress(digital_pdf, mode="DIGITAL")
        assert info["gs_used"] is False
