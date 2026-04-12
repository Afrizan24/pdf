"""
PDF compression — multi-pass pipeline with real-time progress callbacks.

Pipeline per mode
-----------------
DIGITAL  : [A] pikepdf img recompress + metadata strip
           [B] PyMuPDF struct opt (on best of A)
           [C] Ghostscript font-subset (on original)
           → pick smallest across all candidates

SCAN     : [A] rasterise → progressive JPEG (per-page auto-grayscale)
           [B] PyMuPDF struct opt on raster result
           → pick smallest

HYBRID   : same as DIGITAL (pikepdf handles embedded images well)

All modes: safety fallback — if every pass is larger than original, return original.
"""

from __future__ import annotations

import io
import os
import shutil
import statistics
import tempfile
import time
from typing import Callable, Dict, List, Optional, Tuple

import fitz          # PyMuPDF
import pikepdf
from PIL import Image

from core.classifier import classify_pdf
from core.features import extract_features
from core.ghostscript import GS_EXECUTABLE, font_subsetting_gs

# Progress callback type: (step: str, pct: int, detail: str) -> None
ProgressCb = Optional[Callable[[str, int, str], None]]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _p(cb: ProgressCb, step: str, pct: int, detail: str = "") -> None:
    if cb:
        cb(step, pct, detail)


def _tmp(d: str, name: str) -> str:
    return os.path.join(d, name)


def _size(path: str) -> int:
    return os.path.getsize(path) if os.path.exists(path) else 0


def _fmt(b: int) -> str:
    if b < 1024:
        return f"{b} B"
    if b < 1_048_576:
        return f"{b/1024:.1f} KB"
    return f"{b/1_048_576:.2f} MB"


# ---------------------------------------------------------------------------
# Image recompression helpers
# ---------------------------------------------------------------------------

def _cs_to_mode(cs_obj) -> Optional[str]:
    """Map PDF ColorSpace to Pillow mode string."""
    if cs_obj is None:
        return "RGB"
    # Handle array form: [/ICCBased stream] or [/Indexed /DeviceRGB ...]
    if isinstance(cs_obj, list):
        cs_obj = cs_obj[0]
    cs = str(cs_obj)
    if "Gray" in cs:
        return "L"
    if "CMYK" in cs:
        return "CMYK"
    # ICCBased, CalRGB, sRGB, DeviceRGB, etc → treat as RGB
    return "RGB"


def _xobj_to_pillow(xobj) -> Optional[Image.Image]:
    """
    Decode a PDF image XObject into a Pillow Image.
    Returns None if the image cannot be decoded or should be skipped.
    """
    try:
        w = int(xobj.get("/Width", 0))
        h = int(xobj.get("/Height", 0))
        if w == 0 or h == 0:
            return None

        bpc = int(xobj.get("/BitsPerComponent", 8))
        cs_obj = xobj.get("/ColorSpace")
        mode = _cs_to_mode(cs_obj)

        # Skip 1-bit (masks) and CMYK
        if bpc == 1 or mode == "CMYK":
            return None

        filt = xobj.get("/Filter")
        filt_str = str(filt) if filt is not None else ""

        if "DCTDecode" in filt_str or "JPXDecode" in filt_str:
            # JPEG/JPEG2000 — raw bytes are the compressed image
            raw = xobj.read_raw_bytes()
            img = Image.open(io.BytesIO(raw))
            img.load()
            # Normalize to L or RGB
            if img.mode in ("L", "LA"):
                return img.convert("L")
            return img.convert("RGB")

        # FlateDecode / no filter — read_bytes() gives decoded pixels
        pixel_data = xobj.read_bytes()
        channels = 1 if mode == "L" else 3
        expected = w * h * channels
        if len(pixel_data) < expected:
            return None
        img = Image.frombytes(mode, (w, h), pixel_data[:expected])
        return img.convert("L" if mode == "L" else "RGB")

    except Exception:
        return None


def _estimate_jpeg_quality(compressed_size: int, w: int, h: int, channels: int = 3) -> int:
    """
    Rough estimate of JPEG quality from file size.
    Returns 0-100, lower = more compressed original.
    """
    if w == 0 or h == 0:
        return 100
    bpp = (compressed_size * 8) / (w * h * channels)
    # Empirical mapping: bpp → quality
    if bpp < 0.5:  return 20
    if bpp < 1.0:  return 35
    if bpp < 1.5:  return 50
    if bpp < 2.5:  return 65
    if bpp < 4.0:  return 75
    if bpp < 6.0:  return 85
    return 95


