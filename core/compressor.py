"""
PDF compression — multi-pass pipeline with real-time progress callbacks.

Pipeline routing
----------------
SCAN / bilevel (CCITT, JBIG2)
  Pass A  Ghostscript  — /screen recompress, grayscale conversion
  Pass B  pikepdf      — JBIG2 re-encode + metadata strip
  Pass C  PyMuPDF      — structural optimisation
  → pick smallest

SCAN / JPEG-based
  Pass A  rasterise    — parallel re-render at lower DPI as JPEG
  Pass B  PyMuPDF      — structural optimisation (skipped if output too large)
  → pick smallest

DIGITAL / HYBRID
  Pass A  Ghostscript  — font subset + image downsample
  Pass B  pikepdf      — JPEG recompress + JBIG2 for any 1-bit images
  Pass C  PyMuPDF      — structural optimisation
  Pass D  pikepdf      — post-GS squeeze
  → pick smallest

Safety fallback: if every pass is larger than the original, return the original.
"""

from __future__ import annotations

import io
import os
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional, Tuple

import fitz          # PyMuPDF
import pikepdf
from PIL import Image

from core.classifier import classify_pdf
from core.features import extract_features
from core.ghostscript import GS_EXECUTABLE, font_subsetting_gs
from core.jbig2 import JBIG2_EXECUTABLE, encode_image_jbig2_sequential

# Type aliases
ProgressCb = Optional[Callable[[str, int, str], None]]
Candidate  = Tuple[int, str]   # (size_bytes, file_path)

# Parallel workers for page rendering (JPEG encode releases the GIL).
_RENDER_WORKERS = min(4, os.cpu_count() or 2)
# Pages per rasterisation chunk — bounds peak RAM usage.
_RASTER_CHUNK   = 60
# PyMuPDF full-deflate threshold: above this, use light structural opt only.
# GS output is already stream-compressed, so the heavy path is only useful
# on small files that didn't go through GS.
_STRUCT_HEAVY_LIMIT_MB = 8


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _emit(cb: ProgressCb, step: str, pct: int, detail: str = "") -> None:
    if cb:
        cb(step, pct, detail)


def _tmp(d: str, name: str) -> str:
    return os.path.join(d, name)


def _size(path: str) -> int:
    return os.path.getsize(path) if os.path.exists(path) else 0


def _fmt(b: int) -> str:
    if b < 1024:      return f"{b} B"
    if b < 1_048_576: return f"{b / 1024:.1f} KB"
    return f"{b / 1_048_576:.2f} MB"


def _best(candidates: List[Candidate]) -> Candidate:
    return min(candidates, key=lambda x: x[0])


def _run_pass(
    label: str,
    fn,
    in_path: str,
    out_path: str,
    candidates: List[Candidate],
    time_acc: List[float],
    progress_cb: ProgressCb,
    **kwargs,
) -> None:
    """Execute one compression pass and append the result to candidates."""
    _emit(progress_cb, "pass_start", 0, label)
    try:
        result = fn(in_path, out_path, progress_cb=progress_cb, **kwargs)
        time_acc[0] += result.get("time_ms", 0.0)
        sz = _size(out_path)
        if sz:
            candidates.append((sz, out_path))
            _emit(progress_cb, "pass_done", 0, f"{label} → {_fmt(sz)}")
    except Exception as exc:
        _emit(progress_cb, "pass_err", 0, f"{label} failed: {exc}")


# ---------------------------------------------------------------------------
# Image decoding helpers
# ---------------------------------------------------------------------------

def _cs_to_mode(cs_obj) -> Optional[str]:
    """Map a PDF ColorSpace object to a Pillow mode string."""
    if cs_obj is None:
        return "RGB"
    if isinstance(cs_obj, list):
        cs_obj = cs_obj[0]
    cs = str(cs_obj)
    if "Gray" in cs: return "L"
    if "CMYK" in cs: return "CMYK"
    return "RGB"


