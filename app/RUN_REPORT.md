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
已生成 P1 前 24 页私有页图复核包，路径为 `ancient_ocr/output/review_packet_v1/`。抽检两页确认原始扫描页清晰、竖排/横排页面渲染正常。新增导出器会对人工审核状态、源 SHA-256、页 ID 和原 OCR 文本哈希做一致性校验，并把修订写入独立 JSONL 覆盖层；本轮尚未填写任何人工修订。
## 2026-07-29 Ancient layout reorder v2

The reported issue was confirmed as reading-order reconstruction, not primarily
character recognition. Existing `payload_json.segments` were used to create
`ancient_ocr/data/pages_layout_v2.jsonl`; no OCR rerun and no source-data rewrite
was performed.

The sidecar contains 5,624 page records. Of these, 2,561 are single-column,
156 two-column, 35 three-column, 7 four-column, 4 five-column, 1 six-column,
and 2,860 retain original text because no usable boxes were available.

Layout v2 is connected to ancient keyword, Qwen vector candidate text,
reranker candidate text, and source. The 5,624-page Qwen3-Embedding-8B index
was rebuilt and its manifest records `layout_sidecar_sha256`.

| mode | Recall@5 | Recall@10 | MRR@10 | page locating |
|---|---:|---:|---:|---:|
| keyword | 0.8696 | 0.8913 | 0.6837 | 1.0000 |
| qwen-vector | 0.6087 | 0.6522 | 0.5145 | 1.0000 |
| qwen-reranked-hybrid | 0.8043 | 0.8043 | 0.7337 | 1.0000 |

Deep doctor is healthy and the relevant tests report `11 passed`. The next
step is to regenerate the P1 review packet from v2; the old packet preview is
not a valid column-order acceptance artifact.
## 2026-07-29 Vertical order fix

The initial v2 heuristic still used page-level `y` ordering inside some vertical
groups. This caused cross-column jumps on non-double-column books. The final
implementation uses `x` descending first for `vertical-rtl` pages and `y`
ascending within each vertical line. Horizontal pages use page-scale center gaps
for column separation.

The full sidecar and Qwen3-Embedding-8B page index were rebuilt. The final
ancient evaluation is:

| mode | Recall@5 | Recall@10 | MRR@10 | page locating |
|---|---:|---:|---:|---:|
| keyword | 0.8696 | 0.8913 | 0.6837 | 1.0000 |
| qwen-vector | 0.6304 | 0.6739 | 0.5479 | 1.0000 |
| qwen-reranked-hybrid | 0.7826 | 0.8043 | 0.7058 | 1.0000 |

The final P1 review packet uses the final ordered preview. Blurred-page
character corrections remain a human-reviewed sidecar task.

## 2026-07-30 P1 PaddleOCR-VL 候选批次

P1 候选批次已完成 113/113 页：本轮新增 29 页，按相同配置哈希复用 84 页，失败 0。主渲染器为 Poppler；损坏 PDF 页使用 PyMuPDF 修复式回退，并强制写入 `render_fallback` 人工标记。该流程未修改原 PDF、正式 OCR、SQLite 数据库或向量索引。

最终清单分流如下：

| 状态 | 页数 | 含义 |
|---|---:|---|
| `vl_candidate_ready_for_review` | 61 | 通过自动质量门，仍需人工确认 |
| `manual_compare_required` | 52 | 存在版面、字符比例、长度、重复、含图或渲染回退风险，需重点对照 |

质量标记计数为：`contains_non_text_blocks=37`、`low_cjk_ratio=15`、`render_fallback=12`、`empty_candidate=8`、`candidate_too_long=6`、`candidate_too_short=6`、`kana_noise=4`、`repeated_text=4`。同一页面可以带多个标记，因此计数不可相加为页面总数。

新增 `ancient_ocr/verify_candidate_manifest.py` 和对应测试，对必填字段、SHA-256、书籍与来源映射、物理页唯一键、候选/渲染相对路径及空候选标记做只读验证。真实 113 行清单结果为 `valid=true`、`issues=[]`；Windows 导出副本的轻量测试为 `43 passed`。候选 JSON、页图和验收 JSON 均属于本地数据产物，不进入公开仓库。

## 2026-07-30 vNext 直接纳入

本次里程碑不再等待逐页人工对照。113 页 PaddleOCR-VL 记录全部进入独立 vNext 数据库：105 个非空候选采用新文本，8 个空候选保留原 OCR，避免清空页面。`ancient_ocr/promote_vl_candidates.py` 使用 SQLite 在线备份创建新库，验证来源、页 ID、原文/候选哈希，更新 FTS 与 `payload_json.text`，导出匹配的 vNext `pages.jsonl`，并输出逐页推广日志和整库报告；它还核验源库前后 SHA-256 与 5,624 页整库计数，原 `ancient_rag.db` 不修改。

