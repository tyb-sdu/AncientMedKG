# Kanripo 十部古籍自动纳入副本

本流程把十部新古籍的 Kanripo 逐页转写追加到一个独立数据库副本。它不会修改基线数据库、现有 JSONL 或现有向量索引。

## 接受政策

- 严格使用 `confidence > 0.7`，不是 `>= 0.7`。
- 达标页直接纳入，不建立人工审核队列。
- 纳入状态固定为 `auto_accepted_unreviewed`，并保留 `human_image_reviewed=false`。这表示项目政策不要求人工审核，不表示已经人工核过原图。
- `kanripo_text_quality_v1` 是由页锚存在、汉字占比、页文本长度和损坏字符比例组成的可解释文本质量分，不是 OCR 模型概率。
- 每页保留仓库、Git commit、快照 SHA-256、源文件、版本和 `<pb:...>` 页锚。

## 构建

```powershell
python ancient_ocr/kanripo_auto_ingest.py build `
  --base-database ancient_ocr/data/versions/vl_vnext_2026-07-31/ancient_rag.db `
  --sources-root C:/path/to/kanripo_sources `
  --output-dir ancient_ocr/data/versions/kanripo_auto70_2026-08-02
```

输出包括同版 `ancient_rag.db`、`pages.jsonl`、`books.jsonl`、来源清单和构建报告。再次核验：

```powershell
python ancient_ocr/kanripo_auto_ingest.py doctor `
  --output-dir ancient_ocr/data/versions/kanripo_auto70_2026-08-02 `
  --sources-root C:/path/to/kanripo_sources
```

`doctor` 同时检查 SQLite、FTS/JSONL 行数、接受阈值、状态字段以及十个来源仓库的 commit 和快照指纹。

逐书输出烧伤、外科和目录筛选词的命中页锚证据：

```powershell
python ancient_ocr/kanripo_auto_ingest.py relevance-audit `
  --output-dir ancient_ocr/data/versions/kanripo_auto70_2026-08-02
```

该报告证明书目与项目的文本相关性和逐页可定位性，不把术语命中解释成临床疗效或同方证据。