def _encode_jpeg(img: Image.Image, quality: int, grayscale: bool) -> bytes:
    """Encode Pillow image to JPEG bytes."""
    if grayscale or img.mode == "L":
        img = img.convert("L")
    else:
        img = img.convert("RGB")

    buf = io.BytesIO()
    img.save(
        buf, format="JPEG",
        quality=quality,
        optimize=True,
        progressive=True,
        subsampling=2 if quality < 85 else 0,
    )
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Pass A — pikepdf: recompress embedded images + strip metadata
# ---------------------------------------------------------------------------

def pikepdf_recompress(
    in_path: str,
    out_path: str,
    jpeg_quality: int = 70,
    grayscale: bool = False,
    progress_cb: ProgressCb = None,
) -> Dict[str, float]:
    t0 = time.perf_counter()
    _p(progress_cb, "pikepdf", 0, "Membuka PDF…")

    with pikepdf.open(in_path, suppress_warnings=True) as pdf:
        total_pages = len(pdf.pages)

        # Strip metadata
        with pdf.open_metadata() as meta:
            keep = {"dc:title", "dc:creator"}
            for k in [k for k in meta if k not in keep]:
                try:
                    del meta[k]
                except Exception:
                    pass

        recompressed = 0
        skipped = 0
        bytes_saved = 0
        seen_objids: set = set()

        # Collect all image XObjects from all pages + global resources
        def iter_xobjects(resources):
            if "/XObject" not in resources:
                return
            for name in list(resources["/XObject"].keys()):
                xobj = resources["/XObject"][name]
                try:
                    if xobj.get("/Subtype") != "/Image":
                        continue
                    obj_id = xobj.objgen
                    if obj_id in seen_objids:
                        continue
                    seen_objids.add(obj_id)
                    yield xobj
                except Exception:
                    continue

        # Build flat list of all unique image xobjects
        all_xobjs = []
        for page in pdf.pages:
            if "/Resources" in page:
                all_xobjs.extend(iter_xobjects(page["/Resources"]))

        total_imgs = len(all_xobjs)
        _p(progress_cb, "pikepdf", 5,
           f"Ditemukan {total_imgs} gambar unik dari {total_pages} halaman…")

        for idx, xobj in enumerate(all_xobjs):
            pct = int(5 + (idx / max(total_imgs, 1)) * 80)
            try:
                w = int(xobj.get("/Width", 0))
                h = int(xobj.get("/Height", 0))
                filt = xobj.get("/Filter")
                filt_str = str(filt) if filt is not None else "raw"
                is_jpeg = "DCTDecode" in filt_str or "JPXDecode" in filt_str

                if w * h < 4096:
                    skipped += 1
                    _p(progress_cb, "pikepdf", pct,
                       f"Gambar {idx+1}/{total_imgs} skip: terlalu kecil ({w}x{h})")
                    continue

                orig_raw = xobj.read_raw_bytes()
                orig_size = len(orig_raw)

                # For already-JPEG images: skip if estimated quality already <= target
                # (re-encoding would make it larger due to generational loss)
                if is_jpeg:
                    channels = 1 if "Gray" in str(xobj.get("/ColorSpace", "")) else 3
                    est_q = _estimate_jpeg_quality(orig_size, w, h, channels)
                    if est_q <= jpeg_quality:
                        skipped += 1
                        _p(progress_cb, "pikepdf", pct,
                           f"Gambar {idx+1}/{total_imgs} skip: sudah terkompresi "
                           f"(est. q≈{est_q} ≤ target q={jpeg_quality}, {w}x{h})")
                        continue

                img = _xobj_to_pillow(xobj)
                if img is None:
                    skipped += 1
                    _p(progress_cb, "pikepdf", pct,
                       f"Gambar {idx+1}/{total_imgs} skip: decode gagal "
                       f"({w}x{h}, filter={filt_str})")
                    continue

                new_jpeg = _encode_jpeg(img, jpeg_quality, grayscale)

                if len(new_jpeg) >= orig_size:
                    skipped += 1
                    _p(progress_cb, "pikepdf", pct,
                       f"Gambar {idx+1}/{total_imgs} skip: hasil lebih besar "
                       f"({_fmt(len(new_jpeg))} >= {_fmt(orig_size)}, "
                       f"{w}x{h}, filter={filt_str})")
                    continue

                saved = orig_size - len(new_jpeg)
                bytes_saved += saved

                out_cs = (pikepdf.Name("/DeviceGray")
                          if (grayscale or img.mode == "L")
                          else pikepdf.Name("/DeviceRGB"))

                xobj.stream_dict["/Filter"] = pikepdf.Name("/DCTDecode")
                xobj.stream_dict["/ColorSpace"] = out_cs
                xobj.stream_dict["/BitsPerComponent"] = 8
                xobj.stream_dict["/Width"] = w
                xobj.stream_dict["/Height"] = h
                for key in ("/DecodeParms", "/Decode", "/SMask",
                            "/Intent", "/Interpolate"):
                    if key in xobj.stream_dict:
                        try:
                            del xobj.stream_dict[key]
                        except Exception:
                            pass
                xobj.write(new_jpeg, filter=pikepdf.Name("/DCTDecode"))
                recompressed += 1
                _p(progress_cb, "pikepdf", pct,
                   f"Gambar {idx+1}/{total_imgs} ✓ "
                   f"{_fmt(orig_size)} → {_fmt(len(new_jpeg))} "
                   f"(hemat {_fmt(saved)}, {w}x{h}, q={jpeg_quality})")

            except Exception as ex:
                skipped += 1
                _p(progress_cb, "pikepdf", pct,
                   f"Gambar {idx+1}/{total_imgs} error: {ex}")

        _p(progress_cb, "pikepdf", 88,
           f"Menyimpan… ({recompressed}/{total_imgs} dikompresi, hemat {_fmt(bytes_saved)})")
        pdf.save(
            out_path,
            compress_streams=True,
            object_stream_mode=pikepdf.ObjectStreamMode.generate,
            recompress_flate=True,
            linearize=False,
        )

    elapsed = (time.perf_counter() - t0) * 1000.0
    _p(progress_cb, "pikepdf", 100,
       f"Selesai — {recompressed}/{total_imgs} dikompresi, "
       f"hemat {_fmt(bytes_saved)} ({elapsed:.0f}ms)")
    return {"time_ms": elapsed, "recompressed": recompressed,
            "skipped": skipped, "bytes_saved": bytes_saved}


