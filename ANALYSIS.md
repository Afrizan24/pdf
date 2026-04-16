# PDF Compressor — Analysis & Roadmap

## Overview

This document captures the findings from a full pipeline audit of the Adaptive PDF Compressor,
a comparison against iLovePDF's compression approach, and the prioritized next steps for improvement.

---

## Pipeline Walk (Current Flow)

```
Upload PDF
    │
    ▼
Step 1: Feature Extraction       core/features.py
    │   - page count, file size, text length, image count
    │   - dominant image encoding (CCITT / JBIG2 / JPEG / JPEG2000 / Flate)
    │   - bilevel image ratio
    │   - image area ratio + text area ratio (bounding-box, no pixel decode)
    │   - docs > 100 pages: sampled (30 pages) + extrapolated
    │
    ▼
Step 2: Classification           core/classifier.py
    │   Priority order:
    │   1. bilevel_ratio >= 0.5                          → SCAN
    │   2. JPEG dominant + low text + enough images      → SCAN
    │   3. img_area >= 0.80                              → SCAN
    │   4. text_area >= 0.80 and img_area < 0.20         → DIGITAL
    │   5. character-count heuristics                    → SCAN / DIGITAL / HYBRID
    │
    ▼
Step 3: Pipeline Routing         core/compressor.py → compress()
    │
    ├── SCAN / Bilevel (CCITT, JBIG2)
    │       Pass A  Ghostscript /screen + grayscale
    │       Pass B  pikepdf JBIG2 re-encode (on best candidate)
    │       Pass C  PyMuPDF structural opt  ← only if GS was skipped
    │       → pick smallest
    │
    ├── SCAN / JPEG-based
    │       Pass A  Rasterize all pages at target_dpi as JPEG (parallel, chunked)
    │       Pass B  PyMuPDF structural opt  ← only if rasterized output ≤ 8 MB
    │       → pick smallest
    │
    └── DIGITAL / HYBRID
            Pass A  Ghostscript font subset + image downsample
            Pass B  pikepdf JPEG recompress + JBIG2  ← runs on original
            Pass C  PyMuPDF structural opt            ← only if GS was skipped
            Pass D  pikepdf post-GS squeeze           ← runs on best candidate
            → pick smallest
    │
    ▼
Step 4: Safety Fallback
    │   If all passes > original size → return original unchanged
    │
    ▼
Step 5: Store & Download
        Result stored in-memory (_results dict, 5-min TTL, UUID token)
        Client downloads via GET /compress/download/<token>
```

---

## Issues Found

### Feature Extraction (`core/features.py`)

| # | Issue | Impact |
|---|-------|--------|
| F1 | `avg_text_area_ratio` uses block-level bounding boxes, not glyph coverage. A sparse page with one large text block is over-counted. | Misclassification toward DIGITAL |
| F2 | Image area ratio accumulates raw rect areas without union-clipping overlapping images. Capped at 1.0 but the signal is noisy before the cap. | Misclassification toward SCAN |
| F3 | Large-doc extrapolation (`scale = pages / sample_size`) assumes uniform content distribution. Docs with dense images in the first half and blank pages in the second will misclassify. | Misclassification |

### Classification (`core/classifier.py`)

| # | Issue | Impact |
|---|-------|--------|
| C1 | Rule 2 fires on JPEG-dominant docs with `avg_text < 200`. Threshold is too broad — a HYBRID doc with embedded photos and ~150 chars/page gets routed to SCAN and rasterized, destroying selectable text. | Silent text loss |
| C2 | Area-coverage rules (3 & 4) only produce SCAN or DIGITAL. HYBRID is only reachable via the final heuristic fallback. The classifier never returns HYBRID from a strong signal. | HYBRID under-utilized |
| C3 | No confidence score returned. Caller cannot distinguish a hard rule hit from a weak heuristic guess. | No user feedback on uncertain classification |

### SCAN / Bilevel Pipeline

| # | Issue | Impact |
|---|-------|--------|
| S1 | Pass B (JBIG2) runs on the GS output. GS may have already re-encoded bilevel images differently, reducing JBIG2 effectiveness vs. running on the original. | Sub-optimal JBIG2 compression |
| S2 | Pass C (structural opt) is skipped when GS ran. GS does not pack object streams (`use_objstms`). This leaves 5–15% savings on the table. | Missed compression |