def _xobj_to_pillow(xobj) -> Optional[Image.Image]:
    """
    Decode a pikepdf image XObject to a Pillow Image.
    Returns None for unsupported formats (CMYK, decode errors).
    1-bit images are returned in mode "1" for the JBIG2 encoding path.
    """
    try:
        w   = int(xobj.get("/Width",  0))
        h   = int(xobj.get("/Height", 0))
        if w == 0 or h == 0:
            return None

        bpc  = int(xobj.get("/BitsPerComponent", 8))
        mode = _cs_to_mode(xobj.get("/ColorSpace"))
        if mode == "CMYK":
            return None

        filt = str(xobj.get("/Filter") or "")

        if "DCTDecode" in filt or "JPXDecode" in filt:
            img = Image.open(io.BytesIO(xobj.read_raw_bytes()))
            img.load()
            return img.convert("L" if img.mode in ("L", "LA") else "RGB")

        data = xobj.read_bytes()

        if bpc == 1:
            return Image.frombytes("1", (w, h), data)

        ch = 1 if mode == "L" else 3
        if len(data) < w * h * ch:
            return None
        return Image.frombytes(mode, (w, h), data[:w * h * ch]).convert(
            "L" if mode == "L" else "RGB"
        )
    except Exception:
        return None


def _estimate_jpeg_quality(size: int, w: int, h: int, ch: int = 3) -> int:
    """Estimate JPEG quality from compressed size. Used to skip already-compressed images."""
    if w == 0 or h == 0:
        return 100
    bpp = (size * 8) / (w * h * ch)
    if bpp < 0.4: return 15
    if bpp < 0.8: return 30
    if bpp < 1.3: return 45
    if bpp < 2.0: return 60
    if bpp < 3.5: return 72
    if bpp < 5.5: return 82
    return 92


def _encode_jpeg(img: Image.Image, quality: int, grayscale: bool) -> bytes:
    """Encode a Pillow image to JPEG bytes."""
    img = img.convert("L" if (grayscale or img.mode == "L") else "RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality,
             optimize=False, progressive=False,
             subsampling=2 if quality < 85 else 0)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Pass: pikepdf image recompression + metadata strip
# ---------------------------------------------------------------------------

