"""
core — PDF compression engine.

Public API:
    compress()        — main entry point, returns (pdf_bytes, info_dict)
    extract_features() — extract PdfFeatures from a PDF path
    classify_pdf()    — classify a PDF into SCAN / DIGITAL / HYBRID
    GS_EXECUTABLE     — path to Ghostscript binary, or None
    JBIG2_EXECUTABLE  — path to jbig2enc binary, or None
"""

from core.compressor import compress
from core.features import PdfFeatures, extract_features
from core.classifier import classify_pdf
from core.ghostscript import GS_EXECUTABLE
from core.jbig2 import JBIG2_EXECUTABLE

__all__ = [
    "compress",
    "extract_features",
    "PdfFeatures",
    "classify_pdf",
    "GS_EXECUTABLE",
    "JBIG2_EXECUTABLE",
]