### SCAN / JPEG Rasterization Pipeline

| # | Issue | Impact |
|---|-------|--------|
| R1 | Rasterization destroys all selectable text, hyperlinks, and form fields. No warning is emitted to the user in the SSE stream or result info dict. | Silent data loss |
| R2 | Each worker thread opens a new `fitz.Document` per page. For a 500-page doc with 4 workers, up to 4 simultaneous file handles are opened and closed repeatedly. Slow on network/cloud storage. | Performance |
| R3 | Grayscale vote threshold is 70%. A doc that is 65% grayscale pages renders in color, inflating output size. | Larger output than necessary |
| R4 | Chunk PDFs are saved with `deflate=False`, then merged with `garbage=2, deflate=False`. Structural overhead is not deflated until the optional Pass B, which is skipped for outputs > 8 MB. | Larger output for big scans |

### DIGITAL / HYBRID Pipeline

| # | Issue | Impact |
|---|-------|--------|
| D1 | Pass B always runs on the original input (not GS output). Pass D then runs pikepdf on the best candidate. The two passes do overlapping work — Pass B and Pass D can both re-encode the same images. | Wasted CPU time |
| D2 | Pass C (structural opt) is skipped when GS ran, same as bilevel. Object stream packing is left on the table. | Missed compression |
| D3 | `gs_used` tracking checks if `best_path` matches one of three hardcoded temp paths. Adding a new pass will silently break this flag. | Incorrect reporting |

### Server / API (`routes/compress.py`)

| # | Issue | Impact |
|---|-------|--------|
| A1 | `_results` dict holds raw PDF bytes in memory for up to 5 minutes with no size cap. Under concurrent load this is a memory leak vector. | OOM risk |
| A2 | `_evict_expired()` is only called on new compress/download requests. Expired entries accumulate if traffic stops. | Memory waste |
| A3 | SSE generator uses `time.sleep(0.05)` busy-wait — 20 polls/second per active compression. | Unnecessary CPU usage |
| A4 | Download filename is hardcoded to `compressed.pdf` regardless of the original filename. | Poor UX |

### JBIG2 (`core/jbig2.py`)

| # | Issue | Impact |
|---|-------|--------|
| J1 | `encode_image_jbig2_symbol()` is implemented but never called anywhere. Symbol mode is ~20% smaller than sequential for multi-page bilevel scans with repeated glyphs (typed text on fax). | Missed compression |

---

## iLovePDF — Reference Comparison

### How iLovePDF Works
Three preset levels: **Extreme**, **Recommended**, **Less**. Black-box cloud pipeline — likely
Ghostscript or equivalent with fixed DPI/quality targets per level. No content-aware routing
is exposed to the user.

### Pros
- Dead-simple UX — one click, three levels
- Fast (cloud-side, no local dependencies)
- Handles common cases well at "Recommended"
- No software install required
- Batch processing (paid tier)

### Cons
- No content-aware routing — same algorithm applied to scanned docs and digital text docs alike
- No font subsetting control
- No DPI control — fixed per preset
- Files uploaded to their servers — privacy risk for sensitive documents
- Free tier: ~100 MB file size limit, no API access
- No transparency — no compression report, no breakdown of what changed
- "Extreme" mode can produce visually degraded output with no quality floor guarantee
- No JBIG2 encoding for bilevel/fax scans — significant missed compression for that content type
- No safety fallback — if "compressed" output is larger than input, it still returns the compressed version

### Where This Codebase Already Wins
- Content-aware routing (SCAN / DIGITAL / HYBRID)
- JBIG2 encoding for bilevel images
- Font subsetting via Ghostscript
- Safety fallback that never returns a larger file
- Full compression report in the result info dict
- Local processing — no data leaves the machine

---

## Next Steps (Prioritized)

### P0 — Critical (Silent Data Loss)

**[C1 + R1] Guard rasterization against text-bearing documents**
- Tighten Rule 2 in the classifier: require `avg_text < 50` (not 200) before routing JPEG-dominant docs to SCAN.
- Before rasterizing, check `feats.total_text_len > 500`. If true, emit a `text_loss_warning` event in the SSE stream and add `"text_loss_warning": true` to the result info dict.
- Surface this warning visibly in the UI.