# ---------------------------------------------------------------------------
# Pass B — PyMuPDF: structural optimisation
# ---------------------------------------------------------------------------

def optimize_pdf_structure(
    in_path: str,
    out_path: str,
    garbage: int = 4,
    deflate: bool = True,
    clean: bool = True,
    progress_cb: ProgressCb = None,
) -> Dict[str, float]:
    _p(progress_cb, "struct", 10, "Membuka PDF untuk optimasi struktur…")
    t0 = time.perf_counter()
    doc = fitz.open(in_path)
    try:
        _p(progress_cb, "struct", 40, "Menjalankan garbage collection & deflate…")
        doc.save(
            out_path,
            garbage=garbage,
            deflate=deflate,
            clean=clean,
            incremental=False,
            deflate_images=True,
            deflate_fonts=True,
            use_objstms=1,
        )
    finally:
        doc.close()

    elapsed = (time.perf_counter() - t0) * 1000.0
    _p(progress_cb, "struct", 100, f"Selesai ({elapsed:.0f}ms)")
    return {"time_ms": elapsed}


# ---------------------------------------------------------------------------
# Pass C — Ghostscript font subsetting
# ---------------------------------------------------------------------------
# Thin wrapper that adds progress events around core.ghostscript call

def gs_compress(
    in_path: str,
    out_path: str,
    pdf_setting: str = "/ebook",
    grayscale: bool = False,
    progress_cb: ProgressCb = None,
) -> Dict[str, float]:
    _p(progress_cb, "ghostscript", 10, f"Menjalankan Ghostscript ({pdf_setting})…")
    result = font_subsetting_gs(in_path, out_path,
                                pdf_setting=pdf_setting, grayscale=grayscale)
    _p(progress_cb, "ghostscript", 100,
       f"Selesai ({result['time_ms']:.0f}ms)")
    return result


