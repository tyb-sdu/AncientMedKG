# Handoff：现代文献 RAG 服务器版

更新时间：2026-07-30

## 已验收

项目目录：`/data2/lxj/projects/tcm-burn-rag`

2026-07-28 最终复核时，已将 `config.yaml` 的 `modern_pdf_dir` 从旧 Windows 路径修正为服务器相对路径 `../corpus/modern_pdf`。

- PDF：584/584；页：9,870；chunk：10,983；FTS5 行：10,983。
- `doctor --deep`：healthy；重复 doc_id、DOI 冲突、无效 DOI、缺页、孤儿 chunk、FTS 孤儿均为 0。
- 原始 PDF SHA-256：未变化。
- 冻结数据库：`data/freeze/modern_corpus_v1_manifest.json` 对应哈希一致。
- Python 测试：13 passed。
- 页码定位：正式评测中所有返回结果均可通过 `source_page` 定位。

## 检索版本

### Qwen 主通道

`Qwen3-Embedding-8B` 使用 GPU 0，`Qwen3-Reranker-8B` 使用 GPU 1；模型为 Apache-2.0，索引为 4096 维 `IndexFlatIP`，映射复用原 chunk_id，`rag.db_modified=false`。

正式 56 题结果：

| 模式 | Recall@5 | Recall@10 | MRR@10 | 页码定位 | 无答案准确率 |
|---|---:|---:|---:|---:|---:|
| qwen-vector | 0.8750 | 0.9583 | 0.8000 | 1.0000 | 1.0000 |
| qwen-reranked-hybrid | 0.9792 | 0.9792 | 0.9688 | 1.0000 | 1.0000 |

评测结果：`data/retrieval_eval_v2.json`。Qwen 重排混合通道是当前默认生产路径。

### 保留的对照

旧 keyword/vector/hybrid 与 BGE-M3 结果仍保留在同一评测文件中，用于回归比较，不删除、不覆盖。

## 不要做的事

- 不要对现代 PDF 重新命名、覆盖或重新提取，除非先建立新的语料版本。
- 不要修改 `data/rag.db`、`data/chunks.jsonl` 或冻结清单来迎合评测结果。
- 不要把古籍 OCR 混入现代文献索引；古籍应作为独立语料版本验收。
- 不要将当前终端检索结果表述为回答大模型生成的医学结论。

## 后续建议

先固定当前现代文献版本并保存服务器目录清单、模型目录清单与 SHA-256。下一阶段再单独设计古籍 OCR、页图溯源和人工抽检，不与当前 Qwen 索引混库。
## 2026-07-28 双库检索接入

本轮已将古籍库作为独立语料接入现有终端检索，但没有把古籍混入现代 `rag.db`。

现状：

- 现代文献库：`app/data/rag.db`
- 古籍页库：`../ancient_ocr/data/ancient_rag.db`
- 查询模式：`modern` / `ancient` / `dual`
- `source --mode auto` 已支持按 `doc_id` 自动分流

验证结果：

- 服务器测试：`15 passed`
- `doctor --deep`：现代库 `healthy=true`
- 新增 `ancient_corpus` 检查：`healthy=true`

当前行为：

- `modern + qwen-reranked-hybrid` 仍是现代文献默认主通道
- `ancient + keyword` 使用古籍页级检索
- `dual + qwen-reranked-hybrid` 会先跑现代高质量检索，再并列合入古籍结果
- 如果 `dual` 融合前列全被现代结果占满，但古籍存在命中，则最终展示至少保留一条古籍结果

建议后续：

- 如要继续提升古籍权重，可单独调 `search.dual_ancient_weight`
- 古籍 Qwen 页级向量索引已在本轮完成；后续工作应转向 OCR 低置信页的人工复核和古籍独立评测集，而不是重建现有索引

## 2026-07-28 古籍 Qwen 页级索引验收

