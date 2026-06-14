# Dokumen Desain: Batch Parameter Tuning

## Overview

Fitur **Batch Parameter Tuning** menambahkan halaman riset baru (`/tuning`) ke aplikasi Flask kompresi PDF. Halaman ini memungkinkan peneliti mendefinisikan rentang parameter secara manual per level kompresi (HIGH/MEDIUM/LOW), menjalankan sweep parameter secara batch terhadap file PDF sampel, lalu menganalisis hasilnya melalui grafik sweet spot interaktif dan tabel sortable.

Berbeda dari halaman Batch yang sudah ada (yang menggunakan grid adaptif per tipe dokumen), halaman Tuning berfokus pada **pencarian parameter per level kompresi** dengan patokan sweet spot: kombinasi parameter yang memaksimalkan `saving_pct` dengan batasan `ssim_avg ≥ 0.85`.

### Tujuan Desain

1. **Reuse maksimal** — memanfaatkan `compress()` dari `core/compressor.py` dan `evaluate()` dari `core/evaluator.py` yang sudah ada, serta pola SSE streaming dari `routes/batch.py`.
2. **Konsistensi UI** — mengikuti design system yang sudah ada di `templates/batch.html` (CSS variables, komponen, dark mode).
3. **Modularitas** — logika tuning dipisahkan ke `core/tuning.py` dan route ke `routes/tuning.py`, tidak mengubah kode yang sudah ada kecuali registrasi blueprint dan nav link.

---

## Architecture

```mermaid
graph TD
    Browser["Browser\n/tuning"] -->|POST /tuning/run\nmultipart/form-data| TuningRoute["routes/tuning.py\ntuning_bp"]
    TuningRoute -->|SSE stream| Browser
    TuningRoute -->|generate_grid_from_config| TuningCore["core/tuning.py"]
    TuningCore -->|run_tuning| TuningCore
    TuningCore -->|compress()| Compressor["core/compressor.py"]
    TuningCore -->|evaluate()| Evaluator["core/evaluator.py"]
    TuningCore -->|compute_sweet_spot| TuningCore
    TuningRoute -->|GET /tuning/csv/<token>| Browser
    Browser -->|Chart.js| Charts["Sweet Spot Charts\n(saving% vs param\nSSIM vs param)"]
    AppPy["app.py"] -->|register_blueprint| TuningRoute
    BatchHTML["templates/batch.html"] -->|nav link| TuningHTML["templates/tuning.html"]
```

### Alur Eksekusi

1. Pengguna membuka `/tuning`, mengisi form konfigurasi parameter per level, mengunggah PDF sampel.
2. Browser mengirim `POST /tuning/run` dengan file PDF dan konfigurasi JSON.
3. Server menjalankan `generate_grid_from_config()` untuk menghasilkan semua kombinasi parameter.
4. Untuk setiap kombinasi × setiap file, server memanggil `compress()` lalu `evaluate()`.
5. Progres dikirim via SSE ke browser secara real-time.
6. Setelah selesai, server mengirim event `done` berisi semua hasil, token CSV, dan rekomendasi sweet spot.
7. Browser merender grafik Chart.js, tabel hasil, dan kartu rekomendasi.

---

## Components and Interfaces

### `core/tuning.py`

Modul inti yang berisi tiga fungsi utama:

