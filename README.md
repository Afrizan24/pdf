# PDF Compression Research Tool

Alat kompresi PDF berbasis web untuk penelitian tugas akhir, dirancang untuk menemukan **sweet spot** parameter kompresi terbaik. Pipeline dua-pass: **Ghostscript** (kompresi utama) + **pikepdf** (optimasi struktural), dilengkapi **evaluasi kualitas** otomatis (PSNR/SSIM) dan **parameter tuning** untuk analisis multi-kombinasi.

## Arsitektur

```
pdf_compression/
├── app.py                  # Flask server entry point
├── pdf.py                  # CLI entry point
├── requirements.txt
├── core/
│   ├── __init__.py         # Public API
│   ├── compressor.py       # Pipeline utama (compress()) + pikepdf structural optimize
│   ├── ghostscript.py      # GS detection + compression pass
│   ├── features.py         # PyMuPDF feature extraction (area ratio, encoding, sampling)
│   ├── classifier.py       # Rule-based SCAN/DIGITAL/HYBRID classifier + confidence
│   ├── evaluator.py        # Quality evaluation (PSNR, SSIM, text integrity)
│   ├── batch.py            # Batch compression + adaptive parameter grid
│   ├── tuning.py           # Parameter tuning sweep + sweet spot analysis
│   └── jbig2.py            # JBIG2 binary detection
├── routes/
│   ├── compress.py         # /preview, /compress/stream (SSE), /compress/download
│   ├── batch.py            # /batch/run (SSE), /batch/csv, /batch/default_grid
│   ├── visual.py           # /visual/* — step-by-step visual compression pipeline
│   └── files.py            # /status
├── templates/
│   ├── index.html          # Research UI — single-file compression
│   ├── batch.html          # Batch compression + parameter grid UI
│   └── visual.html         # Visual step-by-step ablation pipeline UI
├── tests/
│   └── test_tuning_core.py # Unit tests untuk tuning module
└── Dataset_PDF/            # Dataset PDF untuk penelitian
```

## Pipeline Kompresi

```
Input PDF
    │
    ├─ PyMuPDF: ekstrak fitur (halaman, gambar, teks, encoding, area ratio)
    │     sampling-based untuk dokumen > 100 halaman
    │
    ├─ Classifier: SCAN / DIGITAL / HYBRID (+ confidence: hard/medium/weak)
    │     Rule 1: bilevel_ratio >= 0.5           → SCAN (hard)
    │     Rule 2: image_area >= 0.9 & text ≈ 0   → SCAN (hard)
    │     Rule 3: text_area >= 0.8 & image < 0.2  → DIGITAL (medium)
    │     Rule 4: campuran / fallback              → HYBRID (weak)
    │
    ├─ Pass A — Ghostscript
    │     image downsample (color/gray/mono DPI)
    │     font subsetting
    │     stream recompress (JPEG quality auto/manual)
    │     scan-optimized flags (DCTEncode, Bicubic/Subsample)
    │
    └─ Pass B — pikepdf (opsional, pada output GS)
          ObjStm packing (5–15% tambahan)
          metadata strip lanjutan
          recompress_flate (file ≤ 20 MB)
              │
              └─ Output: file terkecil dari semua pass
                         (fallback ke original jika semua pass lebih besar)
```

## Compression Levels

| Level  | GS Preset  | Color DPI | Gray DPI | Mono DPI | Cocok untuk |
|--------|-----------|-----------|----------|----------|-------------|
| HIGH   | /screen   | 72        | 72       | 144      | Ukuran terkecil, kualitas rendah |
| MEDIUM | /ebook    | 150       | 150      | 300      | Seimbang (default) |
| LOW    | /printer  | 300       | 300      | 600      | Kualitas tinggi, ukuran lebih besar |

Semua parameter dapat di-override secara individual untuk penelitian.

## Klasifikasi Dokumen

| Kelas   | Kriteria                                                    | Confidence |
|---------|-------------------------------------------------------------|------------|
| SCAN    | bilevel ratio >= 50%                                        | hard       |
| SCAN    | image area >= 90% AND teks ≈ 0 (< 50 chars/page)           | hard       |
| DIGITAL | text area >= 80% AND image area < 20%                       | medium     |
| HYBRID  | campuran (default fallback)                                 | weak       |

## Evaluasi Kualitas

Modul `core/evaluator.py` membandingkan PDF original vs compressed menggunakan:

| Metrik | Deskripsi | Tipe Dokumen |
|--------|-----------|-------------|
| **PSNR** | Peak Signal-to-Noise Ratio (rasterized per-page) | Semua |
| **SSIM** | Structural Similarity Index (scikit-image atau fallback numpy) | Semua |
| **Text Preserved %** | Rasio jumlah karakter original vs compressed | DIGITAL, HYBRID |
| **Text Sequence Ratio** | SequenceMatcher ratio — deteksi reordering/substitusi | DIGITAL, HYBRID |

### Sweet Spot Thresholds

| SSIM Range | Interpretasi |
|------------|-------------|
| > 0.99     | Low compression — kualitas print/archive |
| > 0.95     | Medium compression — kualitas e-book/screen |
| > 0.90     | High compression — kualitas draft/web |

