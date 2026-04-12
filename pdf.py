#!/usr/bin/env python3
"""
CLI entry point for Adaptive PDF Compressor.
Core logic lives in the `core/` package.
"""

import argparse

from core.compressor import compress
from core.features import extract_features, PdfFeatures
from core.ghostscript import GS_EXECUTABLE

# Re-export for any code that imports directly from pdf.py
__all__ = ["compress", "extract_features", "PdfFeatures", "GS_EXECUTABLE"]


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Adaptive PDF Compressor — output is always a valid .pdf"
    )
    ap.add_argument("input",  help="Input PDF path")
    ap.add_argument("output", help="Output PDF path")

    ap.add_argument("--scan-text-th",    type=int,   default=20)
    ap.add_argument("--digital-text-th", type=int,   default=200)
    ap.add_argument("--min-img-scan",    type=float, default=1.0)
    ap.add_argument("--dpi",             type=int,   default=150)
    ap.add_argument("--jpeg-q",          type=int,   default=75)
    ap.add_argument("--grayscale",       action="store_true")
    ap.add_argument("--pdf-setting",     default="/ebook",
                    choices=["/screen", "/ebook", "/printer", "/prepress"])
    ap.add_argument("--garbage",         type=int,   default=4)
    ap.add_argument("--no-deflate",      action="store_true")
    ap.add_argument("--no-clean",        action="store_true")
    ap.add_argument("--mode",            default="AUTO",
                    choices=["AUTO", "DIGITAL", "SCAN", "HYBRID"])

    args = ap.parse_args()

    result_bytes, result = compress(
        in_path=args.input,
        mode=args.mode,
        dpi=args.dpi,
        jpeg_quality=args.jpeg_q,
        grayscale=args.grayscale,
        garbage=args.garbage,
        deflate=not args.no_deflate,
        clean=not args.no_clean,
        pdf_setting=args.pdf_setting,
        scan_text_threshold=args.scan_text_th,
        digital_text_threshold=args.digital_text_th,
        min_images_for_scan=args.min_img_scan,
    )

    with open(args.output, "wb") as f:
        f.write(result_bytes)

    print("\n=== PDF FEATURES ===")
    print(f"  Pages              : {result['pages']}")
    print(f"  File size (bytes)  : {result['file_size_bytes']}")
    print(f"  Total text length  : {result['total_text_len']}")
    print(f"  Total images       : {result['total_images']}")
    print(f"  Avg text/page      : {result['avg_text_len_per_page']:.2f}")
    print(f"  Avg images/page    : {result['avg_images_per_page']:.2f}")
    print(f"  Detected class     : {result['detected_class']}")
    print(f"  Mode used          : {result['mode_used']}")
    print(f"  Ghostscript        : {'available (' + result['gs_executable'] + ')' if result['gs_available'] else 'not found'}")
    print(f"  GS used            : {result['gs_used']}")

    print("\n=== RESULTS ===")
    print(f"  Before (bytes)     : {result['before_bytes']:,}")
    print(f"  After  (bytes)     : {result['after_bytes']:,}")
    print(f"  Ratio              : {result['ratio']:.4f}")
    print(f"  Saving (%)         : {result['saving_pct']:.2f}%")
    print(f"  Time (ms)          : {result['time_ms']:.2f}")
    print(f"  Throughput (MB/s)  : {result['throughput_mb_s']:.2f}")


if __name__ == "__main__":
    main()
