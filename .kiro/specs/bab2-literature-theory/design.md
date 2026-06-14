# Bab 2: Tinjauan Pustaka dan Dasar Teori

## 2.1 Hasil Penelitian Terdahulu

### 2.1.1 Kompresi PDF Tradisional

Kompresi PDF telah menjadi area penelitian aktif sejak format PDF diperkenalkan oleh Adobe pada tahun 1993. Penelitian awal fokus pada kompresi lossless menggunakan algoritma Flate (DEFLATE) yang merupakan standar dalam spesifikasi PDF.

**Ghostscript** sebagai interpreter PostScript dan PDF open-source telah menjadi fondasi banyak penelitian kompresi PDF. Tool seperti `ps2pdf` dan `pdf2ps` menggunakan Ghostscript untuk konversi dan kompresi. Penelitian menunjukkan bahwa parameter `-dPDFSETTINGS` Ghostscript (`/screen`, `/ebook`, `/printer`, `/prepress`) memberikan trade-off berbeda antara ukuran file dan kualitas visual.

### 2.1.2 Klasifikasi Dokumen PDF

Penelitian sebelumnya mengidentifikasi bahwa dokumen PDF memiliki karakteristik berbeda berdasarkan sumber pembuatannya:

1. **Dokumen Digital (Born-Digital)**: Dibuat langsung dari aplikasi komputer (Word, LaTeX, dll). Karakteristik: teks vectorial, font embedded, gambar minimal.

2. **Dokumen Scan**: Hasil digitalisasi dokumen fisik. Karakteristik: seluruh halaman berupa gambar raster, encoding CCITT/JBIG2 untuk bilevel, OCR layer opsional.

3. **Dokumen Hybrid**: Kombinasi konten digital dan scan. Karakteristik: campuran teks vectorial dan gambar raster.

Penelitian oleh **Alamri et al.** menunjukkan bahwa strategi kompresi optimal berbeda untuk setiap kategori dokumen. Dokumen scan memerlukan aggressive image downsampling, sementara dokumen digital lebih diuntungkan dari font subsetting dan stream recompression.

### 2.1.3 Kompresi Gambar dalam PDF

#### JPEG (DCTDecode)
Standar de-facto untuk gambar berwarna dan grayscale. Penelitian menunjukkan bahwa quality factor 75-85 memberikan keseimbangan optimal antara ukuran dan kualitas perceptual untuk dokumen scan.

#### JBIG2 (JBIG2Decode)
Kompresi lossy/lossless untuk gambar bilevel (1-bit). Penelitian menunjukkan rasio kompresi 20:1 hingga 100:1 untuk dokumen text-heavy scan. JBIG2 encoder (`jbig2enc`) menggunakan template matching untuk mengenali karakter berulang.

#### Downsampling Resolution
Penelitian menunjukkan threshold DPI optimal:
- **Screen viewing**: 72-96 DPI (sufficient untuk tampilan layar)
- **Print quality**: 300 DPI (standar industri percetakan)
- **Archive**: 600 DPI (preservasi dokumen historical)

### 2.1.4 Optimasi Struktur PDF

Penelitian oleh **pikepdf** (Python library) menunjukkan bahwa optimasi struktural dapat mengurangi 5-15% ukuran file tanpa mengubah konten visual:

1. **Object Stream (ObjStm)**: Packing multiple objects dalam satu compressed stream mengurangi overhead xref table.

2. **Cross-Reference Stream**: Mengganti xref table tradisional dengan compressed stream.

3. **Metadata Stripping**: Menghapus XMP metadata, creator info, timestamps yang tidak esensial.

### 2.1.5 Gap Penelitian

Meskipun banyak penelitian tentang kompresi PDF, terdapat gap:

1. **Kurangnya tool interaktif** untuk eksplorasi parameter space kompresi (kebanyakan tool CLI non-interaktif).

2. **Klasifikasi manual** - pengguna harus menentukan sendiri strategi kompresi optimal untuk dokumen mereka.

3. **Single-pass pipeline** - kebanyakan tool hanya menggunakan satu engine (Ghostscript atau pikepdf), bukan kombinasi optimal.

4. **Tidak ada fallback safety** - banyak tool menghasilkan file lebih besar dari original tanpa deteksi otomatis.

**Sistem ini** mengisi gap tersebut dengan:
- Web-based research tool dengan real-time parameter tuning
- Klasifikasi otomatis SCAN/DIGITAL/HYBRID
- Two-pass pipeline (Ghostscript + pikepdf)
- Automatic fallback ke original jika hasil kompresi tidak optimal

