# Dokumen Persyaratan

## Pendahuluan

Fitur **Batch Parameter Tuning** adalah alat riset yang memungkinkan pengguna mencari parameter kompresi PDF optimal untuk setiap level kompresi (HIGH, MEDIUM, LOW) menggunakan analisis *sweet spot*. Pengguna mendefinisikan rentang parameter secara manual (misalnya `color_dpi`, `jpeg_quality`, `pdf_setting`, `pikepdf_optimize`), sistem menjalankan sweep parameter secara batch terhadap file PDF sampel, lalu menampilkan grafik interaktif *saving% vs parameter* dan *SSIM vs parameter* untuk membantu menentukan titik optimal di setiap level kompresi.

Fitur ini berbeda dari halaman Batch yang sudah ada: halaman Batch menjalankan grid adaptif per tipe dokumen (SCAN/DIGITAL/HYBRID), sedangkan Batch Parameter Tuning berfokus pada **pencarian parameter per level kompresi** (HIGH/MEDIUM/LOW) dengan patokan sweet spot.

## Glosarium

- **Tuning_Runner**: Komponen backend yang menjalankan sweep parameter dan mengumpulkan hasil metrik.
- **Sweep**: Eksekusi kompresi untuk setiap kombinasi parameter dalam rentang yang ditentukan pengguna.
- **Sweet Spot**: Titik parameter di mana peningkatan kompresi (saving%) mulai melandai atau kualitas (SSIM/PSNR) mulai turun signifikan — titik keseimbangan optimal antara ukuran dan kualitas.
- **Sweet Spot Chart**: Grafik yang memvisualisasikan hubungan antara satu parameter (sumbu X) dan metrik kualitas/kompresi (sumbu Y) untuk mengidentifikasi sweet spot.
- **Param_Form**: Komponen UI formulir input parameter tuning per level kompresi.
- **Chart_Renderer**: Komponen frontend yang merender grafik sweet spot interaktif menggunakan data hasil sweep.
- **Tuning_Result**: Struktur data yang menyimpan hasil satu kombinasi parameter: level, nilai parameter, saving_pct, ssim_avg, psnr_avg, time_ms.
- **Optimal_Recommendation**: Rekomendasi parameter terbaik yang dihasilkan sistem berdasarkan analisis sweet spot.
- **Level Kompresi**: Salah satu dari tiga preset: HIGH (/screen, 72 dpi), MEDIUM (/ebook, 150 dpi), LOW (/printer, 300 dpi).
- **SSIM**: Structural Similarity Index — metrik kualitas gambar (0–1, semakin tinggi semakin baik).
- **PSNR**: Peak Signal-to-Noise Ratio — metrik kualitas gambar dalam dB (semakin tinggi semakin baik).
- **saving_pct**: Persentase pengurangan ukuran file setelah kompresi dibanding ukuran asli.

---

## Persyaratan

### Persyaratan 1: Disclaimer dan Konteks Tujuan

**User Story:** Sebagai peneliti, saya ingin melihat penjelasan tujuan halaman tuning sebelum menggunakannya, agar saya memahami bahwa halaman ini khusus untuk mencari parameter optimal per level kompresi dengan patokan sweet spot — bukan untuk kompresi produksi.

#### Kriteria Penerimaan

1. THE Tuning_Runner SHALL menampilkan disclaimer yang menjelaskan bahwa tujuan halaman ini adalah mencari parameter optimal per level kompresi (HIGH/MEDIUM/LOW) menggunakan analisis sweet spot, bukan untuk kompresi file produksi.
2. THE Param_Form SHALL menampilkan disclaimer sebelum formulir input parameter, sehingga pengguna membacanya sebelum memulai konfigurasi.
3. WHEN pengguna membuka halaman batch parameter tuning, THE Param_Form SHALL menampilkan disclaimer dalam bahasa yang jelas dan tidak dapat disembunyikan.

---

### Persyaratan 2: Upload File PDF Sampel

**User Story:** Sebagai peneliti, saya ingin mengunggah satu atau lebih file PDF sebagai sampel uji, agar sweep parameter dijalankan terhadap file nyata yang representatif.

