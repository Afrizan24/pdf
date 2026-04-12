"""Tests for core/features.py — PDF feature extraction."""

from __future__ import annotations

import os
import pytest
from core.features import PdfFeatures, extract_features


class TestPdfFeatures:
    def test_dataclass_fields(self):
        f = PdfFeatures(
            pages=5,
            file_size_bytes=1024,
            total_text_len=500,
            total_images=10,
            avg_text_len_per_page=100.0,
            avg_images_per_page=2.0,
        )
        assert f.pages == 5
        assert f.file_size_bytes == 1024
        assert f.total_text_len == 500
        assert f.total_images == 10
        assert f.avg_text_len_per_page == 100.0
        assert f.avg_images_per_page == 2.0


class TestExtractFeatures:
    def test_returns_pdffeatures(self, digital_pdf):
        feats = extract_features(digital_pdf)
        assert isinstance(feats, PdfFeatures)

    def test_page_count(self, digital_pdf):
        feats = extract_features(digital_pdf)
        assert feats.pages == 3

    def test_file_size_matches_disk(self, digital_pdf):
        feats = extract_features(digital_pdf)
        assert feats.file_size_bytes == os.path.getsize(digital_pdf)

    def test_digital_has_text(self, digital_pdf):
        feats = extract_features(digital_pdf)
        assert feats.total_text_len > 0
        assert feats.avg_text_len_per_page > 0

    def test_scan_has_images(self, scan_pdf):
        feats = extract_features(scan_pdf)
        assert feats.total_images > 0
        assert feats.avg_images_per_page > 0

    def test_scan_minimal_text(self, scan_pdf):
        feats = extract_features(scan_pdf)
        # Scan PDF has no inserted text
        assert feats.total_text_len == 0

    def test_averages_computed_correctly(self, digital_pdf):
        feats = extract_features(digital_pdf)
        assert feats.avg_text_len_per_page == feats.total_text_len / feats.pages
        assert feats.avg_images_per_page == feats.total_images / feats.pages

    def test_empty_pdf(self, empty_pdf):
        feats = extract_features(empty_pdf)
        assert feats.pages == 1
        assert feats.total_text_len == 0
        assert feats.total_images == 0
        assert feats.avg_text_len_per_page == 0.0
        assert feats.avg_images_per_page == 0.0

    def test_file_not_found(self, tmp_path):
        with pytest.raises(Exception):
            extract_features(str(tmp_path / "nonexistent.pdf"))
