# 运行报告：服务器高质量检索版

## 结论

现代文献 RAG 已完成服务器部署和高质量本地检索。当前推荐 `qwen-reranked-hybrid`：先用 FTS5 与 Qwen3-Embedding-8B 召回，再用 Qwen3-Reranker-8B 对候选 chunk 重排。Qwen 主通道保持原始 PDF 页码和 chunk 证据链，不生成回答，不启动 Web 服务。

## 资源

- 主机：两张 NVIDIA RTX 4090，各 24 GB 显存。
- 环境：项目独立 Conda，Python 3.11；PyTorch CUDA；Transformers 4.57.6；Sentence Transformers；FAISS CPU 旁路索引。
- 目录：`/data2/lxj/projects/tcm-burn-rag`。
- 现代 PDF：584 篇，约 2.7 GB。
- Qwen 模型：约 30 GB，两个 8B 模型分别使用 GPU 0/1。

## 评测

正式评测集共 56 题，其中 48 个有答案、8 个无答案，覆盖烧伤、烫伤、创面修复、忍冬/金银花、甘草、绿原酸、甘草酸、炎症、抗菌、水凝胶、安全性、临床证据及无答案标识符。

Qwen 结果：

- `qwen-vector`：Recall@5 0.8750，Recall@10 0.9583，MRR@10 0.8000。
- `qwen-reranked-hybrid`：Recall@5 0.9792，Recall@10 0.9792，MRR@10 0.9688。
- 两者页码定位率和无答案准确率均为 1.0000。

检索返回会同时包含原始证据片段、物理 PDF 页码、文件名、doc_id、chunk_id、向量/重排分数，便于 RAG 溯源。

## 健康检查

`doctor --deep` 最终结果为 `healthy=true`：数据库 quick_check 正常，584/9870/10983 计数一致，FTS5 完整，E5 和 Qwen 索引映射均为 10,983/10,983，Qwen 索引维度 4096，冻结数据库 SHA-256 一致，原 PDF SHA-256 无变化。

最终运行前已把 `config.yaml` 的现代 PDF 路径修正为服务器相对路径 `../corpus/modern_pdf`，消除了旧 Windows 路径造成的缺失假告警。

## 产物

- `data/rag.db`
- `data/chunks.jsonl`
- `data/vector/`
- `data/vector_bge_m3/`
- `data/vector_qwen3_8b/`
- `data/retrieval_eval_v2.json`
- `setup/doctor-deep.json`
- `setup/smoke-qwen-vector.txt`
- `setup/smoke-qwen-reranked-hybrid.txt`

古籍 OCR、回答大模型和网页端均未处理，保留为下一独立阶段。
## 2026-07-28 双库检索增量

本轮没有重建现代语料，也没有改动现代 `rag.db`、`chunks.jsonl` 或任何现代向量索引。

完成内容：

- 新增古籍页库接入：`../ancient_ocr/data/ancient_rag.db`
- 新增 `dual_retrieval.py` 与 `ancient_retrieval.py`
- `rag_cli.py` 新增 `query --mode modern|ancient|dual`
- `rag_cli.py` 新增 `source --mode auto|modern|ancient`
- `doctor --deep` 新增古籍库健康检查输出 `ancient_corpus`

验证：

- 本地回归：`15 passed`
- 服务器回归：`15 passed`
- 服务器 `doctor --deep`：现代库与古籍库均 `healthy=true`
- 服务器 smoke query：
  - `modern + qwen-reranked-hybrid` 正常
  - `ancient + keyword` 正常
  - `dual + qwen-reranked-hybrid` 正常
  - `source --mode auto` 对现代与古籍均可回页

说明：

- 双库模式当前仍以现代高质量结果为主
- 古籍结果使用独立页级检索，不参与现代向量索引
- 为避免 `dual` 结果完全退化成单库展示，只要古籍存在命中，最终列表至少保留一条古籍结果

## 2026-07-28 古籍 Qwen 向量与重排增量

完成内容：

- 新增 `ancient_qwen_retrieval.py`，为独立古籍页库建立可断点续跑的 Qwen3-Embedding-8B 索引。
- 新增命令 `embed-ancient-qwen`，只写入 `../ancient_ocr/data/vector_qwen3_8b_pages/` 旁路目录，不写入 `ancient_rag.db` 或现代 `rag.db`。
- 古籍页向量索引：5,624 条、4,096 维、`IndexFlatIP`；页面映射与数据库页数完全一致，无孤儿向量或缺失页面。
- `ancient` 与 `dual` 模式均支持 `qwen-vector` 和 `qwen-reranked-hybrid`；后者按古籍 FTS5 + 页向量 RRF 召回，再使用 Qwen3-Reranker-8B 重排。
- `doctor --deep` 新增 `ancient_qwen_vector` 检查。

验证：

- 索引清单：页面 SHA-256 匹配，模型为 `Qwen/Qwen3-Embedding-8B`，构建耗时 310.622 秒，`ancient_db_modified=false`。
- `doctor --deep`：现代文献库、古籍页库和古籍 Qwen 索引均 `healthy=true`。
- 服务器回归：`16 passed`。
- 真实 smoke：古籍 Qwen 向量与重排检索“忍冬 金银花 治疗痈疽发背”均命中《本草纲目》卷十八忍冬 PDF 第 232 页，`source --mode auto` 可回读该页；双库 Qwen 重排检索“金银花 烧伤 创面修复”返回古籍与现代文献的独立证据链。

## 2026-07-28 古籍检索独立验收

本轮新增古籍独立验收和 OCR 人工复核工具，不重建 OCR、不修改古籍数据库、不影响现代语料。新增：

- `app/evaluation/ancient_questions_v1.json`：52 题，46 个固定页正例、6 个无答案题。
- `app/scripts/evaluate_ancient_retrieval.py`：先校验每个标签页的证据词，再评估关键词、Qwen 向量和 Qwen 重排混合。
- `ancient_ocr/generate_low_confidence_audit.py`：只读数据库生成低置信 OCR 审核 CSV 和汇总 JSON。

正式评测结果：

| mode | Recall@5 | Recall@10 | MRR@10 | 页码定位率 | 无答案准确率 |
|---|---:|---:|---:|---:|---:|
| keyword | 0.8696 | 0.8913 | 0.6837 | 1.0000 | 0.0000 |
| qwen-vector | 0.5652 | 0.6087 | 0.4796 | 1.0000 | 0.0000 |
| qwen-reranked-hybrid | 0.8043 | 0.8043 | 0.7409 | 1.0000 | 0.0000 |

解释：古文 OCR、方名和繁简字形使现代医学 Qwen 嵌入对精确方名页的判别弱于关键词；重排提升了 MRR，但没有超过关键词的 Recall@10。古籍生产默认应采用关键词通道，Qwen 重排只作为辅助证据发现。无答案表现为 0 是当前没有拒答阈值的已知限制，后续须用独立的校准集实现，而不是从本评测结果调参。

OCR 审计输出：282 页低置信页，P1 113 页、P2 169 页。P1 通过“正文长度 + OCR 质量 + 核心项目词”优先排序，并对目录页降权；原始 PDF、OCR 页 JSON 和 `ancient_rag.db` 均未修改。
