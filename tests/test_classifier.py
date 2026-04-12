"""Tests for core/classifier.py — rule-based PDF classification."""

from __future__ import annotations

import pytest
from core.classifier import classify_pdf
from core.features import PdfFeatures


def make_feats(avg_text: float, avg_imgs: float) -> PdfFeatures:
    return PdfFeatures(
        pages=1,
        file_size_bytes=1000,
        total_text_len=int(avg_text),
        total_images=int(avg_imgs),
        avg_text_len_per_page=avg_text,
        avg_images_per_page=avg_imgs,
    )


class TestClassifyPdf:
    # ── SCAN cases ──────────────────────────────────────────────────────────

    def test_scan_low_text_high_images(self):
        feats = make_feats(avg_text=5.0, avg_imgs=2.0)
        assert classify_pdf(feats) == "SCAN"

    def test_scan_zero_text_one_image(self):
        feats = make_feats(avg_text=0.0, avg_imgs=1.0)
        assert classify_pdf(feats) == "SCAN"

    def test_scan_boundary_text_threshold(self):
        # avg_text exactly at threshold (19 < 20) with images
        feats = make_feats(avg_text=19.9, avg_imgs=1.0)
        assert classify_pdf(feats) == "SCAN"

    def test_not_scan_when_no_images(self):
        feats = make_feats(avg_text=5.0, avg_imgs=0.0)
        assert classify_pdf(feats) != "SCAN"

    def test_not_scan_when_text_above_threshold(self):
        feats = make_feats(avg_text=25.0, avg_imgs=2.0)
        assert classify_pdf(feats) != "SCAN"

    # ── DIGITAL cases ────────────────────────────────────────────────────────

    def test_digital_high_text_no_images(self):
        feats = make_feats(avg_text=500.0, avg_imgs=0.0)
        assert classify_pdf(feats) == "DIGITAL"

    def test_digital_boundary_text_threshold(self):
        feats = make_feats(avg_text=200.0, avg_imgs=0.0)
        assert classify_pdf(feats) == "DIGITAL"

    def test_not_digital_when_has_images(self):
        feats = make_feats(avg_text=500.0, avg_imgs=1.0)
        assert classify_pdf(feats) != "DIGITAL"

    def test_not_digital_when_text_below_threshold(self):
        feats = make_feats(avg_text=150.0, avg_imgs=0.0)
        assert classify_pdf(feats) != "DIGITAL"

    # ── HYBRID cases ─────────────────────────────────────────────────────────

    def test_hybrid_medium_text_some_images(self):
        feats = make_feats(avg_text=100.0, avg_imgs=0.5)
        assert classify_pdf(feats) == "HYBRID"

    def test_hybrid_high_text_with_images(self):
        feats = make_feats(avg_text=300.0, avg_imgs=1.0)
        assert classify_pdf(feats) == "HYBRID"

    def test_hybrid_low_text_no_images(self):
        feats = make_feats(avg_text=10.0, avg_imgs=0.0)
        assert classify_pdf(feats) == "HYBRID"

    # ── Custom threshold cases ────────────────────────────────────────────────

    def test_custom_scan_threshold(self):
        feats = make_feats(avg_text=30.0, avg_imgs=2.0)
        # Default threshold=20 → not SCAN, but with threshold=50 → SCAN
        assert classify_pdf(feats, text_scan_threshold=50) == "SCAN"

    def test_custom_digital_threshold(self):
        feats = make_feats(avg_text=150.0, avg_imgs=0.0)
        # Default threshold=200 → not DIGITAL, but with threshold=100 → DIGITAL
        assert classify_pdf(feats, text_digital_threshold=100) == "DIGITAL"

    def test_custom_min_images_for_scan(self):
        feats = make_feats(avg_text=5.0, avg_imgs=0.5)
        # Default min_images=1.0 → not SCAN, but with min_images=0.3 → SCAN
        assert classify_pdf(feats, min_images_for_scan=0.3) == "SCAN"

    # ── Return value is always one of three valid classes ────────────────────

    @pytest.mark.parametrize("avg_text,avg_imgs", [
        (0, 0), (0, 5), (50, 0), (50, 2), (300, 0), (300, 3),
    ])
    def test_always_returns_valid_class(self, avg_text, avg_imgs):
        feats = make_feats(avg_text, avg_imgs)
        result = classify_pdf(feats)
        assert result in ("SCAN", "DIGITAL", "HYBRID")