def pikepdf_recompress(
    in_path: str,
    out_path: str,
    jpeg_quality: int = 70,
    grayscale: bool = False,
    use_jbig2: bool = True,
    progress_cb: ProgressCb = None,
) -> Dict:
    """
    Recompress embedded images using pikepdf and strip metadata.

    1-bit images → JBIG2 (if jbig2enc available and use_jbig2=True)
    Color/gray   → JPEG at jpeg_quality
    """
    t0    = time.perf_counter()
    in_mb = os.path.getsize(in_path) / 1_048_576
    _emit(progress_cb, "pikepdf", 0, "Opening PDF…")

    jbig2_on = use_jbig2 and JBIG2_EXECUTABLE is not None
    tmp_dir  = tempfile.mkdtemp(prefix="jbig2_")

    try:
        with pikepdf.open(in_path, suppress_warnings=True) as pdf:
            # Strip all metadata except title and creator
            with pdf.open_metadata() as meta:
                keep = {"dc:title", "dc:creator"}
                for k in [k for k in meta if k not in keep]:
                    try:    del meta[k]
                    except: pass

            recompressed = skipped = jbig2_count = 0
            bytes_saved  = 0
            seen: set    = set()

            def collect_images(obj):
                """Recursively yield unique image XObjects, including nested Form XObjects."""
                try:
                    xobjs = obj.get("/XObject", {})
                except Exception:
                    return
                for name in list(xobjs.keys()):
                    try:
                        xobj    = xobjs[name]
                        subtype = xobj.get("/Subtype")
                        oid     = xobj.objgen
                        if oid in seen:
                            continue
                        seen.add(oid)
                        if subtype == "/Image":
                            yield xobj
                        elif subtype == "/Form" and "/Resources" in xobj:
                            yield from collect_images(xobj["/Resources"])
                    except Exception:
                        continue

            all_xobjs = [
                xobj
                for page in pdf.pages
                if "/Resources" in page
                for xobj in collect_images(page["/Resources"])
            ]
            total = len(all_xobjs)
            _emit(progress_cb, "pikepdf", 5,
                  f"Found {total} images | JBIG2: {'✓' if jbig2_on else '✗'}")

            for idx, xobj in enumerate(all_xobjs):
                pct = int(5 + (idx / max(total, 1)) * 80)
                if idx % 20 == 0:
                    _emit(progress_cb, "pikepdf", pct, f"Image {idx + 1}/{total}…")
                try:
                    w    = int(xobj.get("/Width",  0))
                    h    = int(xobj.get("/Height", 0))
                    bpc  = int(xobj.get("/BitsPerComponent", 8))
                    filt = str(xobj.get("/Filter") or "raw")

                    if w * h < 8192:
                        skipped += 1
                        continue

                    is_jpeg    = "DCTDecode" in filt or "JPXDecode" in filt
                    is_bilevel = bpc == 1 or "CCITTFaxDecode" in filt or "JBIG2Decode" in filt
                    orig_size  = len(xobj.read_raw_bytes())

                    # ── 1-bit → JBIG2 ────────────────────────────────────
                    if is_bilevel and jbig2_on:
                        img = _xobj_to_pillow(xobj)
                        if img is not None:
                            jbig2_bytes = encode_image_jbig2_sequential(img, tmp_dir, idx)
                            if jbig2_bytes and len(jbig2_bytes) < orig_size:
                                bytes_saved += orig_size - len(jbig2_bytes)
                                xobj.stream_dict.update({
                                    "/Filter":           pikepdf.Name("/JBIG2Decode"),
                                    "/ColorSpace":       pikepdf.Name("/DeviceGray"),
                                    "/BitsPerComponent": 1,
                                    "/Width":  w,
                                    "/Height": h,
                                })
                                for key in ("/DecodeParms", "/Decode", "/SMask",
                                            "/Intent", "/Interpolate"):
                                    xobj.stream_dict.pop(key, None)
                                xobj.write(jbig2_bytes, filter=pikepdf.Name("/JBIG2Decode"))
                                jbig2_count  += 1
                                recompressed += 1
                                continue
                        skipped += 1
                        continue

                    # ── JPEG quality gate ─────────────────────────────────
                    if is_jpeg:
                        ch    = 1 if "Gray" in str(xobj.get("/ColorSpace", "")) else 3
                        est_q = _estimate_jpeg_quality(orig_size, w, h, ch)
                        if est_q <= jpeg_quality:
                            skipped += 1
                            continue

                    # ── Re-encode as JPEG ─────────────────────────────────
                    img = _xobj_to_pillow(xobj)
                    if img is None or img.mode == "1":
                        skipped += 1
                        continue

                    new_jpeg = _encode_jpeg(img, jpeg_quality, grayscale)
                    if len(new_jpeg) >= orig_size:
                        skipped += 1
                        continue

                    bytes_saved += orig_size - len(new_jpeg)
                    out_cs = (
                        pikepdf.Name("/DeviceGray")
                        if (grayscale or img.mode == "L")
                        else pikepdf.Name("/DeviceRGB")
                    )
                    xobj.stream_dict.update({
                        "/Filter":           pikepdf.Name("/DCTDecode"),
                        "/ColorSpace":       out_cs,
                        "/BitsPerComponent": 8,
                        "/Width":  w,
                        "/Height": h,
                    })
                    for key in ("/DecodeParms", "/Decode", "/SMask", "/Intent", "/Interpolate"):
                        xobj.stream_dict.pop(key, None)
                    xobj.write(new_jpeg, filter=pikepdf.Name("/DCTDecode"))
                    recompressed += 1

                except Exception:
                    skipped += 1

            jbig2_note = f", {jbig2_count} JBIG2" if jbig2_count else ""
            _emit(progress_cb, "pikepdf", 88,
                  f"Saving… ({recompressed}/{total} recompressed{jbig2_note}, "
                  f"saved {_fmt(bytes_saved)})")
            pdf.save(
                out_path,
                compress_streams=True,
                object_stream_mode=pikepdf.ObjectStreamMode.generate,
                recompress_flate=in_mb <= 15.0,
                linearize=False,
            )

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    elapsed = (time.perf_counter() - t0) * 1000.0
    _emit(progress_cb, "pikepdf", 100,
          f"Done — {recompressed}/{total} recompressed{jbig2_note}, "
          f"saved {_fmt(bytes_saved)} ({elapsed:.0f}ms)")
    return {
        "time_ms":       elapsed,
        "recompressed":  recompressed,
        "jbig2_encoded": jbig2_count,
        "skipped":       skipped,
        "bytes_saved":   bytes_saved,
    }


