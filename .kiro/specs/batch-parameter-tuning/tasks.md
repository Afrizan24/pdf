# Implementation Plan: Batch Parameter Tuning

## Overview

Implementasi fitur Batch Parameter Tuning dalam urutan: backend logic (`core/tuning.py`) → Flask routes + SSE (`routes/tuning.py`) → integrasi app (`app.py` + `batch.html`) → UI lengkap (`templates/tuning.html`) → property-based tests (`tests/test_tuning.py`). Setiap langkah dibangun di atas langkah sebelumnya dan langsung terintegrasi ke aplikasi yang sudah ada.

## Tasks

- [x] 1. Implementasi `core/tuning.py` — logika inti tuning
  - [x] 1.1 Implementasi `generate_grid_from_config(config: Dict) -> List[Dict]`
    - Buat file `core/tuning.py` baru
    - Implementasi fungsi yang mengiterasi setiap level (HIGH/MEDIUM/LOW) yang `enabled: True`
    - Untuk setiap level, generate produk kartesian dari semua nilai parameter: `range(min, max+step, step)` untuk DPI dan jpeg_quality, list untuk `pdf_settings`, bool/list untuk `pikepdf_optimize`
    - Setiap kombinasi menghasilkan dict dengan field: `level`, `pdf_setting`, `color_dpi`, `gray_dpi`, `mono_dpi`, `jpeg_quality`, `pikepdf_optimize`, `label`
    - Format label: `{LEVEL}_{setting}_{cdpi}{color_dpi}_jq{jpeg_quality}_pike{0|1}`
    - _Requirements: 4.1_

  - [x] 1.2 Tulis property test untuk `generate_grid_from_config()`
    - **Property 1: Grid Generation Completeness**
    - Generate konfigurasi acak dengan Hypothesis (`st.integers`, `st.booleans`, `st.lists`), verifikasi `len(grid) == product(len(values) for each param dimension)`
    - **Validates: Requirements 4.1**

  - [x] 1.3 Implementasi `run_tuning(pdf_paths, config, evaluate_quality, progress_cb) -> List[Dict]`
    - Panggil `generate_grid_from_config(config)` untuk mendapatkan grid
    - Untuk setiap file × setiap kombinasi, panggil `compress()` dari `core/compressor.py` lalu `evaluate()` dari `core/evaluator.py`
    - Tangkap exception per kombinasi: isi field `error`, lanjutkan ke kombinasi berikutnya
    - Kirim progress via `progress_cb("tuning", pct, f"[{done}/{total}] {fname} | {label}")`
    - Return list of `TuningResult` dicts (format sesuai design: `filename`, `level`, `param_label`, semua field parameter, semua field metrik)
    - _Requirements: 4.2, 4.3, 4.4, 4.5, 4.6_

  - [x] 1.4 Tulis property test untuk error isolation di `run_tuning()`
    - **Property 4: Error Isolation**
    - Inject exception pada kombinasi acak menggunakan `unittest.mock.patch`, verifikasi sweep berlanjut dan baris error memiliki field `error` yang tidak kosong
    - **Validates: Requirements 4.5**

  - [x] 1.5 Tulis property test untuk result completeness di `run_tuning()`
    - **Property 3: Result Completeness**
    - Mock `compress()` dan `evaluate()` dengan return value acak, verifikasi setiap baris hasil mengandung semua field wajib: `saving_pct`, `ssim_avg`, `psnr_avg`, `time_ms`, `level`, `param_label`, `filename`, dan semua nilai parameter
    - **Validates: Requirements 4.3**

  - [x] 1.6 Implementasi `compute_sweet_spot(results, ssim_threshold=0.85) -> Dict[str, Dict]`
    - Filter hasil per level yang memiliki `ssim_avg >= ssim_threshold`
    - Dari hasil yang lolos filter, pilih yang memiliki `saving_pct` tertinggi → `constraint_met: True`
    - Jika tidak ada yang lolos filter, pilih yang memiliki `ssim_avg` tertinggi → `constraint_met: False`, isi field `warning`
    - Return dict per level: `{"params": {...}, "saving_pct": float, "ssim_avg": float, "psnr_avg": float|None, "constraint_met": bool, "warning": str|None}`
    - _Requirements: 7.1, 7.2, 7.4_

  - [x] 1.7 Tulis property test untuk `compute_sweet_spot()` — constraint met
    - **Property 5: Sweet Spot Optimality (Constraint Met)**
    - Generate hasil acak di mana setidaknya satu kombinasi memiliki `ssim_avg >= 0.85`, verifikasi sweet spot memiliki `saving_pct` tertinggi di antara semua yang memenuhi constraint
    - **Validates: Requirements 7.2**

  - [x] 1.8 Tulis property test untuk `compute_sweet_spot()` — constraint not met
    - **Property 6: Sweet Spot Fallback (Constraint Not Met)**
    - Generate hasil acak di mana semua kombinasi memiliki `ssim_avg < 0.85`, verifikasi sweet spot memiliki `ssim_avg` tertinggi dan `constraint_met == False`
    - **Validates: Requirements 7.4**

  - [x] 1.9 Implementasi `results_to_csv(results: List[Dict]) -> str`
    - Reuse pola dari `core/batch.py` (`csv.DictWriter` ke `io.StringIO`)
    - Pastikan semua field `TuningResult` masuk sebagai kolom CSV
    - _Requirements: 9.1_

  - [x] 1.10 Tulis property test untuk `results_to_csv()` — CSV round-trip
    - **Property 7: CSV Round-Trip**
    - Generate hasil acak, serialisasi ke CSV, parse kembali dengan `csv.DictReader`, verifikasi semua field dan nilai setara dengan data asli
    - **Validates: Requirements 9.1**