```python
def generate_grid_from_config(config: Dict) -> List[Dict]:
    """
    Menghasilkan semua kombinasi parameter dari konfigurasi pengguna.

    config format:
    {
        "HIGH": {
            "enabled": True,
            "color_dpi": {"min": 36, "max": 100, "step": 18},
            "gray_dpi":  {"min": 36, "max": 100, "step": 18},
            "mono_dpi":  {"min": 72, "max": 200, "step": 36},
            "jpeg_quality": {"min": 40, "max": 80, "step": 20},
            "pdf_settings": ["/screen"],
            "pikepdf_optimize": True   # bool atau [True, False]
        },
        "MEDIUM": { ... },
        "LOW": { ... }
    }

    Returns list of param dicts, each with:
    {
        "level": "HIGH",
        "pdf_setting": "/screen",
        "color_dpi": 72,
        "gray_dpi": 72,
        "mono_dpi": 144,
        "jpeg_quality": 60,
        "pikepdf_optimize": True,
        "label": "HIGH_screen_cdpi72_jq60_pike1"
    }
    """

def run_tuning(
    pdf_paths: List[str],
    config: Dict,
    evaluate_quality: bool = True,
    progress_cb: Optional[Callable[[str, int, str], None]] = None,
) -> List[Dict]:
    """
    Menjalankan sweep parameter untuk semua file × semua kombinasi.
    Memanggil compress() dan evaluate() dari modul yang sudah ada.
    Mengembalikan list of result dicts (format sama dengan run_one() di batch.py).
    """

def compute_sweet_spot(results: List[Dict], ssim_threshold: float = 0.85) -> Dict[str, Dict]:
    """
    Menghitung rekomendasi parameter optimal per level kompresi.

    Algoritma:
    1. Filter hasil yang memiliki ssim_avg >= ssim_threshold
    2. Dari hasil yang lolos filter, pilih yang memiliki saving_pct tertinggi
    3. Jika tidak ada yang lolos filter, pilih yang memiliki ssim_avg tertinggi
       dan tandai dengan warning

    Returns:
    {
        "HIGH": {
            "params": {...},
            "saving_pct": 45.2,
            "ssim_avg": 0.91,
            "psnr_avg": 32.5,
            "constraint_met": True,
            "warning": None
        },
        "MEDIUM": { ... },
        "LOW": { ... }
    }
    """
```

### `routes/tuning.py`

Flask Blueprint dengan dua endpoint:

```python
tuning_bp = Blueprint("tuning", __name__)

@tuning_bp.route("/tuning/run", methods=["POST"])
def tuning_run():
    """
    Menerima file PDF + konfigurasi JSON.
    Streaming SSE: progress events + done event dengan hasil dan token.
    """

@tuning_bp.route("/tuning/csv/<token>")
def tuning_csv(token: str):
    """
    Mengembalikan file CSV hasil sweep.
    Token valid selama 10 menit (TTL = 600 detik).
    """
```

### `templates/tuning.html`

Halaman UI dengan komponen:
- **Topbar** — navigasi ke `/`, `/batch`, dark mode toggle
- **Disclaimer** — pesan tujuan riset (tidak dapat disembunyikan)
- **Upload Zone** — drag-and-drop multi-file PDF
- **Config Panels** — tiga panel (HIGH/MEDIUM/LOW) dengan toggle aktif/nonaktif
- **Combo Estimator** — estimasi jumlah kombinasi real-time
- **Run Button** — tombol jalankan sweep
- **Progress Section** — progress bar + log SSE
- **Sweet Spot Charts** — dua grafik Chart.js per level (saving% vs param, SSIM vs param)
- **Recommendation Cards** — kartu rekomendasi per level dengan tombol salin JSON
- **Results Table** — tabel sortable/filterable dengan semua kolom
- **Download Button** — unduh CSV

### Modifikasi File yang Ada

**`app.py`** — tambah import dan registrasi blueprint:
```python
from routes.tuning import tuning_bp
app.register_blueprint(tuning_bp)

@app.route("/tuning")
def tuning():
    return render_template("tuning.html")
```

**`templates/batch.html`** — tambah nav link ke `/tuning` di topbar.

---

## Data Models

### TuningConfig (input dari form)

```python
{
    "HIGH": {
        "enabled": bool,
        "color_dpi": {"min": int, "max": int, "step": int},
        "gray_dpi":  {"min": int, "max": int, "step": int},
        "mono_dpi":  {"min": int, "max": int, "step": int},
        "jpeg_quality": {"min": int, "max": int, "step": int},
        "pdf_settings": List[str],   # subset dari ["/screen", "/ebook", "/printer", "/prepress"]
        "pikepdf_optimize": bool | List[bool]
    },
    "MEDIUM": { ... },
    "LOW": { ... }
}
```

### TuningResult (satu baris hasil)