## Batch Compression

Modul `core/batch.py` menjalankan kompresi multi-file dengan **adaptive parameter grid** berdasarkan tipe dokumen:

| Tipe Dokumen | Strategi Grid | Jumlah Kombinasi |
|-------------|--------------|-----------------|
| SCAN | DPI sweep (36–300) × 3 level | 9 kombinasi |
| DIGITAL | 4 preset GS × pikepdf on/off | 8 kombinasi |
| HYBRID | 3 preset × 3 DPI point | 9 kombinasi |

Hasil diekspor ke CSV dengan metrik kompresi dan kualitas lengkap.

## Parameter Tuning

Modul `core/tuning.py` menyediakan **Cartesian product sweep** untuk pencarian parameter optimal:

- Konfigurasi per-level (HIGH/MEDIUM/LOW) dengan range `{min, max, step}`
- Parameter: `color_dpi`, `gray_dpi`, `mono_dpi`, `jpeg_quality`, `pdf_settings`, `pikepdf_optimize`
- **Sweet spot analysis**: otomatis memilih kombinasi terbaik per level berdasarkan constraint SSIM minimum
- Export hasil ke CSV dengan kolom standar

## Visual Pipeline (Ablation Study)

Halaman `/visual` menyediakan **step-by-step visual compression pipeline** untuk ablation study:

1. **Upload** — unggah PDF, lihat thumbnail dan anatomi file (font/gambar/lainnya dalam MB)
2. **Extract** — ekstraksi fitur PyMuPDF (area ratio, encoding, jumlah teks/gambar)
3. **Classify** — klasifikasi dokumen dengan penjelasan logika dan confidence
4. **Ghostscript** — kompresi Pass A dengan parameter yang dapat dikustomisasi
5. **pikepdf** — optimasi struktural Pass B pada output GS
6. **Evaluate** — evaluasi kualitas PSNR/SSIM/text integrity
7. **Download** — unduh file terkecil + export log ablation ke CSV

Setiap langkah menampilkan: library yang digunakan, parameter yang diterapkan, thumbnail before/after, dan perubahan anatomi file.

## Instalasi

### 1. Python dependencies

```bash
pip install -r requirements.txt
```

**Dependencies opsional untuk evaluasi kualitas:**
```bash
pip install scikit-image numpy
```

### 2. Ghostscript (wajib)

**Windows:**
Download dari https://www.ghostscript.com/releases/gsdnld.html  
Pastikan `gswin64c` tersedia di PATH.

```bash
gswin64c --version
```

**macOS:**
```bash
brew install ghostscript
```

**Linux:**
```bash
sudo apt install ghostscript
```

### 3. Jalankan server

```bash
python app.py
```

Buka browser: **http://127.0.0.1:5000**

### Halaman yang Tersedia

| URL | Deskripsi |
|-----|-----------|
| `/` | Research UI — kompresi single-file dengan parameter kontrol penuh |
| `/visual` | Visual pipeline — ablation study step-by-step |
| Batch | Batch compression via API `/batch/run` |

## CLI Usage

```bash
python pdf.py input.pdf output.pdf [options]

Options:
  --mode {AUTO,DIGITAL,SCAN,HYBRID}   Classification mode (default: AUTO)
  --level {HIGH,MEDIUM,LOW}           Compression level preset (default: MEDIUM)
  --pdf-setting {/screen,/ebook,/printer,/prepress}
                                      Override GS preset
  --color-dpi INT                     Override colour image DPI
  --gray-dpi INT                      Override grayscale image DPI
  --mono-dpi INT                      Override monochrome image DPI
  --jpeg-quality INT                  Override JPEG quality 20-100
  --grayscale                         Convert all colour to grayscale
  --no-pikepdf                        Disable pikepdf Pass B
  --scan-text-th INT                  SCAN text threshold (default: 20)
  --digital-text-th INT               DIGITAL text threshold (default: 200)
  --min-img-scan FLOAT                Min images/page for SCAN (default: 1.0)
  --max-size-gs FLOAT                 Max file size MB for GS (default: 200)
```

**Contoh:**
```bash
# Kompresi agresif
python pdf.py doc.pdf out.pdf --level HIGH

# Override DPI manual untuk penelitian
python pdf.py doc.pdf out.pdf --level MEDIUM --color-dpi 120 --gray-dpi 120

# Tanpa pikepdf pass
python pdf.py doc.pdf out.pdf --level MEDIUM --no-pikepdf

# Grayscale + kualitas tinggi
python pdf.py doc.pdf out.pdf --level LOW --grayscale
```

## API Endpoints

### Single Compression

| Method | Endpoint | Deskripsi |
|--------|----------|-----------|
| POST | `/preview` | Ekstrak fitur PDF tanpa kompresi |
| POST | `/compress/stream` | Kompresi dengan SSE progress streaming |
| GET | `/compress/download/<token>` | Download hasil kompresi (TTL 5 menit) |

### Batch Compression

