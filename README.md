# PDF Compression Research Tool

Alat kompresi PDF berbasis web untuk penelitian, dirancang untuk menemukan sweet spot parameter kompresi terbaik. Pipeline dua-pass: **Ghostscript** (kompresi utama) + **pikepdf** (optimasi struktural).

## Arsitektur

```
pdf_compression/
├── app.py                  # Flask server entry point
├── pdf.py                  # CLI entry point
├── requirements.txt
├── core/
│   ├── __init__.py         # Public API
│   ├── compressor.py       # Pipeline utama (compress())
│   ├── ghostscript.py      # GS detection + compression pass
│   ├── features.py         # PyMuPDF feature extraction
│   ├── classifier.py       # Rule-based SCAN/DIGITAL/HYBRID classifier
│   └── jbig2.py            # JBIG2 binary detection
├── routes/
│   ├── compress.py         # /preview, /compress/stream (SSE), /compress/download
│   └── files.py            # /status
└── templates/
    └── index.html          # Research UI
```

## Pipeline Kompresi

```
Input PDF
    │
    ├─ PyMuPDF: ekstrak fitur (halaman, gambar, teks, encoding)
    ├─ Classifier: SCAN / DIGITAL / HYBRID
    │
    ├─ Pass A — Ghostscript
    │     image downsample (color/gray/mono DPI)
    │     font subsetting
    │     stream recompress (JPEG quality control)
    │     metadata strip
    │
    └─ Pass B — pikepdf (opsional)
          ObjStm packing (5-15% tambahan)
          metadata strip lanjutan
          recompress_flate (file kecil)
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

| Kelas   | Kriteria                                      | GS Preset Auto |
|---------|-----------------------------------------------|----------------|
| SCAN    | bilevel ratio >= 50% atau image area >= 90%   | /screen        |
| DIGITAL | text area >= 80% dan image area < 20%         | /ebook         |
| HYBRID  | campuran (default fallback)                   | /ebook         |

## Instalasi

### 1. Python dependencies

```bash
pip install -r requirements.txt
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

## GS Flags yang Digunakan

Flag utama yang diterapkan (terinspirasi dari minimalpdfcompress):

| Flag | Fungsi |
|------|--------|
| `-dPDFSETTINGS` | Preset kompresi dasar |
| `-dColorImageResolution` | DPI downsample gambar warna |
| `-dGrayImageResolution` | DPI downsample gambar grayscale |
| `-dMonoImageResolution` | DPI downsample gambar bilevel |
| `-dJPEGQ` | Kualitas JPEG eksplisit (auto dari DPI) |
| `-dColorImageFilter=/DCTEncode` | Paksa JPEG untuk gambar warna |
| `-dGrayImageFilter=/DCTEncode` | Paksa JPEG untuk gambar grayscale |
| `-dColorImageDownsampleType=/Bicubic` | Algoritma downsample berkualitas |
| `-dDetectDuplicateImages=true` | Deduplikasi gambar identik |
| `-dCompressFonts=true` | Kompresi font streams |
| `-dPreserveEPSInfo=false` | Buang EPS metadata |
| `-dPreserveHalftoneInfo=false` | Buang halftone info |
| `-dColorImageDownsampleThreshold=1.0` | Downsample semua gambar tanpa threshold |
| `-sColorConversionStrategy=sRGB` | Normalisasi colorspace ke sRGB |

## Dependencies

| Package | Versi | Fungsi |
|---------|-------|--------|
| Flask | >=3.0 | Web server + SSE streaming |
| PyMuPDF | >=1.24 | Feature extraction (halaman, gambar, teks) |
| Pillow | >=10.0 | Image processing support |
| pikepdf | >=8.0 | Structural PDF optimization (Pass B) |

**External tools:**
- **Ghostscript** — wajib untuk kompresi
- **jbig2enc** — opsional, dilaporkan di status jika tersedia

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