# ---------------------------------------------------------------------------
# Pass: PyMuPDF structural optimisation
# ---------------------------------------------------------------------------

def optimize_pdf_structure(
    in_path: str,
    out_path: str,
    garbage: int = 4,
    deflate: bool = True,
    clean: bool = True,
    heavy_limit_mb: float = _STRUCT_HEAVY_LIMIT_MB,
    progress_cb: ProgressCb = None,
) -> Dict:
    """
    Optimise PDF structure via PyMuPDF.

    Full mode  (file ≤ heavy_limit_mb): garbage collect + deflate all streams + objstm rebuild.
    Light mode (file > heavy_limit_mb): garbage collect + clean only (no per-stream deflate).
    """
    file_mb = os.path.getsize(in_path) / 1_048_576
    heavy   = deflate and file_mb <= heavy_limit_mb
    eff_gc  = garbage if heavy else min(garbage, 3)

    mode_label = f"{'Full' if heavy else 'Light'} (gc={eff_gc}{'+deflate' if heavy else ''})"
    _emit(progress_cb, "struct", 10, f"{mode_label} on {file_mb:.1f} MB…")

    t0  = time.perf_counter()
    doc = fitz.open(in_path)
    try:
        _emit(progress_cb, "struct", 40, "Saving…")
        doc.save(
            out_path,
            garbage=eff_gc,
            deflate=heavy,
            clean=clean,
            incremental=False,
            deflate_images=heavy,
            deflate_fonts=heavy,
            use_objstms=1 if heavy else 0,
        )
    finally:
        doc.close()

    elapsed = (time.perf_counter() - t0) * 1000.0
    _emit(progress_cb, "struct", 100, f"Done ({elapsed:.0f}ms)")
    return {"time_ms": elapsed}


# ---------------------------------------------------------------------------
# Pass: Ghostscript
# ---------------------------------------------------------------------------

def gs_compress(
    in_path: str,
    out_path: str,
    pdf_setting: str = "/ebook",
    grayscale: bool = False,
    bilevel: bool = False,
    progress_cb: ProgressCb = None,
) -> Dict:
    """Run Ghostscript compression. bilevel=True forces /screen + grayscale."""
    label = "/screen (bilevel)" if bilevel else pdf_setting
    _emit(progress_cb, "ghostscript", 10, f"Running Ghostscript ({label})…")
    result = font_subsetting_gs(
        in_path, out_path,
        pdf_setting=pdf_setting,
        grayscale=grayscale,
        bilevel=bilevel,
    )
    _emit(progress_cb, "ghostscript", 100, f"Done ({result['time_ms']:.0f}ms)")
    return result


# ---------------------------------------------------------------------------
# Grayscale page detection
# ---------------------------------------------------------------------------

def _is_page_grayscale(page: fitz.Page) -> bool:
    """
    Return True if a page is effectively grayscale.
    Renders a tiny RGB thumbnail and compares mean R-G channel difference.
    """
    try:
        pix = page.get_pixmap(matrix=fitz.Matrix(0.1, 0.1),
                              colorspace=fitz.csRGB, alpha=False)
        s = pix.samples
        n = len(s) // 3
        if n == 0:
            return True
        mv   = memoryview(bytearray(s))
        r, g = mv[0::3], mv[1::3]
        diff = sum(abs(int(r[i]) - int(g[i])) for i in range(min(n, 500))) / min(n, 500)
        return diff < 4
    except Exception:
        return False


# ---------------------------------------------------------------------------
# SCAN rasterisation — parallel rendering, chunk-based disk flushing
# ---------------------------------------------------------------------------

