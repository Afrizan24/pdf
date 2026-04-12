# Adaptive PDF Compressor — Setup Guide

## Struktur Folder

```
pdf_compressor/
├── app.py              ← Flask server (sudah diupgrade)
├── pdf.py              ← Logic kompresi
├── requirements.txt
├── templates/
│   └── index.html      ← Frontend (terhubung ke backend)
├── uploads/            ← Auto-dibuat saat run
└── outputs/            ← Auto-dibuat saat run
```

## 1. Install Python Dependencies

```bash
pip install -r requirements.txt
```

## 2. Install Ghostscript (untuk DIGITAL/HYBRID mode)

### Windows
Download installer dari: https://www.ghostscript.com/releases/gsdnld.html
Pastikan `gswin64c` tersedia di PATH.

Cek dengan:
```bash
gswin64c --version
```

### macOS
```bash
brew install ghostscript
```
*(Ubah `gswin64c` → `gs` di pdf.py baris pemanggilan subprocess)*

### Linux
```bash
sudo apt install ghostscript
```
*(Ubah `gswin64c` → `gs` di pdf.py)*

## 3. Jalankan Aplikasi

```bash
cd pdf_compressor
python app.py
```

Buka browser: **http://127.0.0.1:5000**

## 4. Cara Pakai

1. Drop file PDF ke area upload
2. Pilih mode (AUTO direkomendasikan)
3. Atur parameter sesuai kebutuhan
4. Klik **Run Compression**
5. Lihat hasil nyata, lalu klik **Download**

## Catatan Mode

| Mode    | Cocok untuk              | Teknik                          |
|---------|--------------------------|----------------------------------|
| AUTO    | Semua jenis PDF          | Otomatis detect tipe             |
| DIGITAL | PDF teks (Word, LaTeX)   | GC + font subsetting (Ghostscript)|
| SCAN    | PDF hasil scan           | Rasterisasi ulang ke JPEG        |
| HYBRID  | Campuran teks + gambar   | GC + font subsetting              |

## Troubleshooting

**Ghostscript tidak ditemukan?**  
Jika Ghostscript belum tersedia, mode DIGITAL/HYBRID tetap jalan — hanya
step structural optimization yang aktif (tanpa font subsetting).

**Import error PyMuPDF?**  
```bash
pip install --upgrade PyMuPDF
```