#### Kriteria Penerimaan

1. THE Param_Form SHALL menyediakan area unggah file yang menerima satu atau lebih file berformat `.pdf`.
2. WHEN pengguna mengunggah file non-PDF, THE Param_Form SHALL menolak file tersebut dan menampilkan pesan kesalahan yang menyebutkan format yang diterima.
3. WHEN pengguna mengunggah file PDF yang melebihi 200 MB, THE Param_Form SHALL menolak file tersebut dan menampilkan pesan kesalahan yang menyebutkan batas ukuran.
4. THE Param_Form SHALL menampilkan nama file dan ukuran setiap file yang berhasil diunggah.
5. THE Param_Form SHALL memungkinkan pengguna menghapus file dari daftar sebelum menjalankan sweep.

---

### Persyaratan 3: Konfigurasi Parameter Tuning per Level Kompresi

**User Story:** Sebagai peneliti, saya ingin mengkonfigurasi rentang dan langkah parameter untuk setiap level kompresi (HIGH, MEDIUM, LOW) secara terpisah, agar saya dapat menentukan ruang pencarian yang relevan untuk tiap level.

#### Kriteria Penerimaan

1. THE Param_Form SHALL menyediakan panel konfigurasi terpisah untuk setiap level kompresi: HIGH, MEDIUM, dan LOW.
2. THE Param_Form SHALL menyediakan input untuk parameter berikut pada setiap panel level: `color_dpi` (nilai min, maks, langkah), `gray_dpi` (nilai min, maks, langkah), `mono_dpi` (nilai min, maks, langkah), `jpeg_quality` (nilai min, maks, langkah), `pdf_setting` (pilihan dari: `/screen`, `/ebook`, `/printer`, `/prepress`), dan `pikepdf_optimize` (aktif/nonaktif).
3. THE Param_Form SHALL menampilkan nilai default yang sesuai dengan preset level: HIGH menggunakan `/screen` 72 dpi, MEDIUM menggunakan `/ebook` 150 dpi, LOW menggunakan `/printer` 300 dpi.
4. WHEN pengguna memasukkan nilai `color_dpi` minimum yang lebih besar dari nilai maksimum, THE Param_Form SHALL menampilkan pesan validasi dan mencegah pengiriman formulir.
5. WHEN pengguna memasukkan nilai `jpeg_quality` di luar rentang 1–100, THE Param_Form SHALL menampilkan pesan validasi dan mencegah pengiriman formulir.
6. WHEN pengguna memasukkan nilai `color_dpi` minimum yang lebih besar dari nilai maksimum, THE Param_Form SHALL menampilkan pesan validasi dan mencegah pengiriman formulir.
7. THE Param_Form SHALL menampilkan estimasi jumlah kombinasi parameter yang akan dijalankan berdasarkan konfigurasi saat ini, sebelum pengguna memulai sweep.
8. THE Param_Form SHALL memungkinkan pengguna mengaktifkan atau menonaktifkan setiap panel level secara individual, sehingga hanya level yang diaktifkan yang diikutsertakan dalam sweep.

---

### Persyaratan 4: Eksekusi Sweep Parameter

**User Story:** Sebagai peneliti, saya ingin menjalankan sweep parameter secara batch terhadap file PDF sampel, agar saya mendapatkan data metrik kompresi dan kualitas untuk setiap kombinasi parameter.

#### Kriteria Penerimaan

