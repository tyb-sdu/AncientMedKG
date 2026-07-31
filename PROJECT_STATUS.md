# 中药烧伤 RAG 项目状态

更新时间：2026-07-31

## 当前阶段

项目已完成 **P1 候选 vNext 纳入与服务器最终验收**。现代文献 RAG 主链路、古籍 OCR、独立页级索引、双库检索和评测均已完成。113 个 P1 页面已全部进入版本化 vNext，原数据库保留为回滚点；当前进入 GitHub 里程碑提交收尾。

## 已完成

| 阶段 | 状态 | 结果 |
|---|---|---|
| 现代文献整理与入库 | 已完成 | 584 篇、9,870 页、10,983 个 chunk |
| 现代文献质量修复 | 已完成 | DOI、doc_id、chunk_id、语言识别、页码定位已验收 |
| 现代文献检索评测 | 已完成 | 独立问题集；Qwen 主路径 Recall@10 约 0.9792，MRR@10 约 0.9688，页码定位率 1.0 |
| 古籍 PDF 与来源保护 | 已完成 | 原始 PDF 不修改，来源哈希已记录 |
| 古籍 OCR | 已完成 | 12 部古籍、5,624 页；2,788 页 OCR，2,836 页原生文本 |
| 古籍数据库 | 已完成 | 5,624 本地页记录及 FTS5 检索，数据库健康 |
| 古籍页级向量索引 | 已完成 | Qwen3-Embedding-8B，5,624 页，4,096 维，独立 sidecar，不覆盖数据库 |
| 双库检索 CLI | 已完成 | modern / ancient / dual，keyword / vector / hybrid |
| 古籍独立检索评测 | 已完成 | 52 个问题，包含 46 个有答案问题和 6 个无答案问题 |
| 古籍 OCR 初始复核包 | 已完成 | 已生成首批 24 个 P1 页面供人工复核 |
| P1 VLM 候选批次 | 已完成 | 113/113；105 个非空候选采用新文本，8 个空候选保留原文 |
| P1 候选清单完整性 | 已完成 | 113 条记录通过字段、哈希、页码、唯一键和相对路径核验，问题 0 |
| vNext 发布总闸 | 已通过 | 68 项测试；doctor、52 题评测、preflight 与汇总验证均通过 |

## 古籍评测结果

| 方法 | Recall@5 | Recall@10 | MRR@10 | 页码定位率 |
|---|---:|---:|---:|---:|
| keyword | 0.8696 | 0.8913 | 0.6837 | 1.0 |
| qwen-vector | 0.6304 | 0.6739 | 0.5280 | 1.0 |
| qwen-reranked-hybrid | 0.7609 | 0.7826 | 0.7089 | 1.0 |

当前古籍默认检索建议使用 `keyword`。Qwen reranker 可作为辅助排序，但在当前 52 题集上没有超过关键词检索的 Recall@10。

## 发布收尾

服务器已完成独立 vNext 数据库、同版 `pages.jsonl`、冻结 layout sidecar、全新 Qwen 页级索引、深层 doctor、52 题回归和汇总验收。最后只需复核暂存文件、运行 `git diff --check` 与 staged preflight，并推送 GitHub 里程碑。

人工复核、拒答阈值校准和知识图谱证据层列为交付后增强，不再阻塞本次发布。

## 明确暂不做

- 不处理古籍 OCR 之外的自动改写或自动纠错。
- 不覆盖 `rag.db` 或 `ancient_rag.db`。
- 不重切已有 chunks。
- 不接入收费 API。
- 不开发网页端。
- 不接入回答大模型。

## 代码与 Git 状态

服务器项目目录：

`/data2/lxj/projects/tcm-burn-rag`

服务器本地最新提交：

- `70b6fca Add non-destructive ancient OCR review workflow`
- `e8f348f Add ancient retrieval evaluation and OCR audit`
- `e2fe497 Initial public AncientMedKG codebase`

截至 2026-07-29 的记录，GitHub `main` 仍为初始公开提交 `e2fe497`。后续代码、测试和文档提交必须在服务器实际 Git 仓库中复核后推送；当前 Windows `github_publish` 是无 `.git` 元数据的导出副本，不能用于证明远端提交状态。

## 2026-07-29 竖排跳行修复

已确认旧规则在竖排页面中先按 `y` 排序，导致不同竖行之间交叉跳转。现已改为：竖排先按文字框 `x` 从右到左，再在同一竖行内按 `y` 从上到下；横排根据页面整体横向间距识别栏；文字框缺失时保留原文并显式标记回退。

修复后全量旁路、古籍 Qwen 向量索引和 P1 复核包均已重新生成。相关测试 `13 passed`，古籍页码定位率仍为 `1.0`，Qwen vector Recall@10 为 `0.6739`，关键词 Recall@10 为 `0.8913`。当前桌面 `review_manifest.csv` 已是最终版面排序版本。

## 2026-07-30 P1 VLM 候选与验收工具

PaddleOCR-VL 1.6 已完成全部 113 个 P1 页面的候选生成，主渲染器为 Poppler，损坏 PDF 页使用 PyMuPDF 修复式回退并强制标记人工复核。最终报告记录 `completed=29`、`skipped_current=84`、`failed=0`、`manifest_rows=113`；断点复用页与本轮新生成页共同构成完整批次。

候选分流为：61 页 `vl_candidate_ready_for_review`，52 页 `manual_compare_required`。自动质量门只决定人工复核顺序，不自动接受候选文本。新增 `verify_candidate_manifest.py` 对最终清单执行可追溯性核验，真实 P1 清单结果为 `valid=true`、`issues=[]`。

```bash
python ancient_ocr/verify_candidate_manifest.py /path/to/vl_candidate_manifest_p1_final.csv \
  --output /path/to/candidate_manifest_integrity.json
python -m pytest ancient_ocr app/tests -q
```

当前 Windows 导出副本的轻量测试结果为 `43 passed`。新增 `extract_json_report.py` 会从 `doctor --deep` 的日志混合输出提取纯 JSON；`validate_vnext_release.py` 随后读取推广、doctor、52 题评测和 preflight 的真实服务器 JSON，统一核验 113/105/8、5,624、正文/数据库/布局侧车指纹、页码定位率和关键词 Recall@10 门槛。最终 GitHub 里程碑仍需在服务器完整仓库中执行项目全量测试，并确认 `git status` 不包含 PDF、OCR 数据、数据库、索引、模型、日志或密钥。

## 2026-07-30 vNext 直接纳入决策

根据最新交付决定，本次发布不再以人工逐页对照为前置条件，113 页候选全部进入独立 vNext。`promote_vl_candidates.py` 使用 SQLite 在线备份生成新数据库，105 个非空候选采用新文本，8 个空候选保留原 OCR；同步更新新库 FTS 与 `payload_json.text`，并导出与数据库一致的 vNext `pages.jsonl`，为每页记录完整哈希、风险标记与推广模式。工具硬校验 113/105/8 计数、源库前后 SHA-256 以及 5,624 页整库计数；原数据库不改写，可随时整库回退。

vNext 配置必须同时指向新数据库与新 `pages.jsonl`，并重新构建独立索引后通过回归：页码定位率保持 `1.0`，关键词 Recall@10 不低于当前基线 `0.8913`。最终验收口径见 `RELEASE_ACCEPTANCE.md`。`release_preflight.py` 在实际 Git 仓库中检查被跟踪文件，发现私有语料、数据库、索引、模型、缓存、日志或密钥时返回失败。
