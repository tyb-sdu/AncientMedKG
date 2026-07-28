# 中药烧伤项目现代文献 RAG

服务器部署目录：`/data2/lxj/projects/tcm-burn-rag`

服务器配置中的现代文献目录为 `../corpus/modern_pdf`，从 `app/` 运行命令即可直接定位 584 份 PDF。

这是纯本地终端 RAG，不启动网页服务、不调用收费 API、不接回答大模型。古籍已作为独立页级语料接入，不混入现代文献数据库。

## 当前完成状态

- 584 篇 PDF，9,870 页，10,983 个同页 chunk。
- 原 PDF 已迁移到 `corpus/modern_pdf`，服务器端数量 584/584。
- SQLite FTS5、原 E5 CPU FAISS、BGE-M3 GPU FAISS、Qwen3-Embedding-8B FAISS 均使用现有 `chunk_id`，不重切、不覆盖 `rag.db`。
- `doctor --deep`：`healthy=true`；原 PDF SHA-256 未变化；冻结数据库哈希一致。
- 56 题独立评测已经完成，标签固定为文献 `doc_id` 与物理 PDF 页码，不使用检索结果生成标签。

## 推荐主通道

生产检索使用双 RTX 4090 上的 Qwen3 8B：

- `Qwen/Qwen3-Embedding-8B`：GPU 0，4096 维，归一化向量。
- `Qwen/Qwen3-Reranker-8B`：GPU 1，候选集 cross-encoder 重排。
- Qwen 主通道 `Recall@10=0.9792`、`MRR@10=0.9688`、页码定位率 `1.0`、无答案准确率 `1.0`。
- 模型和索引是项目专属目录，不混入 `/data2/lxj/projects/CervixAgent`。

模型来源：[Qwen3-Embedding-8B](https://huggingface.co/Qwen/Qwen3-Embedding-8B)、[Qwen3-Reranker-8B](https://huggingface.co/Qwen/Qwen3-Reranker-8B)。

## 运行命令

```bash
cd /data2/lxj/projects/tcm-burn-rag/app
PY=/data2/lxj/projects/tcm-burn-rag/.conda/bin/python
export RAG_MODERN_PDF_DIR=/data2/lxj/projects/tcm-burn-rag/corpus/modern_pdf
export PYTHONPATH=/data2/lxj/projects/tcm-burn-rag/app/src

$PY rag_cli.py --config config.yaml query --retrieval qwen-vector \
  "绿原酸促进创面修复"
$PY rag_cli.py --config config.yaml query --retrieval qwen-reranked-hybrid \
  "绿原酸促进创面修复"
$PY rag_cli.py --config config.yaml doctor --deep
$PY scripts/retrieval_eval_v2.py
```

结果始终带有题名、年份、DOI、PDF 物理页码、文件名、`doc_id`、`chunk_id`、向量分数、重排分数和原文片段；可用 `source --doc-id ... --page ...` 回看整页。

## 旁路索引

- 旧基线：`data/vector`，E5 384 维。
- BGE 对照：`data/vector_bge_m3`。
- 当前主通道：`data/vector_qwen3_8b`。

所有索引均可用 `--resume` 续跑；`rag.db` 和 `data/chunks.jsonl` 是冻结输入。
## 2026-07-28 双库更新

当前 `rag_cli.py` 已支持三种查询模式：

- `--mode modern`：仅检索现代文献库
- `--mode ancient`：仅检索古籍页库
- `--mode dual`：现代文献与古籍独立检索后合并展示

新增配置：

- `paths.ancient_database=../ancient_ocr/data/ancient_rag.db`
- `paths.ancient_books_jsonl=../ancient_ocr/data/books.jsonl`
- `paths.ancient_pages_jsonl=../ancient_ocr/data/pages.jsonl`

推荐命令：

```bash
$PY rag_cli.py --config config.yaml query --mode modern --retrieval qwen-reranked-hybrid \
  "绿原酸促进创面修复"
$PY rag_cli.py --config config.yaml query --mode ancient --retrieval keyword \
  "金银花 烧伤"
$PY rag_cli.py --config config.yaml query --mode dual --retrieval qwen-reranked-hybrid \
  "金银花 烧伤 创面修复"
$PY rag_cli.py --config config.yaml source --mode auto --doc-id ancient:BOOK_ID --page 232
```

说明：

- 古籍仍保持独立数据库，不混入现代 `rag.db`
- `dual` 模式优先保留现代高质量结果，但如果古籍有命中，会至少保留一条古籍结果进入最终展示
- `source --mode auto` 会根据 `doc_id` 自动分流到现代页或古籍页

## 2026-07-28 古籍 Qwen 页级索引

古籍页库已建立独立的 `Qwen/Qwen3-Embedding-8B` 向量索引；索引复用原有 `page_id`，不修改 `ancient_rag.db`，也不重切页面。索引为 4,096 维 `IndexFlatIP`，共 5,624 页，索引清单记录页面 SHA-256、模型指纹和创建时间。`doctor --deep` 会同时验证现代库、古籍页库和古籍 Qwen 索引。

```bash
$PY rag_cli.py --config config.yaml embed-ancient-qwen --resume
$PY rag_cli.py --config config.yaml query --mode ancient --retrieval qwen-vector \
  "忍冬 金银花 治疗痈疽发背"
$PY rag_cli.py --config config.yaml query --mode ancient --retrieval qwen-reranked-hybrid \
  "忍冬 金银花 治疗痈疽发背"
$PY rag_cli.py --config config.yaml query --mode dual --retrieval qwen-reranked-hybrid \
  "金银花 烧伤 创面修复"
```

古籍 `qwen-reranked-hybrid` 使用古籍 FTS5、古籍 Qwen 页向量和 Qwen3-Reranker-8B 重排；双库模式分别检索现代与古籍，之后以 RRF 合并，保持每条结果的语料类型、文件、物理 PDF 页码、`doc_id` 和 `chunk_id`。

## 2026-07-28 古籍独立验收基线

古籍独立题集位于 `evaluation/ancient_questions_v1.json`，共 52 题：46 个固定书籍 ID + 物理 PDF 页标签，6 个无答案题。每一个正例标签均含页内证据词，评测运行前会用 `source` 回读同一页验证标签，避免由检索结果反推标准答案。

```bash
$PY scripts/evaluate_ancient_retrieval.py --config config.yaml
$OCR_PY ../ancient_ocr/generate_low_confidence_audit.py \
  --data-dir ../ancient_ocr/data
```

| 古籍通道 | Recall@5 | Recall@10 | MRR@10 | 页码定位率 |
|---|---:|---:|---:|---:|
| keyword | 0.8696 | 0.8913 | 0.6837 | 1.0000 |
| qwen-vector | 0.5652 | 0.6087 | 0.4796 | 1.0000 |
| qwen-reranked-hybrid | 0.8043 | 0.8043 | 0.7409 | 1.0000 |

因此，古籍当前默认应使用 `--retrieval keyword`；Qwen 重排可作为补充对照，纯 Qwen 向量不作为默认。三条通道尚未实现经独立校准的无答案拒答阈值，无答案题准确率为 0，不能把非空检索列表解释为古籍中的医学结论。低置信 OCR 复核队列由审计器生成，共 282 页：P1 113 页、P2 169 页；该过程不会修改原始 PDF、页 JSON 或 SQLite 数据库。