| Method | Endpoint | Deskripsi |
|--------|----------|-----------|
| POST | `/batch/run` | Batch compression multi-file (SSE) |
| GET | `/batch/csv/<token>` | Download CSV hasil batch (TTL 10 menit) |
| GET | `/batch/default_grid` | Lihat adaptive grid per tipe dokumen |

### Visual Pipeline

| Method | Endpoint | Deskripsi |
|--------|----------|-----------|
| GET | `/visual` | Halaman visual pipeline |
| POST | `/visual/upload` | Upload PDF, dapatkan token + thumbnail |
| POST | `/visual/extract/<token>` | Ekstraksi fitur PyMuPDF |
| POST | `/visual/classify/<token>` | Klasifikasi dokumen |
| POST | `/visual/gs/<token>` | Jalankan Ghostscript compression |
| POST | `/visual/pike/<token>` | Jalankan pikepdf optimization |
| POST | `/visual/evaluate/<token>` | Evaluasi kualitas PSNR/SSIM |
| GET | `/visual/download/<token>` | Download file terkecil |
| GET | `/visual/export_log/<token>` | Export log ablation ke CSV |

## GS Flags yang Digunakan

| Flag | Fungsi |
|------|--------|
| `-dPDFSETTINGS` | Preset kompresi dasar |
| `-dColorImageResolution` | DPI downsample gambar warna |
| `-dGrayImageResolution` | DPI downsample gambar grayscale |
| `-dMonoImageResolution` | DPI downsample gambar bilevel |
| `-dJPEGQ` | Kualitas JPEG eksplisit (auto dari DPI) |
| `-dColorImageFilter=/DCTEncode` | Paksa JPEG untuk gambar warna |
| `-dGrayImageFilter=/DCTEncode` | Paksa JPEG untuk gambar grayscale |
| `-dColorImageDownsampleType=/Bicubic` | Algoritma downsample berkualitas (color/gray) |
| `-dMonoImageDownsampleType=/Subsample` | Subsample untuk bilevel (preservasi tepi) |
| `-dDetectDuplicateImages=true` | Deduplikasi gambar identik |
| `-dCompressFonts=true` | Kompresi font streams |
| `-dSubsetFonts=true` | Subset font (buang glyph yang tidak dipakai) |
| `-dPreserveEPSInfo=false` | Buang EPS metadata |
| `-dPreserveHalftoneInfo=false` | Buang halftone info |
| `-dColorImageDownsampleThreshold=1.0` | Downsample semua gambar tanpa threshold |
| `-sColorConversionStrategy=sRGB` | Normalisasi colorspace ke sRGB |
| `-dAutoFilterColorImages=false` | Nonaktifkan auto-filter (mode SCAN) |

### JPEG Quality Auto-Selection

| DPI Range | SCAN | DIGITAL/HYBRID |
|-----------|------|----------------|
| ≤ 72      | 50   | —              |
| ≤ 100     | 55   | 60             |
| ≤ 150     | 65   | —              |
| ≤ 200     | 75   | 75             |
| > 200     | 75   | 85             |

## Dependencies

| Package | Versi | Fungsi |
|---------|-------|--------|
| Flask | >=3.0 | Web server + SSE streaming |
| PyMuPDF | >=1.24 | Feature extraction, rendering, text extraction |
| Pillow | >=10.0 | Image processing support |
| pikepdf | >=8.0 | Structural PDF optimization (Pass B) |

**Opsional:**

| Package | Versi | Fungsi |
|---------|-------|--------|
| scikit-image | — | Windowed SSIM (lebih akurat dari fallback numpy) |
| numpy | — | Komputasi PSNR/SSIM numerik |

**External tools:**
- **Ghostscript** — wajib untuk kompresi
- **jbig2enc** — opsional, dilaporkan di status jika tersedia

## Testing

```bash
python -m pytest tests/ -v
```

Test module `test_tuning_core.py` mencakup:
- `_make_range()` — edge cases (step=0, min>max, normal)
- `_normalize_pikepdf()` — bool/list conversion
- `generate_grid_from_config()` — grid generation dari konfigurasi tuning
- `compute_sweet_spot()` — sweet spot analysis dengan berbagai skenario SSIM

## Troubleshooting

**Ghostscript tidak ditemukan:**
```
RuntimeError: Ghostscript not found. Install Ghostscript and ensure it is on PATH.
```
Install Ghostscript dan pastikan executable (`gswin64c` / `gs`) ada di PATH.

**File lebih besar setelah kompresi:**
Normal untuk PDF yang sudah sangat teroptimasi. Tool otomatis mengembalikan file original.

**Import error PyMuPDF:**
```bash
pip install --upgrade PyMuPDF
```

**SSIM fallback ke Global SSIM:**
Jika `scikit-image` tidak terinstal, evaluator menggunakan Global SSIM (kurang akurat untuk perbandingan lokal). Install `scikit-image` untuk windowed SSIM.

**Klasifikasi confidence "weak":**
Classifier tidak yakin dengan tipe dokumen. Pertimbangkan untuk set `--mode` secara manual untuk hasil optimal.