- [-] 2. Checkpoint — Pastikan semua tests pass
  - Jalankan `pytest tests/test_tuning.py -v` dan pastikan semua property tests di task 1 lulus.
  - Ensure all tests pass, ask the user if questions arise.

- [~] 3. Implementasi `routes/tuning.py` — Flask Blueprint + SSE
  - [~] 3.1 Buat file `routes/tuning.py` dengan Blueprint `tuning_bp`
    - Salin pola dari `routes/batch.py`: in-memory result store (`_tuning_results`), TTL 600 detik, `_evict_expired()`
    - Implementasi endpoint `POST /tuning/run`:
      - Terima file PDF (`request.files.getlist("pdfs")`) dan konfigurasi JSON (`request.form.get("tuning_config")`)
      - Validasi: tidak ada file → 400, tidak ada file PDF valid → 400, config JSON tidak valid → 400, grid kosong → 400
      - Simpan file ke `tempfile.mkdtemp()`, jalankan `run_tuning()` di thread terpisah via `queue.Queue`
      - Stream SSE: event `progress` selama sweep, event `done` dengan `token`, `count`, `rows`, `sweet_spot`, `summary` saat selesai, event `error` jika exception
      - Setelah selesai, simpan CSV ke `_tuning_results[token]` dengan TTL
    - _Requirements: 4.7, 9.3, 9.4_

  - [~] 3.2 Implementasi endpoint `GET /tuning/csv/<token>`
    - Panggil `_evict_expired()`, cari token di `_tuning_results`
    - Jika tidak ada → return 404 JSON `{"error": "Token invalid or expired."}`
    - Jika ada → return file CSV dengan `send_file()`, `Content-Disposition: attachment`, `download_name="tuning_results.csv"`
    - _Requirements: 9.3, 9.4, 9.5_

  - [~] 3.3 Tulis property test untuk invalid token → 404
    - **Property 8: Invalid Token Returns 404**
    - Generate token UUID acak yang tidak ada di store, verifikasi endpoint mengembalikan HTTP 404
    - **Validates: Requirements 9.5**

  - [~] 3.4 Tulis property test untuk non-PDF file rejection
    - **Property 2: Non-PDF File Rejection**
    - Generate nama file acak dengan ekstensi non-`.pdf` (`.txt`, `.docx`, `.jpg`, dll.), verifikasi file ditolak dan tidak masuk ke daftar yang diproses
    - **Validates: Requirements 2.2**

- [~] 4. Integrasi ke `app.py` dan `templates/batch.html`
  - [~] 4.1 Modifikasi `app.py` — register blueprint dan tambah route `/tuning`
    - Tambah `from routes.tuning import tuning_bp`
    - Tambah `app.register_blueprint(tuning_bp)`
    - Tambah route `@app.route("/tuning")` yang me-render `tuning.html`
    - _Requirements: 10.1_

  - [~] 4.2 Modifikasi `templates/batch.html` — tambah nav link ke `/tuning`
    - Di topbar, tambah `<a class="nav-link" href="/tuning">Parameter Tuning</a>` di antara nav links yang sudah ada
    - Ikuti style `.nav-link` yang sudah ada
    - _Requirements: 10.3_