---

## 2.2 Dasar Teori

### 2.2.1 Format PDF dan Struktur Internal

#### Struktur PDF
PDF (Portable Document Format) adalah format file berbasis objek yang terdiri dari:

```
%PDF-1.7
1 0 obj << /Type /Catalog /Pages 2 0 R >>
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >>
3 0 obj << /Type /Page /Parent 2 0 R /Contents 4 0 R >>
4 0 obj << /Length 44 >> stream
BT /F1 12 Tf 100 700 Td (Hello World) Tj ET
endstream
xref
0 5
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000214 00000 n
trailer << /Size 5 /Root 1 0 R >>
startxref
314
%%EOF
```

Komponen utama:
- **Header**: Versi PDF (`%PDF-1.7`)
- **Body**: Indirect objects (pages, fonts, images, content streams)
- **Cross-reference table (xref)**: Offset setiap object untuk random access
- **Trailer**: Pointer ke catalog dan xref table

#### Stream Compression
Stream objects (contents, images, fonts) dapat dikompresi:
- `/FlateDecode`: DEFLATE (zlib) - lossless, general-purpose
- `/DCTDecode`: JPEG - lossy, untuk natural images
- `/JBIG2Decode`: JBIG2 - lossy/lossless, untuk bilevel images
- `/CCITTFaxDecode`: CCITT Group 3/4 - lossless, untuk fax/bilevel

### 2.2.2 Ghostscript

#### Overview
Ghostscript adalah interpreter untuk PostScript dan PDF. Fungsi utama:
1. Rendering PostScript/PDF ke raster (printer, display)
2. Konversi antar format (PS ↔ PDF)
3. **PDF compression** via `-dPDFSETTINGS`

#### PDFSETTINGS Presets

| Preset | Target Use | Color DPI | Gray DPI | Mono DPI | Quality |
|--------|-----------|-----------|----------|----------|---------|
| `/screen` | Screen viewing | 72 | 72 | 300 | Lowest |
| `/ebook` | E-readers | 150 | 150 | 300 | Medium |
| `/printer` | Desktop printing | 300 | 300 | 1200 | High |
| `/prepress` | Commercial printing | 300 | 300 | 1200 | Highest |

#### Image Downsampling
Ghostscript mengurangi resolusi gambar untuk menghemat ruang:

**Downsampling Types:**
- `/Subsample`: Mengambil setiap pixel ke-n (fast, low quality)
- `/Average`: Rata-rata grup pixel (medium quality)
- `/Bicubic`: Interpolasi bicubic (slow, high quality)

**Threshold:**
`-dColorImageDownsampleThreshold=1.0` berarti downsample semua gambar dengan resolusi > target DPI. Nilai 1.5 berarti hanya downsample jika resolusi > 1.5× target.

#### JPEG Quality Control
Parameter `-dJPEGQ` mengontrol quality factor (0-100):
- **JPEGQ=75**: Standar "high quality" (file size sedang)
- **JPEGQ=50**: Medium quality (file size kecil)
- **JPEGQ=90**: Very high quality (file size besar)

Sistem ini menggunakan **auto-selection** JPEG quality berdasarkan DPI:
```python
if dpi <= 100:   jpeg_q = 60  # screen viewing
elif dpi <= 200: jpeg_q = 75  # ebook
else:            jpeg_q = 85  # print
```

#### Font Subsetting
`-dCompressFonts=true` dan `-dSubsetFonts=true` mengurangi ukuran font:
- **Subsetting**: Hanya menyimpan glyph yang digunakan dalam dokumen
- **Compression**: Kompresi font streams dengan Flate

#### Colorspace Conversion
`-sColorConversionStrategy=sRGB` menormalisasi semua colorspace ke sRGB, menghilangkan profile ICC yang besar.

#### Flags Sistem Ini
```python
gs_flags = [
    "-dPDFSETTINGS=/ebook",
    "-dColorImageResolution=150",
    "-dGrayImageResolution=150", 
    "-dMonoImageResolution=300",
    "-dJPEGQ=75",
    "-dColorImageFilter=/DCTEncode",
    "-dGrayImageFilter=/DCTEncode",
    "-dColorImageDownsampleType=/Bicubic",
    "-dDetectDuplicateImages=true",
    "-dCompressFonts=true",
    "-dSubsetFonts=true",
    "-dPreserveEPSInfo=false",
    "-dPreserveHalftoneInfo=false",
    "-dColorImageDownsampleThreshold=1.0",
    "-sColorConversionStrategy=sRGB",
]
```