```python
{
    # Identifikasi
    "filename":              str,
    "level":                 str,   # "HIGH" | "MEDIUM" | "LOW"
    "param_label":           str,   # e.g. "HIGH_screen_cdpi72_jq60_pike1"
    # Parameter yang digunakan
    "pdf_setting":           str,
    "color_dpi":             int,
    "gray_dpi":              int,
    "mono_dpi":              int,
    "jpeg_quality":          int | None,
    "pikepdf_optimize":      bool,
    # Metrik kompresi
    "original_size_bytes":   int,
    "compressed_size_bytes": int,
    "saving_pct":            float,
    "ratio":                 float,
    "time_ms":               float,
    # Metrik kualitas
    "ssim_avg":              float | None,
    "ssim_min":              float | None,
    "psnr_avg":              float | None,
    "psnr_min":              float | None,
    "text_preserved_pct":    float | None,
    "text_sequence_ratio":   float | None,
    "pages_match":           bool | None,
    # Status
    "error":                 str | None,
}
```

### SweetSpotResult (output compute_sweet_spot)

```python
{
    "HIGH": {
        "params": {
            "pdf_setting": str,
            "color_dpi": int,
            "gray_dpi": int,
            "mono_dpi": int,
            "jpeg_quality": int | None,
            "pikepdf_optimize": bool,
        },
        "saving_pct":    float,
        "ssim_avg":      float,
        "psnr_avg":      float | None,
        "constraint_met": bool,   # True jika ssim_avg >= 0.85
        "warning":       str | None,
    },
    "MEDIUM": { ... },
    "LOW": { ... }
}
```

### SSE Event Format

```
event: progress
data: {"step": "tuning", "pct": 42, "detail": "[10/24] file.pdf | HIGH_screen_cdpi72"}

event: done
data: {
    "token": "uuid-string",
    "count": 24,
    "rows": [...],
    "sweet_spot": {...},
    "summary": {...}
}

event: error
data: {"message": "error description"}
```

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Grid Generation Completeness

*For any* konfigurasi tuning yang valid dengan parameter (min, max, step) untuk setiap dimensi dan level yang diaktifkan, jumlah kombinasi yang dihasilkan oleh `generate_grid_from_config()` harus sama dengan produk kartesian dari semua nilai parameter yang mungkin di semua level yang diaktifkan.

**Validates: Requirements 4.1**

---

### Property 2: Non-PDF File Rejection

*For any* file dengan ekstensi bukan `.pdf` (misalnya `.txt`, `.docx`, `.jpg`, `.exe`), sistem harus menolak file tersebut dan tidak memasukkannya ke dalam daftar file yang akan diproses.

**Validates: Requirements 2.2**

---

### Property 3: Result Completeness

*For any* kombinasi parameter yang berhasil dijalankan, baris hasil yang dikembalikan oleh `run_tuning()` harus mengandung semua field metrik yang diperlukan: `saving_pct`, `ssim_avg`, `psnr_avg`, `time_ms`, `level`, `param_label`, `filename`, dan semua nilai parameter yang digunakan.

**Validates: Requirements 4.3**

---

### Property 4: Error Isolation

*For any* kombinasi parameter yang menghasilkan exception selama kompresi atau evaluasi, baris hasil yang bersangkutan harus mengandung field `error` yang tidak kosong, dan sweep harus melanjutkan ke kombinasi berikutnya tanpa menghentikan seluruh proses.

**Validates: Requirements 4.5**

---

### Property 5: Sweet Spot Optimality (Constraint Met)

*For any* kumpulan hasil tuning di mana setidaknya satu kombinasi memiliki `ssim_avg ≥ 0.85`, sweet spot yang dipilih oleh `compute_sweet_spot()` harus memiliki `saving_pct` tertinggi di antara semua kombinasi yang memenuhi batasan `ssim_avg ≥ 0.85`.

**Validates: Requirements 7.2**

---

### Property 6: Sweet Spot Fallback (Constraint Not Met)

*For any* kumpulan hasil tuning di mana semua kombinasi memiliki `ssim_avg < 0.85`, sweet spot yang dipilih oleh `compute_sweet_spot()` harus memiliki `ssim_avg` tertinggi di antara semua kombinasi yang tersedia, dan field `constraint_met` harus bernilai `False`.