已创建独立旁路索引 `../ancient_ocr/data/vector_qwen3_8b_pages/`，模型为 `Qwen/Qwen3-Embedding-8B`，页向量维度 4096，索引类型为 `IndexFlatIP`。索引条目、`page_ids.jsonl` 映射和古籍数据库页面数均为 5,624；索引清单内的 `pages_sha256` 与当前 `pages.jsonl` 一致。构建耗时 310.622 秒，支持检查点续跑，且记录 `ancient_db_modified=false`。

验收：服务器 `doctor --deep` 显示现代库、古籍页库和 `ancient_qwen_vector` 均为 `healthy=true`；回归测试 `16 passed`。真实检索“忍冬 金银花 治疗痈疽发背”由古籍向量和重排路径定位到《本草纲目》卷十八忍冬 PDF 第 232 页；`source --mode auto` 已回读同一页全文。双库 Qwen 重排检索“金银花 烧伤 创面修复”同时返回古籍页和现代烧伤文献。

新增正式命令：`embed-ancient-qwen`、`query --mode ancient --retrieval qwen-vector`、`query --mode ancient --retrieval qwen-reranked-hybrid`。运行 Qwen 查询时保持串行，避免在同一张 GPU 上并发加载 8B 嵌入模型。

## 2026-07-28 古籍独立验收与 OCR 审计

新增可公开版本化的 `evaluation/ancient_questions_v1.json` 与 `scripts/evaluate_ancient_retrieval.py`。题集 52 题，其中 46 个正例固定到 `ancient:` book_id 与物理 PDF 页，6 个无答案题。评测在检索前验证标签页存在且每页至少命中一个人工标注的证据词；标签不从向量、重排或融合结果生成。

正式结果写入私有数据目录 `../ancient_ocr/data/ancient_retrieval_eval_v1.json`：

| mode | Recall@5 | Recall@10 | MRR@10 | 页码定位 |
|---|---:|---:|---:|---:|
| keyword | 0.8696 | 0.8913 | 0.6837 | 1.0000 |
| qwen-vector | 0.5652 | 0.6087 | 0.4796 | 1.0000 |
| qwen-reranked-hybrid | 0.8043 | 0.8043 | 0.7409 | 1.0000 |

结论：古籍默认主通道改为 `keyword`；Qwen 重排仅作补充检索，纯 Qwen 向量不作默认。无答案准确率当前均为 0，因为尚未实现独立校准的拒答阈值；不得把任意返回结果表述为证实性结论。

新增 `ancient_ocr/generate_low_confidence_audit.py`，只读 `ancient_rag.db` 并生成 `low_confidence_audit_v1.csv`。其对目录页降权、对汤火/火疮/忍冬/金银花/甘草等核心项目词加权；当前 282 页低置信页分为 P1 113 页和 P2 169 页。优先人工复核 P1，尤其《外科正宗》与《医宗金鉴》中的项目相关页。
已新增 `ancient_ocr/build_review_packet.py` 和 `ancient_ocr/export_review_overrides.py`。服务器当前已生成 P1 前 24 页的私有审核包：`ancient_ocr/output/review_packet_v1/`。其中 `review_manifest.csv` 初始状态均为 `unreviewed`；人工填写后，导出器会核对当前数据库页 ID、源文件 SHA-256 和原 OCR 文本哈希，再输出旁路 `review_overrides_v1.jsonl`，不会直接改写原始页面。
## 2026-07-29 Ancient layout reorder v2

The OCR payload stores structured `segments` with page geometry. A read-only sidecar
`../ancient_ocr/data/pages_layout_v2.jsonl` now reconstructs reading order from
`reading_direction` and bounding-box coordinates without changing source PDFs,
`ancient_rag.db`, or modern `rag.db`.

Full-corpus result: 5,624 rows; 2,561 single-column pages, 156 two-column pages,
35 three-column pages, 7 four-column pages, 4 five-column pages, 1 six-column page,
and 2,860 pages with no usable boxes that retain the original text.