新增公开发布跟踪文件检查器 `ancient_ocr/release_preflight.py`。当前 Windows 导出副本测试更新为 `43 passed`；`extract_json_report.py` 会从 doctor 日志中生成纯 JSON，`validate_vnext_release.py` 再汇总推广、doctor、52 题评测和 preflight 的服务器证据。vNext 服务器验收要求推广计数严格为 113/105/8，SQLite 健康、数据库页、FTS 与 vNext JSONL 均为 5,624 行，且配置中的数据库、页面 JSONL 和新索引属于同一版本；页码定位率须为 `1.0`，关键词 Recall@10 不低于当前基线 `0.8913`。最终发布还需在服务器完整 Git 仓库运行全量测试、跟踪文件检查和 `git diff --check`。

## 2026-07-31 vNext 服务器最终验收

服务器已生成独立版本 `vl_vnext_2026-07-31`。推广报告为 113 条记录，其中 `candidate_adopted=105`、`original_fallback_empty_candidate=8`；数据库页面数、FTS 行数和同版 `pages.jsonl` 行数均为 5,624，SQLite `quick_check=ok`。源数据库 SHA-256 在推广前后均为 `9daee70b...e64d`，未被修改；vNext 数据库 SHA-256 为 `e21ede4f...b9c`。

全新 Qwen3-Embedding-8B 页级索引包含 5,624 条、4,096 维向量。`doctor --deep` 确认 pages JSONL、规范正文、vNext 数据库和冻结 layout sidecar 四类 SHA-256 均与索引 manifest 匹配，古籍页库与向量索引均为 `healthy=true`。

52 题 vNext 回归结果如下：

| mode | Recall@5 | Recall@10 | MRR@10 | page locating |
|---|---:|---:|---:|---:|
| keyword | 0.8696 | 0.8913 | 0.6837 | 1.0000 |
| qwen-vector | 0.6304 | 0.6739 | 0.5280 | 1.0000 |
| qwen-reranked-hybrid | 0.7609 | 0.7826 | 0.7089 | 1.0000 |

关键词 Recall@10 与发布基线相同，三通道页码定位率均为 1.0。服务器完整测试为 `68 passed`；`release_preflight.py` 返回 `valid=true`、违规 0；`validate_vnext_release.py` 汇总推广、doctor、评测和 preflight 四份真实报告后返回 `valid=true`、`issues=[]`。Qwen 重排混合 Recall@10 较旧版 0.8043 小幅下降至 0.7826，因此仍保持 keyword 为默认古籍检索通道。

## 2026-07-31 五层 KG 与忍冬汤专项验收

新增 `knowledge_graph/` 和 `research_pipeline/`。五层 KG 支持稳定实体 ID、
同名异方组成指纹、E1-E5 证据、直接证据与机制迁移分离、不可变 JSONL 构建、
SQLite 来源复核，以及 Neo4j CSV/Cypher 和 JSON-LD 导出。

真实忍冬汤草案为 2 个来源、17 个实体、4 条证据和 32 条断言。古籍数据库中
第 137、138、227 页的页 ID、物理页、正文 SHA-256 和引文均 `exact`；导出含
17 个实体节点、4 个证据节点、32 个断言节点、32 条直接关系和 102 条溯源
关系。所有记录仍为 `pending`，发布模式按预期只报
`evidence_not_approved`/`edge_not_approved`。烧伤相关边仅为 E5
`MECHANISM_TRANSFER`，没有古籍直接治疗烧伤的结论。

15 个忍冬汤专项问题验证了同名异方和拒答规划规则。受控词表规划层在 keyword、
Qwen vector、Qwen reranked hybrid 的 Recall@5/10、MRR@10、页码定位率及
3 题拒答准确率均为 1.0；原始检索器基线另行保留，二者没有混报。

## 2026-07-31 活性成分发现 intake

新增 `discovery_pipeline/`，实现：PubChem PUG REST 身份解析与原始响应哈希、
现代文献单遍扫描、ASCII 词边界、防覆盖输出、C0-C5 硬门、六维
`R_compound`、15 个敏感性情景、实验靶点分层、疾病基因双通道规则、
PPI `score>=0.7`、至少 5 节点模块、超几何富集与 Benjamini-Hochberg FDR。

真实 intake 结果：13/13 个候选身份解析成功；绿原酸 CID 1794427、甘草酸
CID 14982 与方案一致；现代 584 篇文献中生成 2,238 条带 `doc_id`、
`chunk_id`、PDF 页码及双 SHA-256 的复核候选，损坏 topic tags 文档为 0。
`discovery_pipeline doctor` 复算全部聚合和 6 类指纹后返回 `valid=true`、
`issues=[]`、`computational_intake_complete=true`，并保守保持
`scientific_release_ready=false`。候选共现不计为 C3 证据，尚未产生最终排名。