---

### P1 — High (Compression Quality)

**[S2 + D2] Run lightweight structural opt after GS**
- After GS completes, run a restricted PyMuPDF pass: `use_objstms=1`, `deflate=False`, `garbage=1`, `clean=False`.
- This packs object streams that GS leaves unpacked. Cheap (seconds) and typically saves 5–15% on top of GS output.
- Apply to both the bilevel SCAN and DIGITAL/HYBRID pipelines.

**[J1] Wire up JBIG2 symbol mode for multi-page bilevel scans**
- In `pikepdf_recompress`, when `feats.pages > 5` and `is_bilevel`, try `encode_image_jbig2_symbol` first.
- Fall back to sequential mode if symbol encoding fails or produces a larger result.
- Expected gain: ~20% over current sequential JBIG2 on typed-text fax documents.

**[S1] Run JBIG2 pass on original before GS for bilevel SCAN**
- For bilevel SCAN, run Pass B (JBIG2) on the original input in parallel with Pass A (GS), not sequentially on the GS output.
- Add both results to the candidate pool. Let `_best()` decide.

---

### P2 — Medium (Reliability & Reporting)

**[C3] Add classification confidence to the result**
- Return a `classification_confidence` field: `"hard"` (encoding rule hit), `"medium"` (area-coverage rule), or `"weak"` (heuristic fallback).
- Expose this in the SSE `done` event and the UI results table.
- When confidence is `"weak"`, suggest the user try a different mode.

**[D3] Fix `gs_used` tracking**
- Replace the hardcoded path comparison with a boolean flag set inside `_run_pass` when the GS pass succeeds and its output is selected as best.

**[A4] Preserve original filename in download**
- Pass the original filename through `_parse_params()` and store it alongside the result bytes in `_results`.
- Use it in `send_file(download_name=...)`.

---

### P3 — Low (Performance & Stability)

**[A1 + A2] Cap the in-memory result store**
- Add a `_MAX_RESULTS_BYTES = 500 * 1024 * 1024` (500 MB) total cap.
- When the cap is exceeded, evict the oldest entries before storing a new result.
- Alternatively, write results to temp files and stream from disk on download — eliminates the memory pressure entirely.

**[A3] Replace SSE busy-wait with a queue**
- Replace `deque` + `time.sleep(0.05)` with `queue.Queue`.
- The worker thread puts events into the queue; the generator blocks on `queue.get(timeout=1.0)`.
- Eliminates 20 polls/second per active compression.

**[R2] Reuse document handles in rasterization workers**
- Open one `fitz.Document` per worker thread (not per page) using a thread-local store.
- Close handles when the `ThreadPoolExecutor` shuts down.

**[F2] Union-clip image rects in area ratio computation**
- In `_compute_area_ratios`, maintain a list of clipped rects per page and union them before summing area.
- PyMuPDF's `fitz.Rect` supports `|` (union) — use it to avoid double-counting overlapping images.

---

## Summary Table

| ID | Area | Severity | Type | Fix Complexity |
|----|------|----------|------|----------------|
| C1 | Classifier | Critical | Bug | Low |
| R1 | Rasterizer | Critical | Missing feature | Low |
| S2 | Bilevel pipeline | High | Missed compression | Medium |
| D2 | Digital pipeline | High | Missed compression | Medium |
| J1 | JBIG2 | High | Dead code | Low |
| S1 | Bilevel pipeline | High | Ordering issue | Medium |
| C3 | Classifier | Medium | Missing feature | Low |
| D3 | Digital pipeline | Medium | Bug | Low |
| A4 | API | Medium | UX | Low |
| A1 | API | Medium | Stability | Medium |
| A2 | API | Medium | Stability | Low |
| A3 | API | Low | Performance | Low |
| R2 | Rasterizer | Low | Performance | Medium |
| F2 | Features | Low | Accuracy | Medium |
| F1 | Features | Low | Accuracy | High |
| F3 | Features | Low | Accuracy | Medium |
| R3 | Rasterizer | Low | Quality | Low |
| R4 | Rasterizer | Low | Quality | Low |
| D1 | Digital pipeline | Low | Performance | Medium |