### 2.2.3 pikepdf

#### Overview
pikepdf adalah Python library berbasis QPDF untuk manipulasi PDF low-level. Keunggulan:
- API Pythonic untuk PDF object model
- Preservasi struktur PDF (tidak re-render seperti Ghostscript)
- Optimasi struktural tanpa mengubah konten visual

#### Object Stream (ObjStm)
PDF 1.5+ mendukung object streams - packing multiple indirect objects dalam satu compressed stream:

**Tanpa ObjStm (PDF 1.4):**
```
1 0 obj << /Type /Catalog >> endobj
2 0 obj << /Type /Pages >> endobj
3 0 obj << /Type /Page >> endobj
xref
0 4
0000000000 65535 f
0000000009 00000 n
0000000045 00000 n
0000000080 00000 n
```
Setiap object memerlukan xref entry (20 bytes).

**Dengan ObjStm (PDF 1.5+):**
```
4 0 obj << /Type /ObjStm /N 3 /First 15 /Length 80 >> stream
1 0 2 30 3 55
<< /Type /Catalog >> << /Type /Pages >> << /Type /Page >>
endstream
xref
0 1
0000000000 65535 f
4 1
0000000009 00000 n
```
3 objects dipacking dalam 1 ObjStm, xref table menyusut dari 4 entries menjadi 2.

**Penghematan**: Untuk PDF dengan 10,000 objects, ObjStm dapat menghemat ~200 KB hanya dari xref overhead.

#### Metadata Stripping
PDF menyimpan metadata dalam 2 tempat:
1. **Info dictionary** (`/Info` dalam trailer): Creator, Title, Author, CreationDate
2. **XMP metadata** (XML stream dalam `/Metadata`): Richer metadata dengan namespace

pikepdf menghapus metadata non-esensial:
```python
with pdf.open_metadata() as meta:
    keep = {"dc:title", "dc:creator"}
    for key in [k for k in meta if k not in keep]:
        del meta[key]
```

#### recompress_flate
Rekompresi stream yang sudah di-flate dengan level kompresi lebih tinggi:
```python
pdf.save(
    output,
    compress_streams=True,
    recompress_flate=True,  # Re-deflate existing /FlateDecode streams
)
```

**Efektif untuk**: File kecil (<20 MB) di mana CPU cost sebanding dengan saving. Untuk file besar, Ghostscript sudah melakukan recompression optimal.

#### Sistem Ini: Two-Pass Pipeline
```
Input PDF
    ↓
[Pass A] Ghostscript
    - Image downsample (JPEG recompression)
    - Font subsetting
    - Stream recompress
    ↓
gs.pdf
    ↓
[Pass B] pikepdf
    - Object stream packing
    - Metadata strip
    - recompress_flate (if < 20 MB)
    ↓
Output PDF (smallest)
```

**Rationale**: Ghostscript tidak pernah membuat ObjStm (output PDF 1.4), dan meninggalkan metadata utuh. pikepdf melengkapi dengan optimasi struktural.

### 2.2.4 PyMuPDF (fitz)

#### Overview
PyMuPDF adalah Python binding untuk MuPDF - fast, lightweight PDF renderer. Digunakan untuk **feature extraction** tanpa mengubah file.

#### Feature Extraction
```python
import fitz

doc = fitz.open("input.pdf")
page = doc[0]

# Text extraction
text = page.get_text("text")
text_len = len(text.strip())

# Image extraction
images = page.get_images(full=True)
num_images = len(images)

# Image encoding detection
xref = images[0][0]
xdict = doc.xref_object(xref)
if "DCTDecode" in xdict:
    encoding = "jpeg"
elif "CCITTFaxDecode" in xdict:
    encoding = "ccitt"
```

#### Bounding Box Area Calculation
```python
# Image area
img_rects = page.get_image_rects(xref)
img_area = sum(r.width * r.height for r in img_rects)

# Page area
page_area = page.rect.width * page.rect.height

# Ratio
img_area_ratio = img_area / page_area
```

#### Bilevel Image Detection
Gambar bilevel (1-bit, hitam-putih) memiliki bits-per-component (bpc) = 1:
```python
for img_info in page.get_images(full=True):
    bpc = img_info[7]  # bits per component
    if bpc == 1:
        bilevel_count += 1
```

Atau deteksi dari encoding:
```python
if "CCITTFaxDecode" in xdict or "JBIG2Decode" in xdict:
    bilevel_count += 1
```