**Validates: Requirements 7.4**

---

### Property 7: CSV Round-Trip

*For any* kumpulan hasil tuning yang valid, serialisasi ke CSV menggunakan `results_to_csv()` lalu parsing kembali menggunakan `csv.DictReader` harus menghasilkan data yang setara dengan data asli (semua field dan nilai terjaga).

**Validates: Requirements 9.1**

---

### Property 8: Invalid Token Returns 404

*For any* token yang tidak ada dalam penyimpanan hasil (token tidak valid atau sudah kedaluwarsa), endpoint `GET /tuning/csv/<token>` harus mengembalikan respons HTTP 404.

**Validates: Requirements 9.5**

---

### Property 9: Table Sort Correctness

*For any* kolom sortable dan arah pengurutan (ascending/descending), fungsi pengurutan tabel harus menghasilkan baris dalam urutan yang benar berdasarkan nilai kolom tersebut — nilai numerik diurutkan secara numerik, nilai string diurutkan secara leksikografis.

**Validates: Requirements 8.3**

---

### Property 10: Table Filter Correctness

*For any* level filter yang dipilih (HIGH, MEDIUM, atau LOW), semua baris yang ditampilkan dalam tabel harus memiliki nilai `level` yang sama dengan filter yang dipilih, dan tidak ada baris dari level lain yang ditampilkan.

**Validates: Requirements 8.4**

---

### Property 11: DPI Range Validation

*For any* pasangan nilai (min, max) untuk parameter DPI (`color_dpi`, `gray_dpi`, `mono_dpi`) di mana `min > max`, validasi form harus gagal dan pengiriman form harus dicegah.

**Validates: Requirements 3.4, 3.6**

---

### Property 12: JPEG Quality Range Validation

*For any* nilai `jpeg_quality` yang berada di luar rentang [1, 100] (yaitu nilai < 1 atau > 100), validasi form harus gagal dan pengiriman form harus dicegah.

**Validates: Requirements 3.5**

---

### Property 13: File Display After Upload

*For any* file PDF yang valid yang berhasil diunggah ke form, nama file dan ukurannya harus ditampilkan dalam daftar file di UI.

**Validates: Requirements 2.4**

---

### Property 14: File Removal from List

*For any* daftar file yang berisi N file, menghapus satu file harus menghasilkan daftar dengan N-1 file, dan file yang dihapus tidak boleh ada lagi dalam daftar.

**Validates: Requirements 2.5**

---

## Error Handling

### Validasi Input (Frontend)

| Kondisi | Penanganan |
|---|---|
| File non-PDF diunggah | Tolak file, tampilkan pesan error format |
| File > 200 MB | Tolak file, tampilkan pesan error ukuran |
| `color_dpi` min > max | Tampilkan pesan validasi, cegah submit |
| `jpeg_quality` di luar [1, 100] | Tampilkan pesan validasi, cegah submit |
| Tidak ada file yang diunggah | Nonaktifkan tombol Run |
| Tidak ada level yang diaktifkan | Nonaktifkan tombol Run, tampilkan hint |

### Error Handling Backend

| Kondisi | Penanganan |
|---|---|
| Kompresi gagal untuk satu kombinasi | Catat error di field `error`, lanjutkan sweep |
| Evaluasi kualitas gagal | Catat error di field `error`, tetap simpan metrik kompresi |
| File PDF tidak dapat dibaca | Catat error, lanjutkan ke file berikutnya |
| Token CSV tidak valid/kedaluwarsa | Kembalikan HTTP 404 dengan pesan JSON |
| Tidak ada file PDF yang valid | Kembalikan HTTP 400 dengan pesan JSON |
| Konfigurasi JSON tidak valid | Kembalikan HTTP 400 dengan pesan JSON |
| Grid kosong (semua level dinonaktifkan) | Kembalikan HTTP 400 dengan pesan JSON |

### Strategi Fallback

