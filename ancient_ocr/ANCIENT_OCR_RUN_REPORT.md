# Ancient OCR Run Report

Date: 2026-07-28

## Scope

Ancient corpus OCR was completed on the server at:
`/data2/lxj/projects/tcm-burn-rag`

Primary ancient OCR workspace:
`/data2/lxj/projects/tcm-burn-rag/ancient_ocr`

Raw ancient PDFs:
`/data2/lxj/projects/tcm-burn-rag/corpus/ancient_pdf/raw_flat`

## Inputs Verified

- Source books: 12
- Total pages: 5624
- OCR pages: 2788
- Native-text pages: 2836
- Local-to-server file verification: 12/12 filenames, sizes, page counts, and SHA-256 all matched

## OCR Configuration

- OCR engine: `PaddleOCR 3.7.0`
- Detection model: `PP-OCRv6_medium_det`
- Recognition model: `PP-OCRv6_medium_rec`
- Render DPI: 240
- Devices used: `gpu:0`, `gpu:1` on 2 x RTX 4090
- Ancient page unit: 1 PDF page = 1 page record
- Reading-order postprocess:
  vertical pages sorted right-to-left, top-to-bottom

## Output Artifacts

- Page JSON directory:
  `/data2/lxj/projects/tcm-burn-rag/ancient_ocr/output/pages`
- Page JSONL:
  `/data2/lxj/projects/tcm-burn-rag/ancient_ocr/data/pages.jsonl`
- Ancient SQLite + FTS5 database:
  `/data2/lxj/projects/tcm-burn-rag/ancient_ocr/data/ancient_rag.db`
- Inventory manifest:
  `/data2/lxj/projects/tcm-burn-rag/ancient_ocr/data/books.jsonl`
- Finalize report:
  `/data2/lxj/projects/tcm-burn-rag/ancient_ocr/data/finalize_report.json`

## Final Validation

- `completed_page_count = 5624`
- `missing_page_count = 0`
- `invalid_page_count = 0`
- `database_counts.books = 12`
- `database_counts.pages = 5624`
- `database_counts.fts_rows = 5624`
- `database_quick_check = ok`
- `healthy = true`

## Native Text Cleanup

One native-text cleanup pass was applied after the first full run.

Change:
- removed literal `\\x` noise during native-text normalization

Result:
- pages containing literal `\\x` before cleanup: 2713
- pages containing literal `\\x` after cleanup: 0

This rerun refreshed only native-text pages and skipped all existing OCR pages.

## Low-Confidence Summary

Total low-confidence pages: 282

By book:

- `外科正宗_公开扫描版.pdf`: 168
- `古今医统大全_SSID_卷78-79.pdf`: 36
- `06_医宗金鉴_可检索版_汤火伤.pdf`: 40
- `医学心悟_公开扫描版.pdf`: 11
- `02_疡医大全_卷三十七_汤泼火伤门.pdf`: 10
- `04_备急千金要方_卷七十七至八十_火疮.pdf`: 6
- `03_证治准绳_卷八十_汤火疮.pdf`: 4
- `09_本草纲目_卷十八_忍冬.pdf`: 4
- `08_本草纲目_卷十二_甘草.pdf`: 2
- `01_普济方_卷二百七十七_汤火疮.pdf`: 1
- `05_外台秘要方_卷二十九至三十_汤火灼疮.pdf`: 0
- `07_太平圣惠方_文本检索副本_含卷九十一.pdf`: 0

Interpretation:
- most low-confidence pages are title pages, damaged scans, sparse pages, or heavily degraded print
- `外科正宗` is the main manual-review target if quality needs another lift

## Source Spot Checks

Verified with `ancient_cli.py source`:

- OCR sample:
  `ancient:a86bc46d1167fec0614c` page 55
- Native-text sample:
  `ancient:64f193710ce00244fe88` page 91

Both returned correct book metadata, physical page, PDF page label, and page text.

## Local Helper Files

Local working files used in this run:

- `ancient_cli.py`
- `run_ready_ocr.sh`
- `run_full_ocr.sh`
- `qa_ancient_db.py`
- `test_ancient_cli.py`

## Recommended Next Step

Use this ancient database as a separate retrieval source from the modern literature database.

Recommended retrieval strategy:

- modern literature: keep chunk-level retrieval
- ancient corpus: use page-level retrieval first
- fusion: merge by reciprocal rank or source-weighted rank, but keep ancient and modern provenance separate

Do not mix ancient OCR pages into the modern `rag.db`.
