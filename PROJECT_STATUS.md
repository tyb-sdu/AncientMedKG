# 中药烧伤 RAG 项目状态

更新时间：2026-07-29

## 当前阶段

项目目前处于**古籍证据质量验收与人工 OCR 复核阶段**。现代文献 RAG 主链路已经完成，古籍已经完成 OCR、独立页级向量索引和双库检索，但古籍低置信页面仍需要人工对照原 PDF 确认，暂不应自动覆盖 OCR 原文。

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
| 古籍 OCR 复核包 | 已完成 | 已生成 24 个 P1 优先页面供人工复核 |

## 古籍评测结果

| 方法 | Recall@5 | Recall@10 | MRR@10 | 页码定位率 |
|---|---:|---:|---:|---:|
| keyword | 0.8696 | 0.8913 | 0.6837 | 1.0 |
| qwen-vector | 0.5652 | 0.6087 | 0.4796 | 1.0 |
| qwen-reranked-hybrid | 0.8043 | 0.8043 | 0.7409 | 1.0 |

当前古籍默认检索建议使用 `keyword`。Qwen reranker 可作为辅助排序，但在当前 52 题集上没有超过关键词检索的 Recall@10。

## 当前待办

1. 人工对照 `review_packet_v1` 中的 24 个 P1 页面，并在 `review_manifest.csv` 填写 `confirmed`、`corrected`、`unresolved` 或 `not_needed`。
2. 运行 `export_review_overrides.py` 生成独立的 OCR 复核 sidecar；复核结果不得直接改写原始 PDF 或原始 OCR 数据。
3. 依据人工确认结果建立新的可复现数据版本，并重新运行古籍检索评测。
4. 补充至少 20 个独立无答案或近似问题，单独校准拒答阈值；当前 6 个无答案问题不足以支持可靠阈值。
5. OCR 质量稳定后，再设计知识图谱证据层和实体关系抽取；古籍 OCR 复核前不冻结最终知识图谱。

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

GitHub `main` 当前仍为初始公开提交 `e2fe497`。后续两个提交包含新的评测与复核工作流代码，是否公开推送需要单独确认公开内容后再执行。