def _render_page(
    page_idx: int,
    in_path: str,
    zoom: float,
    jpeg_quality: int,
    force_gray: bool,
) -> Tuple[int, bytes, Tuple[float, float]]:
    """
    Render one PDF page to JPEG bytes in a worker thread.
    Each worker opens its own fitz.Document to avoid shared-state issues.
    Returns (page_index, jpeg_bytes, (page_width, page_height)).
    """
    doc  = fitz.open(in_path)
    page = doc.load_page(page_idx)
    mat  = fitz.Matrix(zoom, zoom)

    if not force_gray:
        force_gray = _is_page_grayscale(page)

    cs   = fitz.csGRAY if force_gray else fitz.csRGB
    pix  = page.get_pixmap(matrix=mat, colorspace=cs, alpha=False)
    mode = "L" if pix.n == 1 else "RGB"
    img  = Image.frombytes(mode, [pix.width, pix.height], pix.samples)

    # Capture page dimensions before closing the document
    pw, ph = page.rect.width, page.rect.height
    doc.close()

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=jpeg_quality,
             optimize=False, progressive=False,
             subsampling=2 if jpeg_quality < 85 else 0)

    return page_idx, buf.getvalue(), (pw, ph)


def rasterize_scan_pdf(
    in_path: str,
    out_path: str,
    target_dpi: int = 150,
    jpeg_quality: int = 75,
    grayscale: bool = False,
    progress_cb: ProgressCb = None,
) -> Dict:
    """
    Re-render all pages as JPEG images at target_dpi.

    Uses parallel rendering (ThreadPoolExecutor) and chunk-based disk flushing
    to keep peak RAM bounded regardless of page count.
    """
    t0    = time.perf_counter()
    src   = fitz.open(in_path)
    total = src.page_count
    _emit(progress_cb, "rasterize", 0,
          f"Rasterising {total} pages @ {target_dpi} dpi ({_RENDER_WORKERS} workers)…")

    # Sample up to 20 pages to decide document-level grayscale mode.
    if not grayscale:
        step     = max(1, total // 20)
        sample   = list(range(0, total, step))[:20]
        votes    = sum(1 for i in sample if _is_page_grayscale(src.load_page(i)))
        doc_gray = votes >= len(sample) * 0.7
    else:
        doc_gray = True

    src.close()  # workers open their own handles

    if doc_gray:
        _emit(progress_cb, "rasterize", 2, "Grayscale document — forcing gray mode")

    zoom        = target_dpi / 72.0
    tmp_dir     = os.path.dirname(out_path)
    chunk_paths: List[str] = []

    try:
        chunk_start = 0
        while chunk_start < total:
            chunk_end  = min(chunk_start + _RASTER_CHUNK, total)
            chunk_path = os.path.join(tmp_dir, f"rchunk_{chunk_start:05d}.pdf")

            # Render chunk pages in parallel
            page_results: Dict[int, Tuple[bytes, Tuple[float, float]]] = {}
            with ThreadPoolExecutor(max_workers=_RENDER_WORKERS) as pool:
                futures = {
                    pool.submit(_render_page, i, in_path, zoom, jpeg_quality,
                                grayscale or doc_gray): i
                    for i in range(chunk_start, chunk_end)
                }
                done = 0
                for fut in as_completed(futures):
                    try:
                        pg_idx, jpeg_bytes, rect = fut.result()
                        page_results[pg_idx] = (jpeg_bytes, rect)
                    except Exception:
                        pass
                    done += 1
                    abs_idx = chunk_start + done - 1
                    if done % 20 == 0 or abs_idx == total - 1:
                        pct = int(2 + (abs_idx / max(total, 1)) * 88)
                        _emit(progress_cb, "rasterize", pct,
                              f"Page {abs_idx + 1}/{total}…")

            # Assemble chunk PDF in correct page order
            cdoc = fitz.open()
            for i in range(chunk_start, chunk_end):
                if i not in page_results:
                    continue
                jpeg_bytes, (pw, ph) = page_results[i]
                new_page = cdoc.new_page(width=pw, height=ph)
                new_page.insert_image(fitz.Rect(0, 0, pw, ph), stream=jpeg_bytes)

            _emit(progress_cb, "rasterize",
                  int(2 + (chunk_end / max(total, 1)) * 88),
                  f"Flushing pages {chunk_start + 1}–{chunk_end}…")
            cdoc.save(chunk_path, garbage=0, deflate=False, clean=False)
            cdoc.close()
            chunk_paths.append(chunk_path)
            chunk_start = chunk_end

        # Merge all chunks into the final output
        _emit(progress_cb, "rasterize", 92, f"Merging {len(chunk_paths)} chunk(s)…")
        merged = fitz.open()
        for cp in chunk_paths:
            with fitz.open(cp) as part:
                merged.insert_pdf(part)
        merged.save(out_path, garbage=2, deflate=False, clean=True)
        merged.close()

    finally:
        for cp in chunk_paths:
            try:    os.remove(cp)
            except: pass

    elapsed = (time.perf_counter() - t0) * 1000.0
    _emit(progress_cb, "rasterize", 100, f"Done ({elapsed:.0f}ms)")
    return {"time_ms": elapsed}


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
# Main entry point
# ---------------------------------------------------------------------------

def compress(
    in_path: str,
    mode: str = "AUTO",
    dpi: int = 150,
    jpeg_quality: int = 75,
    grayscale: bool = False,
    garbage: int = 4,
    deflate: bool = True,
    clean: bool = True,
    pdf_setting: str = "/ebook",
    scan_text_threshold: int = 20,
    digital_text_threshold: int = 200,
    min_images_for_scan: float = 1.0,
    max_size_for_gs_mb: float = 200.0,
    struct_heavy_limit_mb: float = _STRUCT_HEAVY_LIMIT_MB,
    progress_cb: ProgressCb = None,
) -> Tuple[bytes, Dict]:
    """
    Compress a PDF using a multi-pass pipeline.

    Returns (pdf_bytes, info_dict). progress_cb(step, pct, detail) is called
    throughout so callers can stream real-time progress (e.g. via SSE).
    The output is always a valid PDF and never larger than the input.
    """
    if not os.path.exists(in_path):
        raise FileNotFoundError(f"File not found: {in_path}")

    _emit(progress_cb, "init", 0, "Extracting PDF features…")
    before = os.path.getsize(in_path)
    feats  = extract_features(in_path)

    detected  = classify_pdf(
        feats,
        text_scan_threshold=scan_text_threshold,
        text_digital_threshold=digital_text_threshold,
        min_images_for_scan=min_images_for_scan,
    )
    used_mode = detected if mode == "AUTO" else mode
    if used_mode not in ("SCAN", "DIGITAL", "HYBRID"):
        raise ValueError(f"Unknown mode: {used_mode}")

    # Auto-scale DPI for large JPEG-scan docs when the user left the default.
    effective_dpi = dpi
    if used_mode == "SCAN" and dpi == 150 and feats.bilevel_image_ratio < 0.5:
        if   feats.pages > 500: effective_dpi = 100
        elif feats.pages > 300: effective_dpi = 110
        elif feats.pages > 100: effective_dpi = 120
        if effective_dpi != dpi:
            _emit(progress_cb, "init", 50,
                  f"Auto-scaled DPI {dpi}→{effective_dpi} for {feats.pages}-page document")

    gs_avail   = GS_EXECUTABLE is not None
    jbig2_avail = JBIG2_EXECUTABLE is not None
    gs_used    = False
    time_acc: List[float] = [0.0]

    _emit(progress_cb, "init", 100,
          f"Class: {detected} | Mode: {used_mode} | "
          f"Enc: {feats.dominant_image_encoding} | "
          f"GS: {'✓' if gs_avail else '✗'} | "
          f"JBIG2: {'✓' if jbig2_avail else '✗'} | "
          f"{_fmt(before)}")

    tmp_dir    = tempfile.mkdtemp(prefix="pdfcomp_")
    candidates: List[Candidate] = [(before, in_path)]

    try:
        # ── SCAN ──────────────────────────────────────────────────────────
        if used_mode == "SCAN":
            is_bilevel = feats.bilevel_image_ratio >= 0.5

            if is_bilevel:
                # CCITT/JBIG2 fax — rasterising makes these LARGER.
                # Ghostscript recompresses CCITT natively; JBIG2 squeezes further.
                _emit(progress_cb, "init", 100,
                      f"Bilevel ({feats.dominant_image_encoding}) — GS + JBIG2 pipeline")

                gs_path = _tmp(tmp_dir, "gs.pdf")
                skip_gs = not gs_avail or (before > max_size_for_gs_mb * 1_048_576)
                if skip_gs:
                    reason = "not available" if not gs_avail else f"file > {max_size_for_gs_mb} MB"
                    _emit(progress_cb, "pass_skip", 0, f"Pass A (GS) skipped — {reason}")
                else:
                    _run_pass(
                        "Pass A — Ghostscript (/screen, bilevel)",
                        gs_compress,
                        in_path, gs_path,
                        candidates, time_acc, progress_cb,
                        pdf_setting=pdf_setting, grayscale=grayscale, bilevel=True,
                    )

                _run_pass(
                    f"Pass B — pikepdf{' + JBIG2' if jbig2_avail else ''}",
                    pikepdf_recompress,
                    _best(candidates)[1], _tmp(tmp_dir, "pike.pdf"),
                    candidates, time_acc, progress_cb,
                    jpeg_quality=jpeg_quality, grayscale=grayscale, use_jbig2=True,
                )

                # Skip structural opt when GS ran — GS output is already clean.
                if skip_gs:
                    _run_pass(
                        "Pass C — Structural optimisation",
                        optimize_pdf_structure,
                        _best(candidates)[1], _tmp(tmp_dir, "struct.pdf"),
                        candidates, time_acc, progress_cb,
                        garbage=garbage, deflate=deflate, clean=clean,
                        heavy_limit_mb=struct_heavy_limit_mb,
                    )
                else:
                    _emit(progress_cb, "pass_skip", 0,
                          "Pass C skipped — GS output already structurally optimised")

                if not skip_gs:
                    bp = _best(candidates)[1]
                    gs_used = bp in (gs_path,
                                     _tmp(tmp_dir, "pike.pdf"),
                                     _tmp(tmp_dir, "struct.pdf"))

            else:
                # JPEG-based scan — re-render at lower DPI
                _run_pass(
                    f"Pass A — Rasterise @ {effective_dpi} dpi ({_RENDER_WORKERS} workers)",
                    rasterize_scan_pdf,
                    in_path, _tmp(tmp_dir, "raster.pdf"),
                    candidates, time_acc, progress_cb,
                    target_dpi=effective_dpi, jpeg_quality=jpeg_quality, grayscale=grayscale,
                )
                raster_mb = _size(_tmp(tmp_dir, "raster.pdf")) / 1_048_576
                if raster_mb <= struct_heavy_limit_mb:
                    _run_pass(
                        "Pass B — Structural optimisation",
                        optimize_pdf_structure,
                        _best(candidates)[1], _tmp(tmp_dir, "raster_struct.pdf"),
                        candidates, time_acc, progress_cb,
                        garbage=garbage, deflate=deflate, clean=clean,
                        heavy_limit_mb=struct_heavy_limit_mb,
                    )
                else:
                    _emit(progress_cb, "pass_skip", 0,
                          f"Pass B skipped — rasterised output {raster_mb:.1f} MB already clean")

        # ── DIGITAL / HYBRID ──────────────────────────────────────────────
        else:
            gs_path = _tmp(tmp_dir, "gs.pdf")
            skip_gs = not gs_avail or (before > max_size_for_gs_mb * 1_048_576)

            # Pass A — Ghostscript: font subset + image downsample in one shot
            if skip_gs:
                reason = "not available" if not gs_avail else f"file > {max_size_for_gs_mb} MB"
                _emit(progress_cb, "pass_skip", 0, f"Pass A (GS) skipped — {reason}")
            else:
                _run_pass(
                    f"Pass A — Ghostscript ({pdf_setting})",
                    gs_compress,
                    in_path, gs_path,
                    candidates, time_acc, progress_cb,
                    pdf_setting=pdf_setting, grayscale=grayscale,
                )

            # Pass B — pikepdf on original (GS doesn't aggressively re-encode JPEGs)
            pike_a = _tmp(tmp_dir, "pike.pdf")
            _run_pass(
                f"Pass B — pikepdf{' + JBIG2' if jbig2_avail else ''}",
                pikepdf_recompress,
                in_path, pike_a,
                candidates, time_acc, progress_cb,
                jpeg_quality=jpeg_quality, grayscale=grayscale, use_jbig2=True,
            )

            # Pass C — structural optimisation.
            # Skip when GS already ran — GS output is already stream-compressed
            # and structurally clean. Running PyMuPDF deflate on top of GS output
            # is redundant and blocks for minutes with no meaningful size reduction.
            if skip_gs:
                _run_pass(
                    "Pass C — Structural optimisation",
                    optimize_pdf_structure,
                    _best(candidates)[1], _tmp(tmp_dir, "struct.pdf"),
                    candidates, time_acc, progress_cb,
                    garbage=garbage, deflate=deflate, clean=clean,
                    heavy_limit_mb=struct_heavy_limit_mb,
                )
            else:
                _emit(progress_cb, "pass_skip", 0,
                      "Pass C skipped — GS output already structurally optimised")

            # Pass D — pikepdf post-GS squeeze (GS leaves JPEGs untouched)
            if not skip_gs and _size(gs_path) > 0:
                _run_pass(
                    f"Pass D — pikepdf post-GS (q={jpeg_quality})",
                    pikepdf_recompress,
                    _best(candidates)[1], _tmp(tmp_dir, "pike2.pdf"),
                    candidates, time_acc, progress_cb,
                    jpeg_quality=jpeg_quality, grayscale=grayscale, use_jbig2=True,
                )

            if not skip_gs:
                bp = _best(candidates)[1]
                gs_used = bp in (gs_path,
                                 _tmp(tmp_dir, "struct.pdf"),
                                 _tmp(tmp_dir, "pike2.pdf"))

        # ── Safety fallback ───────────────────────────────────────────────
        best_size, best_path = _best(candidates)
        if best_size >= before:
            _emit(progress_cb, "fallback", 0,
                  "All passes larger than original — returning original")
            best_path   = in_path
            best_size   = before
            gs_used     = False
            time_acc[0] = 0.0
        else:
            saving = round((1 - best_size / before) * 100, 1)
            _emit(progress_cb, "select_best", 100,
                  f"Best: {_fmt(best_size)} (saved {saving}%)")

        with open(best_path, "rb") as f:
            pdf_bytes = f.read()

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    m = compute_metrics(before, best_size, time_acc[0])
    return pdf_bytes, {
        "pages":                   feats.pages,
        "file_size_bytes":         feats.file_size_bytes,
        "total_text_len":          feats.total_text_len,
        "total_images":            feats.total_images,
        "avg_text_len_per_page":   round(feats.avg_text_len_per_page, 2),
        "avg_images_per_page":     round(feats.avg_images_per_page, 2),
        "dominant_image_encoding": feats.dominant_image_encoding,
        "bilevel_image_ratio":     round(feats.bilevel_image_ratio, 2),
        "avg_image_area_ratio":    feats.avg_image_area_ratio,
        "avg_text_area_ratio":     feats.avg_text_area_ratio,
        "detected_class":          detected,
        "mode_used":               used_mode,
        "dpi_used":                effective_dpi if used_mode == "SCAN" else None,
        "before_bytes":            int(m["before_bytes"]),
        "after_bytes":             int(best_size),
        "ratio":                   round(m["ratio"], 4),
        "saving_pct":              round(m["saving_pct"], 2),
        "time_ms":                 round(m["time_ms"], 2),
        "throughput_mb_s":         round(m["throughput_mb_s"], 2),
        "gs_available":            gs_avail,
        "gs_used":                 gs_used,
        "gs_executable":           GS_EXECUTABLE,
        "jbig2_available":         jbig2_avail,
        "jbig2_executable":        JBIG2_EXECUTABLE,
    }