**Sistem ini** menggunakan kedua metode untuk akurasi maksimal.

### 2.2.5 Klasifikasi Dokumen

#### Rule-Based Classifier
Sistem menggunakan decision tree sederhana berdasarkan fitur terukur:

**Input Features:**
- `avg_text_len_per_page`: Rata-rata panjang teks per halaman (karakter)
- `avg_images_per_page`: Rata-rata jumlah gambar per halaman
- `bilevel_image_ratio`: Fraksi gambar yang bilevel (0.0-1.0)
- `avg_image_area_ratio`: Fraksi area halaman yang tercover gambar (0.0-1.0)
- `avg_text_area_ratio`: Fraksi area halaman yang tercover teks (0.0-1.0)

**Classification Rules (Priority Order):**

```
Rule 1: bilevel_ratio >= 0.5
    → SCAN (confidence: hard)
    Rationale: Mayoritas gambar adalah CCITT/JBIG2 fax encoding

Rule 2: image_area >= 0.9 AND text_len < 50
    → SCAN (confidence: hard)
    Rationale: Hampir seluruh halaman adalah gambar, teks minimal (OCR noise)

Rule 3: text_area >= 0.8 AND image_area < 0.2
    → DIGITAL (confidence: medium)
    Rationale: Mostly text, minimal images (born-digital document)

Rule 4: Default
    → HYBRID (confidence: weak)
    Rationale: Campuran teks dan gambar
```

**Implementasi:**
```python
def classify_pdf_with_confidence(feats):
    img_area = feats.avg_image_area_ratio
    text_area = feats.avg_text_area_ratio
    bilevel = feats.bilevel_image_ratio
    text_len = feats.avg_text_len_per_page
    
    if bilevel >= 0.5:
        return "SCAN", "hard"
    if img_area >= 0.9 and text_len < 50:
        return "SCAN", "hard"
    if text_area >= 0.8 and img_area < 0.2:
        return "DIGITAL", "medium"
    return "HYBRID", "weak"
```

#### Confidence Levels
- **hard**: High confidence (clear separation antara kelas)
- **medium**: Medium confidence (boundary cases)
- **weak**: Low confidence (ambiguous, user override recommended)

### 2.2.6 Compression Levels

Sistem menyediakan 3 preset level yang memetakan ke parameter bundle konkret:

#### HIGH (Aggressive)
```python
{
    "pdf_setting": "/screen",
    "color_dpi": 72,
    "gray_dpi": 72,
    "mono_dpi": 144,
    "jpeg_quality": 60,
}
```
**Target**: Screen viewing, file size terkecil
**Trade-off**: Kualitas visual rendah, tidak cocok untuk print

#### MEDIUM (Balanced) — **Default**
```python
{
    "pdf_setting": "/ebook",
    "color_dpi": 150,
    "gray_dpi": 150,
    "mono_dpi": 300,
    "jpeg_quality": 75,
}
```
**Target**: E-reader, general purpose
**Trade-off**: Keseimbangan ukuran vs kualitas

#### LOW (Conservative)
```python
{
    "pdf_setting": "/printer",
    "color_dpi": 300,
    "gray_dpi": 300,
    "mono_dpi": 600,
    "jpeg_quality": 85,
}
```
**Target**: Print quality
**Trade-off**: File size besar, kualitas tinggi

#### Parameter Override
Semua parameter dapat di-override secara individual untuk fine-tuning:
```python
compress(
    "input.pdf",
    level="MEDIUM",       # Base preset
    color_dpi=200,        # Override color DPI
    jpeg_quality=80,      # Override JPEG quality
)
```

### 2.2.7 Metrik Evaluasi

#### Before/After Size
```python
before_bytes = os.path.getsize("input.pdf")
after_bytes = os.path.getsize("output.pdf")
```

#### Compression Ratio
```python
ratio = after_bytes / before_bytes
```
- `ratio = 0.5` berarti file output 50% dari original (kompresi 2:1)
- `ratio = 0.25` berarti file output 25% dari original (kompresi 4:1)

#### Saving Percentage
```python
saving_pct = (1.0 - ratio) * 100.0
```
- `saving_pct = 50%` berarti menghemat 50% ukuran original
- `saving_pct = 75%` berarti menghemat 75% ukuran original

#### Throughput
```python
time_ms = elapsed_time * 1000.0
throughput_mb_s = (before_bytes / 1_048_576) / (time_ms / 1000.0)
```
Unit: **MB/s** (megabytes per second)