The sidecar is used by ancient keyword retrieval, Qwen candidate snippets,
reranker candidates, and `source`. The Qwen ancient page index was rebuilt from
the ordered text and records `layout_sidecar_sha256`. Deep doctor is healthy and
the relevant test suite is `11 passed`. Ancient page locating remains `1.0`;
Recall@10 is keyword `0.8913`, qwen-vector `0.6522`, and reranked-hybrid `0.8043`.

The old review packet should not be used to judge column order; regenerate the
P1 review packet from v2 before manual OCR review.

## 2026-07-29 Vertical order fix

The first layout rule still grouped neighboring vertical lines and sorted their
segments by page `y`, which caused the reported middle-start and cross-line jumps.
The final rule now sorts vertical pages by segment `x` from right to left and
then by `y` within each line. Horizontal pages use page-scale x gaps for column
detection.

The full sidecar and Qwen page index were rebuilt again. The final relevant test
suite is `13 passed`; ancient page locating remains `1.0`. Final Recall@10 is
keyword `0.8913`, qwen-vector `0.6739`, and qwen-reranked-hybrid `0.8043`.
The 24-page P1 review packet and desktop manifest were regenerated from this
final sidecar. Blurred-page character errors remain a separate human-review
problem and are not silently auto-corrected.

## 2026-07-30 P1 PaddleOCR-VL candidate batch

The full P1 queue now has PaddleOCR-VL 1.6 review candidates for 113/113 pages.
The resumed run generated 29 new pages, reused 84 candidates with the same
configuration hash, and recorded zero failures. Poppler remains the primary
renderer; repaired-PDF fallback pages use PyMuPDF and are always flagged for
manual comparison.

The final manifest routes 61 pages to `vl_candidate_ready_for_review` and 52
pages to `manual_compare_required`. These states prioritize human review only;
neither state authorizes automatic replacement of original OCR text. Review
flags include empty or short/long candidates, low CJK ratio, repeated output,
kana noise, non-text blocks, and render fallback.

`ancient_ocr/verify_candidate_manifest.py` validates required fields, SHA-256
values, book/source identity, physical-page uniqueness, expected candidate and
image paths, and empty-candidate flags. The 113-row final manifest passes with
`valid=true` and no issues. The Windows release slice reports `43 passed`; run
the complete suite again in the server Git repository before the milestone
commit and exclude all private candidates, page images, OCR data, databases,
indexes, model files, and logs.

## 2026-07-30 vNext promotion decision

Manual page-by-page comparison is no longer a release prerequisite. Promote all
113 PaddleOCR-VL rows with `ancient_ocr/promote_vl_candidates.py` into a new
database: adopt 105 non-empty candidate texts and preserve the original text for
8 empty candidates. The original `ancient_rag.db` remains the rollback point.
The promotion also synchronizes `payload_json.text`, exports a matching vNext
`pages.jsonl`, verifies the source database SHA-256 is unchanged, and enforces
the expected 5,624-page count. Point `ancient_database` and
`ancient_pages_jsonl` at the same vNext version and use a fresh vector directory.

Rebuild FTS and the independent ancient index for vNext, then rerun doctor and
the 52-question evaluation. Release requires page locating `1.0` and keyword
Recall@10 at least the current `0.8913` baseline. Human review, abstention calibration, and the
knowledge-graph evidence layer are post-delivery enhancements.

## 2026-07-31 vNext release acceptance

The server promoted all 113 P1 rows into a new rollback-safe version: 105
non-empty PaddleOCR-VL texts were adopted and 8 empty candidates retained the
original OCR. The vNext database, FTS table, and exported JSONL each contain
5,624 pages; SQLite quick check is `ok`, and the source database SHA-256 was
unchanged.

A fresh Qwen3-Embedding-8B index was built for the vNext corpus. Deep doctor
reports matching page-JSONL, normalized-corpus, database, and layout-sidecar
fingerprints. The 52-question results are keyword Recall@10 `0.8913`,
qwen-vector `0.6739`, and qwen-reranked-hybrid `0.7826`; all three page
locating rates are `1.0`. The complete server suite reports `68 passed`, public
release preflight is clean, and the aggregate release validator returns
`valid=true` with no issues.
