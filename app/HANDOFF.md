# Handoff：现代文献 RAG 服务器版

更新时间：2026-07-28

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
