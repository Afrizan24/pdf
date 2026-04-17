#!/usr/bin/env python3
"""CLI entry point for PDF Compression Research Tool. Core logic lives in core/."""

import argparse

from core import compress, COMPRESSION_LEVELS, GS_EXECUTABLE, JBIG2_EXECUTABLE


def _fmt(b: int) -> str:
    if b < 1024:      return f"{b} B"
    if b < 1_048_576: return f"{b / 1024:.1f} KB"
    return f"{b / 1_048_576:.2f} MB"


def main() -> None:
    ap = argparse.ArgumentParser(
        description="PDF Compression Research Tool — two-pass pipeline: Ghostscript + pikepdf"
    )
    ap.add_argument("input",  help="Input PDF path")
    ap.add_argument("output", help="Output PDF path")

    ap.add_argument("--mode", default="AUTO",
                    choices=["AUTO", "DIGITAL", "SCAN", "HYBRID"],
                    help="Classification mode (default: AUTO)")
    ap.add_argument("--level", default="MEDIUM",
                    choices=["HIGH", "MEDIUM", "LOW"],
                    help="Compression level preset (default: MEDIUM)")
    ap.add_argument("--pdf-setting", default=None,
                    choices=["/screen", "/ebook", "/printer", "/prepress"],
                    help="Override GS PDFSETTINGS preset (default: from level)")
    ap.add_argument("--color-dpi", type=int, default=None,
                    help="Override colour image downsample DPI (default: from level)")
    ap.add_argument("--gray-dpi", type=int, default=None,
                    help="Override grayscale image downsample DPI (default: from level)")
    ap.add_argument("--mono-dpi", type=int, default=None,
                    help="Override monochrome image downsample DPI (default: from level)")
    ap.add_argument("--jpeg-quality", type=int, default=None,
                    help="Override JPEG quality 20-100 (default: auto from DPI)")
    ap.add_argument("--grayscale", action="store_true",
                    help="Convert all colour to grayscale")
    ap.add_argument("--no-pikepdf", action="store_true",
                    help="Disable pikepdf structural optimization (Pass B)")
    ap.add_argument("--scan-text-th", type=int, default=20,
                    help="Avg chars/page below which a doc is SCAN (default: 20)")
    ap.add_argument("--digital-text-th", type=int, default=200,
                    help="Avg chars/page above which a doc is DIGITAL (default: 200)")
    ap.add_argument("--min-img-scan", type=float, default=1.0,
                    help="Min avg images/page to classify as SCAN (default: 1.0)")
    ap.add_argument("--max-size-gs", type=float, default=200.0,
                    help="Max file size in MB to process with GS (default: 200)")

    args = ap.parse_args()

    result_bytes, info = compress(
        in_path=args.input,
        mode=args.mode,
        level=args.level,
        pdf_setting=args.pdf_setting,
        color_dpi=args.color_dpi,
        gray_dpi=args.gray_dpi,
        mono_dpi=args.mono_dpi,
        jpeg_quality=args.jpeg_quality,
        grayscale=args.grayscale,
        pikepdf_optimize=not args.no_pikepdf,
        scan_text_threshold=args.scan_text_th,
        digital_text_threshold=args.digital_text_th,
        min_images_for_scan=args.min_img_scan,
        max_size_for_gs_mb=args.max_size_gs,
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
    print(f"  Confidence         : {info['classification_confidence']}")

    print("\n=== COMPRESSION PARAMS ===")
    print(f"  Mode               : {info['mode_used']}")
    print(f"  Level              : {info['level_used']}")
    print(f"  GS preset          : {info['pdf_setting_used']}")
    print(f"  Color DPI          : {info['color_dpi_used']}")
    print(f"  Gray DPI           : {info['gray_dpi_used']}")
    print(f"  Mono DPI           : {info['mono_dpi_used']}")
    print(f"  Grayscale          : {info['grayscale']}")
    print(f"  pikepdf Pass B     : {info['pikepdf_optimize']}")
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
