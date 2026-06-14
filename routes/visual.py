"""
Flask routes: /visual/* — Step-by-step stateful visual compression pipeline.
"""

from __future__ import annotations

import base64
import os
import tempfile
import time
import uuid
from typing import Dict, Any

from flask import Blueprint, jsonify, request, render_template

import fitz  # PyMuPDF
from core.features import extract_features
from core.classifier import classify_pdf_with_confidence
from core.ghostscript import font_subsetting_gs
from core.compressor import pikepdf_structural_optimize, COMPRESSION_LEVELS
from core.evaluator import evaluate as evaluate_quality

visual_bp = Blueprint("visual", __name__)

@visual_bp.route("/visual", methods=["GET"])
def visual_page():
    return render_template("visual.html")

# In-memory session store: token → { paths, features, params }
_sessions: Dict[str, dict] = {}
_SESSIONS_DIR = tempfile.mkdtemp(prefix="pdf_visual_sessions_")

def _get_thumbnail(pdf_path: str, dpi: int = 72) -> str:
    """Generate a base64 encoded PNG thumbnail of the first page using PyMuPDF."""
    try:
        doc = fitz.open(pdf_path)
        if doc.page_count > 0:
            page = doc.load_page(0)
            pix = page.get_pixmap(dpi=dpi)
            b64 = base64.b64encode(pix.tobytes("png")).decode("utf-8")
            doc.close()
            return f"data:image/png;base64,{b64}"
    except Exception:
        pass
    return ""

def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024: return f"{size_bytes} B"
    if size_bytes < 1048576: return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / 1048576:.2f} MB"

def _get_font_stats(pdf_path: str) -> tuple:
    full_count, sub_count = 0, 0
    font_list = []
    try:
        doc = fitz.open(pdf_path)
        all_fonts = set()
        for i in range(doc.page_count):
            for f in doc.get_page_fonts(i):
                all_fonts.add(f[3]) # basefont string
        doc.close()
        
        for f in sorted(all_fonts):
            is_sub = "+" in f
            if is_sub:
                sub_count += 1
                clean_name = f.split("+", 1)[1] if "+" in f else f
            else:
                full_count += 1
                clean_name = f
            font_list.append({"name": clean_name, "is_subset": is_sub, "raw": f})
    except Exception:
        pass
    
    return full_count, sub_count, font_list

def _get_anatomy_payload(pdf_path: str) -> dict:
    size_bytes = os.path.getsize(pdf_path) if os.path.exists(pdf_path) else 0
    a = _analyze_pdf_anatomy(pdf_path)
    f_full, f_sub, f_list = _get_font_stats(pdf_path)
    return {
        "size_mb": round(size_bytes / 1048576, 3),
        "font_mb": round(a["fonts_bytes"] / 1048576, 3),
        "img_mb": round(a["images_bytes"] / 1048576, 3),
        "other_mb": round(a["other_bytes"] / 1048576, 3),
        "full_fonts": f_full,
        "sub_fonts": f_sub,
        "font_list": f_list
    }

def _analyze_pdf_anatomy(pdf_path: str) -> dict:
    res = {"fonts_bytes": 0, "images_bytes": 0, "other_bytes": 0}
    try:
        doc = fitz.open(pdf_path)
        for xref in range(1, doc.xref_length()):
            if not doc.xref_is_stream(xref): continue
            try:
                obj_str = doc.xref_object(xref)
                stream_data = doc.xref_stream_raw(xref)
                if not stream_data: continue
                size = len(stream_data)
                
                if "/Subtype /Image" in obj_str:
                    res["images_bytes"] += size
                elif "/FontFile" in obj_str or "/Length1" in obj_str:
                    res["fonts_bytes"] += size
                else:
                    res["other_bytes"] += size
            except Exception:
                pass
        doc.close()
    except Exception:
        pass
    return res

@visual_bp.route("/visual/upload", methods=["POST"])
def visual_upload():
    file = request.files.get("pdf")
    if not file or not file.filename:
        return jsonify({"error": "No file uploaded."}), 400

    token = str(uuid.uuid4())
    orig_path = os.path.join(_SESSIONS_DIR, f"orig_{token}.pdf")
    file.save(orig_path)

    size_bytes = os.path.getsize(orig_path)
    thumbnail = _get_thumbnail(orig_path)

    pages = 0
    try:
        doc = fitz.open(orig_path)
        pages = doc.page_count
        doc.close()
    except Exception:
        pass

    _sessions[token] = {
        "orig_path": orig_path,
        "size_bytes": size_bytes,
        "thumbnail": thumbnail,
        "filename": file.filename,
        "pages": pages
    }

    return jsonify({
        "token": token,
        "filename": file.filename,
        "size_bytes": size_bytes,
        "size_formatted": _format_size(size_bytes),
        "thumbnail": thumbnail,
        "pages": pages,
        "anatomy": _get_anatomy_payload(orig_path)
    })