- Jika semua kombinasi gagal untuk satu file, file tersebut tetap muncul di tabel dengan semua baris berisi error.
- Jika `ssim_avg` tidak tersedia (evaluasi dinonaktifkan atau gagal), sweet spot dihitung hanya berdasarkan `saving_pct` tanpa batasan SSIM, dengan peringatan yang ditampilkan.
- Jika tidak ada kombinasi yang memenuhi `ssim_avg ≥ 0.85`, fallback ke kombinasi dengan `ssim_avg` tertinggi (lihat Property 6).

---

## Testing Strategy

### Unit Tests

Unit test berfokus pada fungsi-fungsi murni di `core/tuning.py`:

- **`generate_grid_from_config()`** — verifikasi kelengkapan grid, label yang benar, nilai default
- **`compute_sweet_spot()`** — verifikasi pemilihan sweet spot dengan berbagai skenario (constraint met, constraint not met, hasil kosong)
- **Validasi form** — verifikasi logika validasi parameter (min/max, rentang jpeg_quality)
- **Pengurutan tabel** — verifikasi fungsi sort dengan berbagai kolom dan arah
- **Filter tabel** — verifikasi fungsi filter dengan berbagai level

### Property-Based Tests

Library yang digunakan: **Hypothesis** (Python) untuk backend, **fast-check** (JavaScript) untuk frontend jika diperlukan.

Setiap property test dikonfigurasi untuk minimum **100 iterasi**.

Tag format: `# Feature: batch-parameter-tuning, Property {N}: {property_text}`

| Property | Implementasi |
|---|---|
| Property 1: Grid Generation Completeness | Generate konfigurasi acak, verifikasi `len(grid) == product(len(range(min,max,step)) for each param)` |
| Property 2: Non-PDF File Rejection | Generate nama file acak dengan ekstensi non-.pdf, verifikasi ditolak |
| Property 3: Result Completeness | Generate kombinasi acak dengan mock compress/evaluate, verifikasi semua field ada |
| Property 4: Error Isolation | Inject exception pada kombinasi acak, verifikasi sweep berlanjut |
| Property 5: Sweet Spot Optimality | Generate hasil acak dengan beberapa ssim_avg >= 0.85, verifikasi sweet spot memiliki saving_pct tertinggi |
| Property 6: Sweet Spot Fallback | Generate hasil acak dengan semua ssim_avg < 0.85, verifikasi fallback ke ssim_avg tertinggi |
| Property 7: CSV Round-Trip | Generate hasil acak, serialisasi ke CSV, parse kembali, verifikasi kesetaraan |
| Property 8: Invalid Token Returns 404 | Generate token acak yang tidak ada di store, verifikasi 404 |
| Property 9: Table Sort Correctness | Generate data tabel acak, sort berdasarkan kolom acak, verifikasi urutan |
| Property 10: Table Filter Correctness | Generate data tabel acak, filter berdasarkan level acak, verifikasi semua baris sesuai |
| Property 11: DPI Range Validation | Generate pasangan (min, max) acak di mana min > max, verifikasi validasi gagal |
| Property 12: JPEG Quality Range Validation | Generate nilai jpeg_quality acak di luar [1, 100], verifikasi validasi gagal |
| Property 13: File Display After Upload | Generate file PDF acak, verifikasi nama dan ukuran ditampilkan |
| Property 14: File Removal from List | Generate daftar file acak, hapus satu, verifikasi daftar berkurang 1 |

### Integration Tests

- **SSE Streaming** — verifikasi bahwa event `progress` dan `done` dikirim dengan format yang benar
- **Endpoint `/tuning/run`** — verifikasi respons dengan file PDF nyata (menggunakan file kecil dari `Dataset_PDF/`)
- **Endpoint `/tuning/csv/<token>`** — verifikasi unduhan CSV dengan token valid dan tidak valid
- **Navigasi** — verifikasi tautan navigasi antara `/`, `/batch`, dan `/tuning`

### Smoke Tests

- Halaman `/tuning` dapat diakses dan menampilkan disclaimer
- Semua elemen form ada (upload zone, panel HIGH/MEDIUM/LOW, tombol run)
- Endpoint `/tuning/run` merespons (tidak 404)
- Endpoint `/tuning/csv/<token>` merespons 404 untuk token tidak valid