1. WHEN pengguna menekan tombol jalankan sweep, THE Tuning_Runner SHALL menghasilkan semua kombinasi parameter dari konfigurasi yang dimasukkan pengguna untuk setiap level yang diaktifkan.
2. THE Tuning_Runner SHALL menjalankan kompresi menggunakan fungsi `compress()` dari `core/compressor.py` untuk setiap kombinasi parameter dan setiap file PDF sampel.
3. THE Tuning_Runner SHALL mengumpulkan metrik berikut untuk setiap kombinasi: `saving_pct`, `ssim_avg`, `psnr_avg`, `time_ms`, nilai parameter yang digunakan, dan nama file.
4. WHEN sweep sedang berjalan, THE Tuning_Runner SHALL mengirimkan pembaruan progres melalui Server-Sent Events (SSE) yang mencakup: langkah saat ini, persentase penyelesaian, dan detail kombinasi yang sedang diproses.
5. WHEN terjadi kesalahan pada satu kombinasi parameter, THE Tuning_Runner SHALL mencatat kesalahan tersebut pada baris hasil yang bersangkutan dan melanjutkan ke kombinasi berikutnya tanpa menghentikan seluruh sweep.
6. WHEN seluruh sweep selesai, THE Tuning_Runner SHALL mengirimkan event `done` yang berisi semua hasil dan token untuk unduhan CSV.
7. THE Tuning_Runner SHALL dapat diakses melalui endpoint `POST /tuning/run` yang menerima file PDF dan konfigurasi parameter dalam format JSON.

---

### Persyaratan 5: Tampilan Progres Sweep

**User Story:** Sebagai peneliti, saya ingin melihat progres sweep secara real-time, agar saya mengetahui berapa banyak kombinasi yang sudah selesai dan berapa yang tersisa.

#### Kriteria Penerimaan

1. WHEN sweep sedang berjalan, THE Param_Form SHALL menampilkan bilah progres yang mencerminkan persentase kombinasi yang telah selesai.
2. WHEN sweep sedang berjalan, THE Param_Form SHALL menampilkan pesan status yang menyebutkan kombinasi saat ini (misalnya `[5/24] file.pdf | HIGH_dpi72`).
3. WHEN sweep selesai, THE Param_Form SHALL memperbarui status menjadi selesai dan menampilkan jumlah total kombinasi yang berhasil.
4. WHEN terjadi kesalahan fatal pada sweep, THE Param_Form SHALL menampilkan pesan kesalahan dan memungkinkan pengguna menjalankan ulang sweep.

---

### Persyaratan 6: Grafik Sweet Spot Interaktif

**User Story:** Sebagai peneliti, saya ingin melihat grafik sweet spot yang menampilkan hubungan antara parameter dan metrik kompresi/kualitas, agar saya dapat mengidentifikasi titik parameter optimal secara visual.

#### Kriteria Penerimaan

1. WHEN sweep selesai, THE Chart_Renderer SHALL menampilkan grafik sweet spot untuk setiap level kompresi yang diaktifkan.
2. THE Chart_Renderer SHALL menampilkan grafik `saving_pct vs parameter` dengan sumbu X adalah nilai parameter yang disweep dan sumbu Y adalah persentase penghematan ukuran.
3. THE Chart_Renderer SHALL menampilkan grafik `SSIM vs parameter` dengan sumbu X adalah nilai parameter yang disweep dan sumbu Y adalah nilai SSIM rata-rata.
4. WHEN data dari beberapa file PDF tersedia, THE Chart_Renderer SHALL menampilkan garis terpisah per file PDF pada grafik yang sama, serta garis rata-rata keseluruhan.
5. THE Chart_Renderer SHALL menandai titik sweet spot yang direkomendasikan pada grafik dengan penanda visual yang berbeda (misalnya titik berwarna merah atau garis vertikal putus-putus).
6. WHEN pengguna mengarahkan kursor ke titik data pada grafik, THE Chart_Renderer SHALL menampilkan tooltip yang berisi nilai parameter, saving_pct, SSIM, dan PSNR untuk titik tersebut.
7. THE Chart_Renderer SHALL memungkinkan pengguna memilih parameter mana yang ditampilkan pada sumbu X melalui kontrol dropdown.
8. THE Chart_Renderer SHALL memungkinkan pengguna memilih level kompresi mana yang ditampilkan melalui kontrol tab atau tombol filter.

---

### Persyaratan 7: Rekomendasi Parameter Optimal

**User Story:** Sebagai peneliti, saya ingin mendapatkan rekomendasi parameter optimal per level kompresi berdasarkan analisis sweet spot, agar saya dapat langsung menggunakan nilai tersebut sebagai preset baru.

#### Kriteria Penerimaan