- [~] 5. Implementasi `templates/tuning.html` — UI lengkap
  - [~] 5.1 Buat struktur dasar HTML dengan topbar dan disclaimer
    - Salin CSS variables, font imports, dan komponen dasar dari `templates/batch.html` (dark mode, topbar, nav links)
    - Tambah nav links ke `/` dan `/batch`
    - Implementasi disclaimer section yang tidak dapat disembunyikan (tidak ada tombol close/hide)
    - _Requirements: 1.1, 1.2, 1.3, 10.2_

  - [~] 5.2 Implementasi Upload Zone
    - Drag-and-drop multi-file PDF (salin pola dari `batch.html`)
    - Validasi client-side: tolak non-PDF (tampilkan pesan error), tolak file > 200 MB (tampilkan pesan error)
    - Tampilkan daftar file dengan nama, ukuran, dan tombol hapus
    - Nonaktifkan tombol Run jika tidak ada file
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [~] 5.3 Implementasi Config Panels (HIGH/MEDIUM/LOW)
    - Tiga panel collapsible, masing-masing dengan toggle aktif/nonaktif
    - Setiap panel berisi input: `color_dpi` (min/max/step), `gray_dpi` (min/max/step), `mono_dpi` (min/max/step), `jpeg_quality` (min/max/step), `pdf_settings` (multi-checkbox), `pikepdf_optimize` (toggle)
    - Nilai default sesuai preset: HIGH → `/screen` 72 dpi, MEDIUM → `/ebook` 150 dpi, LOW → `/printer` 300 dpi
    - Validasi real-time: min > max → tampilkan pesan validasi merah, cegah submit
    - Validasi `jpeg_quality` di luar [1, 100] → tampilkan pesan validasi, cegah submit
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.8_

  - [~] 5.4 Tulis property test untuk DPI range validation (frontend logic)
    - **Property 11: DPI Range Validation**
    - Implementasi fungsi validasi `validateDpiRange(min, max)` yang dapat diuji secara terpisah, verifikasi dengan Hypothesis bahwa pasangan (min > max) selalu menghasilkan validasi gagal
    - **Validates: Requirements 3.4, 3.6**

  - [~] 5.5 Tulis property test untuk JPEG quality range validation (frontend logic)
    - **Property 12: JPEG Quality Range Validation**
    - Implementasi fungsi validasi `validateJpegQuality(value)` yang dapat diuji secara terpisah, verifikasi dengan Hypothesis bahwa nilai di luar [1, 100] selalu menghasilkan validasi gagal
    - **Validates: Requirements 3.5**

  - [~] 5.6 Implementasi Combo Estimator dan Run Button
    - Hitung estimasi kombinasi secara real-time saat input berubah: `product(len(range(min, max+step, step)) for each param) × len(pdf_settings) × len(pikepdf_values)` per level yang aktif, jumlahkan semua level
    - Tampilkan estimasi di bawah config panels
    - Nonaktifkan tombol Run jika tidak ada level yang diaktifkan (tampilkan hint)
    - _Requirements: 3.7, 3.8_

  - [~] 5.7 Implementasi Progress Section
    - Progress bar + persentase (update dari SSE event `progress`)
    - Log SSE real-time (scrollable, dengan tombol clear)
    - Status pill: RUNNING (biru) → DONE (hijau) / ERROR (merah)
    - _Requirements: 5.1, 5.2, 5.3, 5.4_

  - [~] 5.8 Implementasi Sweet Spot Charts dengan Chart.js
    - Load Chart.js dari CDN
    - Setelah event `done`, render dua grafik per level yang aktif: `saving_pct vs parameter` dan `SSIM vs parameter`
    - Sumbu X: nilai parameter yang disweep (default: `color_dpi`); dropdown untuk memilih parameter lain
    - Garis terpisah per file PDF + garis rata-rata keseluruhan
    - Tandai titik sweet spot dengan penanda visual berbeda (titik merah / garis vertikal putus-putus)
    - Tooltip: nilai parameter, saving_pct, SSIM, PSNR
    - Tab/tombol filter untuk memilih level yang ditampilkan
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8_

  - [~] 5.9 Implementasi Recommendation Cards
    - Satu kartu per level yang aktif, tampilkan: nilai setiap parameter, `saving_pct`, `ssim_avg`, `psnr_avg`
    - Jika `constraint_met: False`, tampilkan warning badge
    - Tombol salin JSON: salin `params` dict ke clipboard dalam format JSON
    - _Requirements: 7.1, 7.3, 7.4, 7.5_

  - [~] 5.10 Implementasi Results Table
    - Kolom: filename, level, param_label, color_dpi, jpeg_quality, pdf_setting, pikepdf_optimize, saving_pct, ssim_avg, psnr_avg, time_ms, error
    - Sort by column (klik header): numerik untuk angka, leksikografis untuk string
    - Filter by level (HIGH/MEDIUM/LOW) via tombol filter
    - Baris error ditampilkan dengan warna berbeda (merah muted)
    - Tombol Download CSV (aktif setelah sweep selesai)
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 9.2_

  - [~] 5.11 Tulis property test untuk table sort correctness
    - **Property 9: Table Sort Correctness**
    - Implementasi fungsi sort `sortTableData(data, col, dir)` yang dapat diuji secara terpisah, verifikasi dengan Hypothesis bahwa hasil selalu terurut benar untuk kolom dan arah acak
    - **Validates: Requirements 8.3**

  - [~] 5.12 Tulis property test untuk table filter correctness
    - **Property 10: Table Filter Correctness**
    - Implementasi fungsi filter `filterTableData(data, level)` yang dapat diuji secara terpisah, verifikasi dengan Hypothesis bahwa semua baris hasil memiliki `level` yang sesuai filter
    - **Validates: Requirements 8.4**

  - [~] 5.13 Tulis property test untuk file display after upload
    - **Property 13: File Display After Upload**
    - Verifikasi bahwa setiap file PDF yang ditambahkan ke `uploadedFiles` array muncul dalam rendered file list dengan nama dan ukuran yang benar
    - **Validates: Requirements 2.4**

  - [~] 5.14 Tulis property test untuk file removal from list
    - **Property 14: File Removal from List**
    - Generate daftar file acak dengan N item, hapus satu file acak, verifikasi daftar memiliki N-1 item dan file yang dihapus tidak ada lagi
    - **Validates: Requirements 2.5**

