#!/usr/bin/env python3
"""CLI entry point for Adaptive PDF Compressor. Core logic lives in core/."""

import argparse

from core import compress, GS_EXECUTABLE, JBIG2_EXECUTABLE


def _fmt(b: int) -> str:
    if b < 1024:      return f"{b} B"
    if b < 1_048_576: return f"{b / 1024:.1f} KB"
    return f"{b / 1_048_576:.2f} MB"


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Adaptive PDF Compressor — output is always a valid .pdf"
    )
    ap.add_argument("input",  help="Input PDF path")
    ap.add_argument("output", help="Output PDF path")

    ap.add_argument("--mode",            default="AUTO",
                    choices=["AUTO", "DIGITAL", "SCAN", "HYBRID"])
    ap.add_argument("--dpi",             type=int,   default=150,
                    help="Target DPI for SCAN rasterisation (default: 150)")
    ap.add_argument("--jpeg-q",          type=int,   default=75,
                    help="JPEG quality 20–95 (default: 75)")
    ap.add_argument("--grayscale",       action="store_true",
                    help="Force grayscale output")
    ap.add_argument("--pdf-setting",     default="/ebook",
                    choices=["/screen", "/ebook", "/printer", "/prepress"],
                    help="Ghostscript PDFSETTINGS preset (default: /ebook)")
    ap.add_argument("--garbage",         type=int,   default=4,
                    help="PyMuPDF garbage collection level 0–4 (default: 4)")
    ap.add_argument("--no-deflate",      action="store_true",
                    help="Disable stream deflation in structural pass")
    ap.add_argument("--no-clean",        action="store_true",
                    help="Disable PDF structure cleaning")
    ap.add_argument("--scan-text-th",    type=int,   default=20,
                    help="Avg chars/page below which a doc is considered SCAN (default: 20)")
    ap.add_argument("--digital-text-th", type=int,   default=200,
                    help="Avg chars/page above which a doc is considered DIGITAL (default: 200)")
    ap.add_argument("--min-img-scan",    type=float, default=1.0,
                    help="Min avg images/page to classify as SCAN (default: 1.0)")

    args = ap.parse_args()

    result_bytes, info = compress(
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

    gs_status    = f"available ({GS_EXECUTABLE})"    if info["gs_available"]    else "not found"
    jbig2_status = f"available ({JBIG2_EXECUTABLE})" if info["jbig2_available"] else "not found"

    print("\n=== PDF FEATURES ===")
    print(f"  Pages              : {info['pages']}")
    print(f"  File size          : {_fmt(info['file_size_bytes'])}")
    print(f"  Total text length  : {info['total_text_len']}")
    print(f"  Total images       : {info['total_images']}")
    print(f"  Avg text / page    : {info['avg_text_len_per_page']:.2f}")
    print(f"  Avg images / page  : {info['avg_images_per_page']:.2f}")
    print(f"  Image encoding     : {info['dominant_image_encoding']}")
    print(f"  Bilevel ratio      : {info['bilevel_image_ratio']:.0%}")
    print(f"  Image area ratio   : {info.get('avg_image_area_ratio', 0):.0%}")
    print(f"  Text area ratio    : {info.get('avg_text_area_ratio', 0):.0%}")
    print(f"  Detected class     : {info['detected_class']}")
    print(f"  Mode used          : {info['mode_used']}")
    if info.get("dpi_used"):
        print(f"  DPI used           : {info['dpi_used']}")
    print(f"  Ghostscript        : {gs_status}")
    print(f"  GS used            : {info['gs_used']}")
    print(f"  JBIG2enc           : {jbig2_status}")

    print("\n=== RESULTS ===")
    print(f"  Before             : {_fmt(info['before_bytes'])} ({info['before_bytes']:,} B)")
    print(f"  After              : {_fmt(info['after_bytes'])} ({info['after_bytes']:,} B)")
    print(f"  Ratio              : {info['ratio']:.4f}")
    print(f"  Saving             : {info['saving_pct']:.2f}%")
    print(f"  Time               : {info['time_ms']:.0f} ms")
    print(f"  Throughput         : {info['throughput_mb_s']:.2f} MB/s")


if __name__ == "__main__":
    main()