1. WHEN sweep selesai, THE Tuning_Runner SHALL menghitung rekomendasi parameter optimal untuk setiap level kompresi yang diaktifkan.
2. THE Tuning_Runner SHALL menentukan sweet spot sebagai kombinasi parameter yang memaksimalkan `saving_pct` dengan batasan `ssim_avg` ≥ 0.85.
3. THE Param_Form SHALL menampilkan rekomendasi parameter optimal dalam kartu ringkasan per level, yang mencakup: nilai setiap parameter, `saving_pct` yang dicapai, `ssim_avg` yang dicapai, dan `psnr_avg` yang dicapai.
4. WHEN tidak ada kombinasi parameter yang memenuhi batasan `ssim_avg` ≥ 0.85, THE Tuning_Runner SHALL merekomendasikan kombinasi dengan `ssim_avg` tertinggi dan menampilkan peringatan bahwa batasan kualitas tidak terpenuhi.
5. THE Param_Form SHALL menyediakan tombol salin untuk menyalin nilai parameter rekomendasi dalam format JSON, sehingga pengguna dapat menggunakannya di tempat lain.

---

### Persyaratan 8: Tabel Hasil Sweep

**User Story:** Sebagai peneliti, saya ingin melihat semua hasil sweep dalam tabel yang dapat diurutkan dan difilter, agar saya dapat menganalisis data secara detail.

#### Kriteria Penerimaan

1. WHEN sweep selesai, THE Param_Form SHALL menampilkan tabel hasil yang memuat semua kombinasi parameter beserta metriknya.
2. THE Param_Form SHALL menampilkan kolom berikut pada tabel: nama file, level, label parameter, `color_dpi`, `jpeg_quality`, `pdf_setting`, `pikepdf_optimize`, `saving_pct`, `ssim_avg`, `psnr_avg`, `time_ms`, dan status kesalahan.
3. THE Param_Form SHALL memungkinkan pengguna mengurutkan tabel berdasarkan kolom mana pun dengan mengklik header kolom.
4. THE Param_Form SHALL memungkinkan pengguna memfilter tabel berdasarkan level kompresi (HIGH/MEDIUM/LOW).
5. WHEN baris hasil mengandung kesalahan, THE Param_Form SHALL menampilkan baris tersebut dengan warna berbeda dan menampilkan pesan kesalahan singkat.

---

### Persyaratan 9: Ekspor Hasil

**User Story:** Sebagai peneliti, saya ingin mengekspor semua hasil sweep ke file CSV, agar saya dapat menganalisis data lebih lanjut menggunakan alat eksternal seperti Excel atau Python.

#### Kriteria Penerimaan

1. WHEN sweep selesai, THE Tuning_Runner SHALL menyimpan semua hasil sweep dalam format CSV yang dapat diunduh.
2. THE Param_Form SHALL menampilkan tombol unduh CSV yang aktif setelah sweep selesai.
3. THE Tuning_Runner SHALL menyediakan endpoint `GET /tuning/csv/<token>` yang mengembalikan file CSV dengan header `Content-Disposition: attachment`.
4. THE Tuning_Runner SHALL menyimpan hasil CSV selama minimal 10 menit setelah sweep selesai sebelum dihapus dari memori.
5. WHEN token CSV sudah kedaluwarsa atau tidak valid, THE Tuning_Runner SHALL mengembalikan respons HTTP 404 dengan pesan kesalahan yang jelas.

---

### Persyaratan 10: Integrasi dengan Halaman Batch yang Ada

**User Story:** Sebagai pengguna, saya ingin dapat mengakses halaman Batch Parameter Tuning dari halaman Batch yang sudah ada, agar navigasi antar fitur riset terasa terpadu.

#### Kriteria Penerimaan

1. THE Param_Form SHALL dapat diakses melalui URL `/tuning` sebagai halaman terpisah dari halaman Batch (`/batch`).
2. THE Param_Form SHALL menampilkan tautan navigasi ke halaman Batch (`/batch`) dan halaman utama (`/`).
3. WHEN pengguna berada di halaman Batch (`/batch`), THE Param_Form SHALL menampilkan tautan navigasi ke halaman Batch Parameter Tuning (`/tuning`).