- [~] 6. Implementasi `tests/test_tuning.py` — property-based tests dengan Hypothesis
  - [~] 6.1 Setup file test dan strategi Hypothesis
    - Buat `tests/test_tuning.py`
    - Import Hypothesis (`given`, `strategies as st`, `settings`)
    - Definisikan strategi komposit: `st_tuning_config()`, `st_tuning_result()`, `st_result_list()`
    - Konfigurasi `settings(max_examples=100)` untuk semua property tests
    - Tambah tag format: `# Feature: batch-parameter-tuning, Property {N}: {property_text}`

  - [~] 6.2 Implementasi semua property tests dari task 1 dan 3 dalam satu file
    - Konsolidasikan semua property tests yang sudah ditulis di task 1.2, 1.4, 1.5, 1.7, 1.8, 1.10, 3.3, 3.4 ke dalam `tests/test_tuning.py`
    - Pastikan setiap test memiliki tag komentar yang benar
    - Pastikan semua test dapat dijalankan dengan `pytest tests/test_tuning.py`
    - _Requirements: 4.1, 4.3, 4.5, 7.2, 7.4, 9.1, 9.5, 2.2_

  - [~] 6.3 Implementasi property tests untuk validasi logika Python (Properties 9–12)
    - Ekstrak logika sort dan filter dari JavaScript ke fungsi Python yang dapat diuji (atau implementasi ulang sebagai fungsi Python murni di `core/tuning.py`)
    - Implementasi property tests untuk Property 9 (sort correctness), Property 10 (filter correctness), Property 11 (DPI range validation), Property 12 (JPEG quality range validation)
    - _Requirements: 8.3, 8.4, 3.4, 3.5_

- [~] 7. Checkpoint Final — Pastikan semua tests pass dan integrasi berjalan
  - Jalankan `pytest tests/test_tuning.py -v` dan pastikan semua property tests lulus.
  - Verifikasi `app.py` dapat diimport tanpa error (`python -c "import app"`).
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks bertanda `*` bersifat opsional dan dapat dilewati untuk MVP yang lebih cepat
- Setiap task mereferensikan persyaratan spesifik untuk keterlacakan
- Property tests menggunakan Hypothesis dengan minimum 100 iterasi per property
- Pola SSE streaming dan in-memory result store mengikuti implementasi di `routes/batch.py`
- Fungsi `compress()` dan `evaluate()` dari modul yang sudah ada digunakan langsung tanpa modifikasi
- CSS variables dan komponen UI diwarisi dari `templates/batch.html` untuk konsistensi