# ---------------------------------------------------------------------------
# SCAN rasterisation — progressive JPEG + per-page auto-grayscale
# ---------------------------------------------------------------------------

def _is_page_grayscale(page: fitz.Page) -> bool:
    try:
        pix = page.get_pixmap(matrix=fitz.Matrix(0.12, 0.12),
                              colorspace=fitz.csRGB, alpha=False)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        r, g, b = img.split()
        r_v = list(r.tobytes())
        g_v = list(g.tobytes())
        b_v = list(b.tobytes())
        diff_rg = statistics.mean(abs(a - b_) for a, b_ in zip(r_v, g_v))
        diff_rb = statistics.mean(abs(a - b_) for a, b_ in zip(r_v, b_v))
        return diff_rg < 8 and diff_rb < 8
    except Exception:
        return False


def rasterize_scan_pdf(
    in_path: str,
    out_path: str,
    target_dpi: int = 150,
    jpeg_quality: int = 75,
    grayscale: bool = False,
    progress_cb: ProgressCb = None,
) -> Dict[str, float]:
    t0 = time.perf_counter()
    src = fitz.open(in_path)
    dst = fitz.open()
    total = src.page_count

    _p(progress_cb, "rasterize", 0, f"Memulai rasterisasi {total} halaman…")

    try:
        zoom = target_dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)

        for i in range(total):
            pct = int((i / max(total, 1)) * 88)
            _p(progress_cb, "rasterize", pct,
               f"Render halaman {i + 1}/{total} @ {target_dpi}dpi…")

            page = src.load_page(i)
            page_gray = grayscale or _is_page_grayscale(page)
            colorspace = fitz.csGRAY if page_gray else fitz.csRGB
            pix = page.get_pixmap(matrix=mat, colorspace=colorspace, alpha=False)

            mode = "L" if pix.n == 1 else "RGB"
            img = Image.frombytes(mode, [pix.width, pix.height], pix.samples)

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=jpeg_quality,
                     optimize=True, progressive=True,
                     subsampling=2 if jpeg_quality < 85 else 0)

            rect = page.rect
            new_page = dst.new_page(width=rect.width, height=rect.height)
            new_page.insert_image(rect, stream=buf.getvalue())

        _p(progress_cb, "rasterize", 92, "Menyimpan PDF hasil rasterisasi…")
        dst.save(out_path, garbage=4, deflate=True, clean=True)
    finally:
        src.close()
        dst.close()

    elapsed = (time.perf_counter() - t0) * 1000.0
    _p(progress_cb, "rasterize", 100, f"Selesai ({elapsed:.0f}ms)")
    return {"time_ms": elapsed}


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def compute_metrics(before: int, after: int, time_ms: float) -> Dict:
    ratio = after / before if before > 0 else 0.0
    return {
        "before_bytes": float(before),
        "after_bytes": float(after),
        "ratio": ratio,
        "saving_pct": (1.0 - ratio) * 100.0,
        "time_ms": time_ms,
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
    max_size_for_gs_mb: float = 50.0,
    progress_cb: ProgressCb = None,
) -> Tuple[bytes, Dict]:
    """
    Multi-pass PDF compression. Returns (pdf_bytes, info_dict).
    progress_cb(step, pct, detail) is called throughout the pipeline.
    """
    if not os.path.exists(in_path):
        raise FileNotFoundError(f"File not found: {in_path}")

    _p(progress_cb, "init", 0, "Mengekstrak fitur PDF…")
    before = os.path.getsize(in_path)
    feats = extract_features(in_path)
    detected = classify_pdf(
        feats,
        text_scan_threshold=scan_text_threshold,
        text_digital_threshold=digital_text_threshold,
        min_images_for_scan=min_images_for_scan,
    )
    used_mode = detected if mode == "AUTO" else mode
    gs_used = False
    gs_available = GS_EXECUTABLE is not None
    time_ms = 0.0

    _p(progress_cb, "init", 100,
       f"Kelas: {detected} → Mode: {used_mode} | {_fmt(before)}")

    tmp_dir = tempfile.mkdtemp(prefix="pdfcomp_")
    best_path = in_path
    best_size = before

    try:
        # ── SCAN ──────────────────────────────────────────────────────────
        if used_mode == "SCAN":
            candidates: List[Tuple[int, str]] = [(before, in_path)]

            # Pass A: rasterise
            raster_path = _tmp(tmp_dir, "raster.pdf")
            _p(progress_cb, "pass_start", 0, "Pass A — Rasterisasi halaman…")
            try:
                sA = rasterize_scan_pdf(
                    in_path, raster_path,
                    target_dpi=dpi, jpeg_quality=jpeg_quality, grayscale=grayscale,
                    progress_cb=lambda s, p, d: _p(
                        progress_cb, "rasterize", p,
                        f"[Pass A] {d} → sementara {_fmt(_size(raster_path))}"
                        if _size(raster_path) else f"[Pass A] {d}"
                    ),
                )
                time_ms += sA["time_ms"]
                if _size(raster_path):
                    candidates.append((_size(raster_path), raster_path))
                    _p(progress_cb, "pass_done", 0,
                       f"Pass A selesai → {_fmt(_size(raster_path))}")
            except Exception as e:
                _p(progress_cb, "pass_err", 0, f"Pass A gagal: {e}")

            # Pass B: struct opt on raster
            struct_path = _tmp(tmp_dir, "raster_struct.pdf")
            src_for_struct = min(candidates, key=lambda x: x[0])[1]
            _p(progress_cb, "pass_start", 0, "Pass B — Optimasi struktur PDF…")
            try:
                sB = optimize_pdf_structure(
                    src_for_struct, struct_path,
                    garbage=garbage, deflate=deflate, clean=clean,
                    progress_cb=lambda s, p, d: _p(progress_cb, "struct", p, f"[Pass B] {d}"),
                )
                time_ms += sB["time_ms"]
                if _size(struct_path):
                    candidates.append((_size(struct_path), struct_path))
                    _p(progress_cb, "pass_done", 0,
                       f"Pass B selesai → {_fmt(_size(struct_path))}")
            except Exception as e:
                _p(progress_cb, "pass_err", 0, f"Pass B gagal: {e}")

            candidates.sort(key=lambda x: x[0])
            best_size, best_path = candidates[0]

        # ── DIGITAL / HYBRID ──────────────────────────────────────────────
        elif used_mode in ("DIGITAL", "HYBRID"):
            candidates = [(before, in_path)]

            # Pass A: pikepdf image recompress on original
            pike_path = _tmp(tmp_dir, "pike.pdf")
            _p(progress_cb, "pass_start", 0, "Pass A — Rekompresi gambar (pikepdf)…")
            try:
                sA = pikepdf_recompress(
                    in_path, pike_path,
                    jpeg_quality=jpeg_quality, grayscale=grayscale,
                    progress_cb=lambda s, p, d: _p(progress_cb, "pikepdf", p, f"[Pass A] {d}"),
                )
                time_ms += sA["time_ms"]
                if _size(pike_path):
                    candidates.append((_size(pike_path), pike_path))
                    _p(progress_cb, "pass_done", 0,
                       f"Pass A selesai → {_fmt(_size(pike_path))}")
            except Exception as e:
                _p(progress_cb, "pass_err", 0, f"Pass A gagal: {e}")

            # Pass B: PyMuPDF struct opt on best so far
            struct_path = _tmp(tmp_dir, "struct.pdf")
            best_so_far = min(candidates, key=lambda x: x[0])[1]
            _p(progress_cb, "pass_start", 0, "Pass B — Optimasi struktur PDF…")
            try:
                sB = optimize_pdf_structure(
                    best_so_far, struct_path,
                    garbage=garbage, deflate=deflate, clean=clean,
                    progress_cb=lambda s, p, d: _p(progress_cb, "struct", p, f"[Pass B] {d}"),
                )
                time_ms += sB["time_ms"]
                if _size(struct_path):
                    candidates.append((_size(struct_path), struct_path))
                    _p(progress_cb, "pass_done", 0,
                       f"Pass B selesai → {_fmt(_size(struct_path))}")
            except Exception as e:
                _p(progress_cb, "pass_err", 0, f"Pass B gagal: {e}")

            # Pass C: Ghostscript font-subset — feed best result so far
            skip_gs = not gs_available or (before > max_size_for_gs_mb * 1_048_576)
            gs_path = _tmp(tmp_dir, "gs.pdf")
            if skip_gs:
                reason = "tidak tersedia" if not gs_available else f"file > {max_size_for_gs_mb}MB"
                _p(progress_cb, "pass_skip", 0, f"Pass C (Ghostscript) dilewati — {reason}")
            else:
                best_for_gs = min(candidates, key=lambda x: x[0])[1]
                _p(progress_cb, "pass_start", 0,
                   f"Pass C — Ghostscript font-subset ({pdf_setting})…")
                try:
                    sC = gs_compress(
                        best_for_gs, gs_path,
                        pdf_setting=pdf_setting, grayscale=grayscale,
                        progress_cb=lambda s, p, d: _p(
                            progress_cb, "ghostscript", p, f"[Pass C] {d}"),
                    )
                    time_ms += sC["time_ms"]
                    if _size(gs_path):
                        candidates.append((_size(gs_path), gs_path))
                        _p(progress_cb, "pass_done", 0,
                           f"Pass C selesai → {_fmt(_size(gs_path))}")
                except Exception as e:
                    _p(progress_cb, "pass_err", 0, f"Pass C gagal: {e}")

            # Pass D: pikepdf rekompresi gambar di atas hasil GS
            # GS tidak agresif rekompresi JPEG — pikepdf bisa squeeze lebih lanjut
            # Ini yang membuat jpeg_quality benar-benar berpengaruh pada output akhir
            best_for_d = min(candidates, key=lambda x: x[0])[1]
            pike2_path = _tmp(tmp_dir, "pike2.pdf")
            _p(progress_cb, "pass_start", 0,
               f"Pass D — Rekompresi gambar post-GS (q={jpeg_quality})…")
            try:
                sD = pikepdf_recompress(
                    best_for_d, pike2_path,
                    jpeg_quality=jpeg_quality, grayscale=grayscale,
                    progress_cb=lambda s, p, d: _p(progress_cb, "pikepdf", p, f"[Pass D] {d}"),
                )
                time_ms += sD["time_ms"]
                if _size(pike2_path):
                    candidates.append((_size(pike2_path), pike2_path))
                    _p(progress_cb, "pass_done", 0,
                       f"Pass D selesai → {_fmt(_size(pike2_path))}")
            except Exception as e:
                _p(progress_cb, "pass_err", 0, f"Pass D gagal: {e}")

            candidates.sort(key=lambda x: x[0])
            best_size, best_path = candidates[0]
            gs_used = not skip_gs and (
                best_path == gs_path or best_path == pike2_path
            )

        else:
            raise ValueError(f"Unknown mode: {used_mode}")

        # ── Safety fallback ───────────────────────────────────────────────
        if best_size >= before:
            _p(progress_cb, "fallback", 0,
               "Semua pass lebih besar dari asli — mengembalikan file original")
            best_path = in_path
            best_size = before
            gs_used = False
            time_ms = 0.0
        else:
            saving = round((1 - best_size / before) * 100, 1)
            _p(progress_cb, "select_best", 100,
               f"Hasil terbaik: {_fmt(best_size)} (hemat {saving}%)")

        with open(best_path, "rb") as f:
            pdf_bytes = f.read()

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    m = compute_metrics(before, best_size, time_ms)

    info = {
        "pages": feats.pages,
        "file_size_bytes": feats.file_size_bytes,
        "total_text_len": feats.total_text_len,
        "total_images": feats.total_images,
        "avg_text_len_per_page": round(feats.avg_text_len_per_page, 2),
        "avg_images_per_page": round(feats.avg_images_per_page, 2),
        "detected_class": detected,
        "mode_used": used_mode,
        "before_bytes": int(m["before_bytes"]),
        "after_bytes": int(best_size),
        "ratio": round(m["ratio"], 4),
        "saving_pct": round(m["saving_pct"], 2),
        "time_ms": round(m["time_ms"], 2),
        "throughput_mb_s": round(m["throughput_mb_s"], 2),
        "gs_available": gs_available,
        "gs_used": gs_used,
        "gs_executable": GS_EXECUTABLE,
    }

    return pdf_bytes, info