@visual_bp.route("/visual/extract/<token>", methods=["POST"])
def visual_extract(token: str):
    if token not in _sessions:
        return jsonify({"error": "Invalid or expired token."}), 404
    
    orig_path = _sessions[token]["orig_path"]
    try:
        feats = extract_features(orig_path)
        _sessions[token]["features"] = feats
        return jsonify({
            "pages": feats.pages,
            "total_images": feats.total_images,
            "avg_text_area_ratio": round(feats.avg_text_area_ratio, 3),
            "avg_image_area_ratio": round(feats.avg_image_area_ratio, 3),
            "total_text_len": feats.total_text_len,
            "dominant_image_encoding": feats.dominant_image_encoding,
            "bilevel_image_ratio": round(feats.bilevel_image_ratio, 3),
            "library_used": "PyMuPDF (fitz)",
            "params_used": 'get_text("dict"), get_images(full=True), xref_object()'
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

@visual_bp.route("/visual/classify/<token>", methods=["POST"])
def visual_classify(token: str):
    if token not in _sessions or "features" not in _sessions[token]:
        return jsonify({"error": "Invalid token or features not extracted."}), 404
    
    feats = _sessions[token]["features"]
    try:
        cls, conf = classify_pdf_with_confidence(feats)
        
        # Build explanation
        reason = ""
        img_a = feats.avg_image_area_ratio
        txt_a = feats.avg_text_area_ratio
        
        if img_a > 0 and txt_a == 0:
            reason = "Hanya berisi gambar, tidak terdeteksi area teks."
        elif txt_a > 0 and img_a == 0:
            reason = "Hanya berisi teks digital, tidak terdeteksi area gambar."
        elif txt_a > 0 and img_a > 0:
            reason = "Dokumen memiliki perpaduan antara gambar dan teks digital."
        else:
            reason = "Dokumen kosong / tidak terdeteksi teks maupun gambar."

        target_preset = "MEDIUM (/ebook) -> Color DPI: 150, Mono DPI: 300"
        if cls == "SCAN":
            target_preset = "Target Preset: MEDIUM (/ebook) -> Color DPI: 150, Mono DPI: 300"
        elif cls == "DIGITAL":
            target_preset = "Target Preset: HIGH (/printer) -> Color DPI: 300, Mono DPI: 300"
        elif cls == "HYBRID":
            target_preset = "Target Preset: MEDIUM (/ebook) -> Color DPI: 150, Mono DPI: 300 (Balanced)"

        _sessions[token]["class"] = cls

        return jsonify({
            "class": cls,
            "confidence": conf,
            "reason": f"Evaluasi Logika (If-Then): {reason}",
            "target_preset": target_preset,
            "library_used": "Custom Rule-based Engine",
            "params_used": "Simplified Logic: Image Area vs Text Area"
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

@visual_bp.route("/visual/gs/<token>", methods=["POST"])
def visual_gs(token: str):
    if token not in _sessions:
        return jsonify({"error": "Invalid token."}), 404
    
    orig_path = _sessions[token]["orig_path"]
    gs_path = os.path.join(_SESSIONS_DIR, f"gs_{token}.pdf")
    
    level = request.form.get("level", "MEDIUM").upper()
    preset = COMPRESSION_LEVELS.get(level, COMPRESSION_LEVELS["MEDIUM"])
    
    pdf_setting = request.form.get("pdf_setting") or preset["pdf_setting"]
    color_dpi = int(request.form.get("color_dpi") or preset["color_dpi"])
    gray_dpi = int(request.form.get("gray_dpi") or preset["gray_dpi"])
    mono_dpi = int(request.form.get("mono_dpi") or preset["mono_dpi"])
    grayscale = str(request.form.get("grayscale", "false")).lower() in ("true", "1")
    jpeg_quality = request.form.get("jpeg_quality")
    if jpeg_quality: jpeg_quality = int(jpeg_quality)

    is_scan = _sessions[token].get("class") == "SCAN"

    try:
        t0 = time.perf_counter()
        gs_result = font_subsetting_gs(
            orig_path, gs_path,
            pdf_setting=pdf_setting,
            grayscale=grayscale,
            color_dpi=color_dpi,
            gray_dpi=gray_dpi,
            mono_dpi=mono_dpi,
            jpeg_quality=jpeg_quality,
            is_scan=is_scan
        )
        elapsed = (time.perf_counter() - t0) * 1000

        gs_size = os.path.getsize(gs_path) if os.path.exists(gs_path) else 0
        thumbnail = _get_thumbnail(gs_path)
        _sessions[token]["gs_path"] = gs_path
        _sessions[token]["gs_size"] = gs_size

        cmd_preview = f"gs -sDEVICE=pdfwrite -dCompatibilityLevel=1.4 -dPDFSETTINGS={pdf_setting} -dColorImageResolution={color_dpi} -dGrayImageResolution={gray_dpi} -dMonoImageResolution={mono_dpi}"
        if grayscale: cmd_preview += " -sColorConversionStrategy=Gray"

        return jsonify({
            "size_bytes": gs_size,
            "size_formatted": _format_size(gs_size),
            "thumbnail": thumbnail,
            "time_ms": round(elapsed, 1),
            "library_used": "Ghostscript (gs)",
            "params_used": cmd_preview,
            "larger_than_orig": gs_size >= _sessions[token]["size_bytes"],
            "anatomy": _get_anatomy_payload(gs_path)
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

@visual_bp.route("/visual/pike/<token>", methods=["POST"])
def visual_pike(token: str):
    if token not in _sessions:
        return jsonify({"error": "Invalid token."}), 404
    
    # Pikepdf optimizes the GS output if it exists and is smaller, else original
    orig_path = _sessions[token]["orig_path"]
    gs_path = _sessions[token].get("gs_path")
    gs_size = _sessions[token].get("gs_size", float("inf"))
    orig_size = _sessions[token]["size_bytes"]

    in_path = gs_path if (gs_path and gs_size < orig_size) else orig_path
    pike_path = os.path.join(_SESSIONS_DIR, f"pike_{token}.pdf")

    try:
        t0 = time.perf_counter()
        r = pikepdf_structural_optimize(in_path, pike_path)
        elapsed = (time.perf_counter() - t0) * 1000

        pike_size = os.path.getsize(pike_path) if os.path.exists(pike_path) else 0
        thumbnail = _get_thumbnail(pike_path)
        _sessions[token]["pike_path"] = pike_path

        return jsonify({
            "size_bytes": pike_size,
            "size_formatted": _format_size(pike_size),
            "thumbnail": thumbnail,
            "time_ms": round(elapsed, 1),
            "library_used": "pikepdf",
            "params_used": "compress_streams=True, object_stream_mode=generate, metadata_strip=True",
            "source_used": "Ghostscript Output" if in_path == gs_path else "Original File",
            "anatomy": _get_anatomy_payload(pike_path)
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

@visual_bp.route("/visual/download/<token>", methods=["GET"])
def visual_download(token: str):
    from flask import send_file
    if token not in _sessions:
        return jsonify({"error": "Invalid token."}), 404
    
    # Pick the best file
    orig_path = _sessions[token]["orig_path"]
    gs_path = _sessions[token].get("gs_path")
    pike_path = _sessions[token].get("pike_path")

    candidates = [(os.path.getsize(orig_path), orig_path)]
    if gs_path and os.path.exists(gs_path):
        candidates.append((os.path.getsize(gs_path), gs_path))
    if pike_path and os.path.exists(pike_path):
        candidates.append((os.path.getsize(pike_path), pike_path))
    
    best_size, best_path = min(candidates, key=lambda x: x[0])
    
    stem = _sessions[token]["filename"].rsplit(".", 1)[0]
    return send_file(
        best_path,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"{stem}_compressed.pdf"
    )

@visual_bp.route("/visual/evaluate/<token>", methods=["POST"])
def visual_evaluate(token: str):
    if token not in _sessions:
        return jsonify({"error": "Invalid token."}), 404
    
    orig_path = _sessions[token]["orig_path"]
    doc_type = _sessions[token].get("class", "HYBRID")
    
    gs_path = _sessions[token].get("gs_path")
    pike_path = _sessions[token].get("pike_path")
    
    # Determine the final compressed path
    candidates = [(os.path.getsize(orig_path), orig_path)]
    if gs_path and os.path.exists(gs_path):
        candidates.append((os.path.getsize(gs_path), gs_path))
    if pike_path and os.path.exists(pike_path):
        candidates.append((os.path.getsize(pike_path), pike_path))
        
    _, best_path = min(candidates, key=lambda x: x[0])
    
    max_pages = int(request.form.get("max_pages", 20))
    eval_dpi = int(request.form.get("eval_dpi", 100)) # Lower DPI for fast feedback in UI

    try:
        t0 = time.perf_counter()
        res = evaluate_quality(orig_path, best_path, doc_type, max_pages=max_pages, eval_dpi=eval_dpi)
        elapsed = (time.perf_counter() - t0) * 1000
        
        data = res.to_dict()
        data["time_ms"] = round(elapsed, 1)
        data["library_used"] = "scikit-image & SequenceMatcher"
        data["params_used"] = f"max_pages={max_pages}, eval_dpi={eval_dpi}"
        
        # Generate Anatomy data for UI Visualization
        orig_anat = _analyze_pdf_anatomy(orig_path)
        final_anat = _analyze_pdf_anatomy(best_path)
        orig_f_full, orig_f_sub, _ = _get_font_stats(orig_path)
        final_f_full, final_f_sub, _ = _get_font_stats(best_path)
        
        data["anatomy"] = {
            "orig_font_mb": orig_anat["fonts_bytes"] / 1048576,
            "final_font_mb": final_anat["fonts_bytes"] / 1048576,
            "orig_img_mb": orig_anat["images_bytes"] / 1048576,
            "final_img_mb": final_anat["images_bytes"] / 1048576,
            "orig_full_fonts": orig_f_full,
            "final_full_fonts": final_f_full,
            "orig_sub_fonts": orig_f_sub,
            "final_sub_fonts": final_f_sub
        }

        # Generate Timeline Data for Frontend Table
        timeline = []
        paths = [("Asli", orig_path)]
        if gs_path and os.path.exists(gs_path):
            paths.append(("Ghostscript", gs_path))
        if pike_path and os.path.exists(pike_path):
            paths.append(("PikePDF", pike_path))

        for step_name, p in paths:
            sz_mb = os.path.getsize(p) / 1048576
            a = _analyze_pdf_anatomy(p)
            f_full, f_sub, f_list = _get_font_stats(p)
            timeline.append({
                "step": step_name,
                "size_mb": round(sz_mb, 2),
                "font_mb": round(a["fonts_bytes"] / 1048576, 2),
                "img_mb": round(a["images_bytes"] / 1048576, 2),
                "other_mb": round(a["other_bytes"] / 1048576, 2),
                "full_fonts": f_full,
                "sub_fonts": f_sub,
                "font_list": f_list
            })
        
        data["timeline"] = timeline

        # Save to session for export
        _sessions[token]["evaluation"] = data
        
        return jsonify(data)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

@visual_bp.route("/visual/export_log/<token>", methods=["GET"])
def visual_export_log(token: str):
    import io
    import csv
    from flask import send_file

    if token not in _sessions:
        return jsonify({"error": "Invalid token."}), 404

    s = _sessions[token]
    orig_path = s["orig_path"]
    gs_path = s.get("gs_path")
    pike_path = s.get("pike_path")

    # Prepare paths
    paths = [("1. File Asli", orig_path, "Belum dikompresi")]
    if gs_path and os.path.exists(gs_path):
        paths.append(("2. Ghostscript", gs_path, "Downsampling gambar & Font subsetting"))
    if pike_path and os.path.exists(pike_path):
        paths.append(("3. PikePDF", pike_path, "Pembersihan metadata & object streams"))

    output = io.StringIO()
    writer = csv.writer(output)
    
    # Headers
    writer.writerow([
        "Nama File", "Tipe Dokumen", "Tahap", "Ukuran Total (MB)", 
        "Data Font (MB)", "Jml Font Utuh", "Jml Font Subset", "Daftar Font",
        "Data Gambar (MB)", "Keterangan Proses", "Kualitas SSIM", "Kualitas Teks (%)"
    ])

    eval_data = s.get("evaluation", {})
    ssim_val = f"{eval_data.get('ssim_avg', 'N/A')}"
    text_val = f"{eval_data.get('text_sequence_ratio', 'N/A')}"
    
    filename = s.get("filename", "unknown.pdf")
    doc_class = s.get("class", "HYBRID")

    for step_name, path, desc in paths:
        size_mb = os.path.getsize(path) / 1048576
        anat = _analyze_pdf_anatomy(path)
        f_full, f_sub, f_list = _get_font_stats(path)
        f_names = ", ".join([f["raw"] for f in f_list]) if f_list else "Tidak ada teks/font"
        
        font_mb = anat["fonts_bytes"] / 1048576
        img_mb = anat["images_bytes"] / 1048576
        
        # Only show evaluation metrics on the final stage, or leave it blank
        # But for clarity, we can just put it on all rows or just the final one.
        # It's better to show it everywhere to indicate this file's final quality.
        
        writer.writerow([
            filename,
            doc_class,
            step_name,
            f"{size_mb:.4f}",
            f"{font_mb:.4f}",
            f_full,
            f_sub,
            f_names,
            f"{img_mb:.4f}",
            desc,
            ssim_val,
            text_val
        ])

    mem = io.BytesIO()
    # Add BOM for excel to read UTF-8 properly
    mem.write(b'\xef\xbb\xbf')
    mem.write(output.getvalue().encode('utf-8'))
    mem.seek(0)
    
    stem = s.get("filename", "log").rsplit(".", 1)[0]
    return send_file(
        mem,
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"{stem}_ablation_log.csv"
    )