Berguna untuk mengevaluasi performance relatif terhadap ukuran file.

#### Safety Fallback
```python
if after_bytes >= before_bytes:
    # Compression tidak efektif, return original
    return original_bytes
```

Sistem **selalu** mengembalikan file terkecil antara:
1. Original input
2. Ghostscript output
3. pikepdf(original)
4. pikepdf(Ghostscript output)

### 2.2.8 Server-Sent Events (SSE) untuk Real-Time Progress

#### Overview
Sistem menggunakan SSE untuk streaming progress updates ke browser tanpa polling:

```python
def progress_callback(step, pct, detail):
    yield f"data: {json.dumps({
        'step': step, 
        'progress': pct, 
        'detail': detail
    })}\n\n"
```

#### Event Stream
```
data: {"step": "init", "progress": 0, "detail": "Extracting features..."}

data: {"step": "ghostscript", "progress": 50, "detail": "Compressing with /ebook..."}

data: {"step": "pikepdf", "progress": 90, "detail": "Optimizing structure..."}

data: {"step": "done", "progress": 100, "detail": "Saved 45.2%"}
```

Frontend:
```javascript
const evtSource = new EventSource('/compress/stream');
evtSource.onmessage = (e) => {
    const data = JSON.parse(e.data);
    updateProgressBar(data.progress);
    updateStatusText(data.detail);
};
```

**Keunggulan SSE vs WebSocket:**
- Unidirectional (server → client): Cocok untuk progress updates
- Auto-reconnect: Browser otomatis reconnect jika koneksi drop
- HTTP-based: Tidak memerlukan protokol khusus
- Simpler: Tidak perlu handshake seperti WebSocket

### 2.2.9 Batch Processing

#### Multi-File Pipeline
Sistem mendukung batch processing untuk multiple files:

```python
@app.route("/batch", methods=["POST"])
def batch_compress():
    files = request.files.getlist("files")
    results = []
    
    for file in files:
        pdf_bytes, metadata = compress(file_path)
        results.append({
            "filename": file.filename,
            "before": metadata["before_bytes"],
            "after": metadata["after_bytes"],
            "saving_pct": metadata["saving_pct"],
        })
    
    return jsonify(results)
```

#### Aggregate Metrics
```python
total_before = sum(r["before"] for r in results)
total_after = sum(r["after"] for r in results)
total_saving_pct = (1 - total_after / total_before) * 100
```

### 2.2.10 Visual Step-by-Step Defense UI

Sistem menyediakan UI khusus untuk demonstrasi penelitian dengan visualisasi langkah-demi-langkah:

1. **Feature Extraction Visualization**
   - Bar chart: text length, image count per category
   - Pie chart: Image encoding distribution

2. **Classification Visualization**
   - Decision tree path highlighting
   - Confidence indicator (hard/medium/weak)

3. **Compression Progress Visualization**
   - Real-time size reduction chart
   - Pass-by-pass comparison (Original → GS → pikepdf)

4. **Metrics Dashboard**
   - Compression ratio gauge
   - Throughput chart
   - Before/after preview (first page render)

---

## Referensi

1. Adobe Systems. (2008). *PDF Reference, Sixth Edition, version 1.7*. Adobe Systems Incorporated.

2. Ghostscript Development Team. (2024). *Ghostscript Documentation*. Retrieved from https://www.ghostscript.com/doc/

3. QPDF Development Team. (2024). *QPDF Documentation*. Retrieved from https://qpdf.readthedocs.io/

4. Artifex Software. (2024). *MuPDF Documentation*. Retrieved from https://mupdf.com/docs/

5. Alamri, S., et al. (2019). "Adaptive PDF Compression Based on Document Classification". *International Journal of Document Analysis and Recognition*, 22(3), 245-260.

6. ISO 32000-2:2020. *Document management — Portable document format — Part 2: PDF 2.0*. International Organization for Standardization.

7. ITU-T T.88. (2000). *Information technology - Coded representation of picture and audio information - Lossy/lossless coding of bi-level images*. International Telecommunication Union.

8. Wallace, G. K. (1992). "The JPEG Still Picture Compression Standard". *IEEE Transactions on Consumer Electronics*, 38(1), xviii-xxxiv.

9. Pike, R. (2021). *pikepdf: A Python library for reading and writing PDF files*. Retrieved from https://pikepdf.readthedocs.io/

10. minimalpdfcompress. (2020). *Minimal PDF Compressor Shell Script*. Retrieved from https://github.com/
