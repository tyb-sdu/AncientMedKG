# 数据字典

## SourceRecord

| 字段 | 必填 | 说明 |
|---|---:|---|
| `source_id` | 是 | 稳定来源 ID |
| `source_type` | 是 | `ancient_pdf`、`modern_pdf`、`curated_ontology`、`database` 或 `experiment` |
| `title` | 是 | 文献或古籍题名 |
| `file_name` | PDF 是 | 原文件名，仅作定位 |
| `file_sha256` | PDF 是 | 原 PDF 的小写 SHA-256 |
| `doi` | 现代文献建议 | 完整 DOI |
| `work_id` | 古籍建议 | 作品规范 ID |
| `edition_id` | 古籍建议 | 版本规范 ID |
| `attributes` | 否 | `book_id`、`doc_id`、出版信息等结构化扩展 |

## GraphNode

| 字段 | 必填 | 说明 |
|---|---:|---|
| `node_id` | 是 | 根据身份字段生成的稳定 ID |
| `entity_type` | 是 | `schema.json` 中声明的实体类型 |
| `canonical_name` | 是 | 规范名称 |
| `layer` | 是 | `L1`-`L5` 或 `EXT` |
| `aliases` | 否 | 去重后的别名 |
| `external_ids` | 否 | PubChem CID、UniProt、MeSH、药典 ID 等 |
| `attributes` | 否 | 类型特异属性 |

`FormulaVariant.attributes` 必须包含：

| 字段 | 说明 |
|---|---|
| `formula_name` | 原方名或规范方名 |
| `composition` | 药材、剂量、单位、炮制、角色列表 |
| `composition_fingerprint` | 构建器自动生成的规范化组成 SHA-256 |
| `source_locator` | 来源、版本、章节和物理页定位 |

`Passage.attributes` 必须包含 `source_id` 和 `locator`。

## EvidenceRecord

| 字段 | 必填 | 说明 |
|---|---:|---|
| `evidence_id` | 是 | 来源、定位和引文哈希共同生成的稳定 ID |
| `source_id` | 是 | 对应 `SourceRecord` |
| `locator` | 是 | `page_id`、`physical_page`、`pdf_page`、`chunk_id`、章节、坐标等 |
| `quote` | E1 是 | 支持断言的最小充分原文 |
| `quote_sha256` | 是 | UTF-8 原文 SHA-256 |
| `evidence_grade` | 是 | E1-E5 |
| `evidence_class` | 是 | 直接古籍、权威整理、交叉支持、现代桥接、数据库预测或实验 |
| `review.status` | 是 | `approved`、`pending` 或 `rejected` |
| `review.reviewer` | 发布建议 | 审阅者或审阅流程 ID |
| `review.reviewed_at` | 发布建议 | ISO 日期时间 |

若提供 `locator.page_text_sha256` 或 `locator.chunk_text_sha256`，`verify-sources` 会对当前 SQLite 文本重算并核对。

## GraphEdge

| 字段 | 必填 | 说明 |
|---|---:|---|
| `edge_id` | 是 | 主体、谓词、客体、证据和属性共同生成的稳定断言 ID |
| `subject_id` | 是 | 主体节点 |
| `predicate` | 是 | `schema.json` 中声明的关系 |
| `object_id` | 是 | 客体节点 |
| `evidence_ids` | 是 | 一个或多个证据 ID |
| `evidence_grade` | 是 | 不得强于所引用证据 |
| `assertion_mode` | 是 | `explicit`、`inferred`、`predicted` 或 `hypothesis` |
| `confidence` | 是 | 0-1 |
| `review_status` | 是 | `approved`、`pending` 或 `rejected` |
| `attributes` | 否 | 剂量、炮制、方向、实验条件等关系属性 |

## 主要关系

| 关系 | 起点 | 终点 | 说明 |
|---|---|---|---|
| `HAS_EDITION` | 古籍 | 版本 | 作品版本关系 |
| `HAS_PASSAGE` | 版本 | 原文段 | 版本内原文定位 |
| `RECORDED_IN` | L2-L5 实体 | 原文段 | 实体的古籍出处 |
| `HAS_TREATMENT_METHOD` | 疾病/证候 | 治法 | 明示或推断治法 |
| `REPRESENTATIVE_FORMULA` | 治法 | 方剂 | 代表方 |
| `TREATS` | 治法/方剂 | 疾病/证候 | 直接治疗关系 |
| `HAS_INGREDIENT` | 方剂变体 | 药材 | 组成、剂量与炮制 |
| `VARIANT_OF` | 方剂变体 | 方剂概念 | 同名异方归属 |
| `MODERN_MAPS_TO` | 古代病证/治法 | 烧伤阶段/表型 | 现代术语映射 |
| `MECHANISM_TRANSFER` | 古代实体 | 现代扩展实体 | E4/E5 转移假设 |
| `CONTAINS_COMPOUND` | 药材 | 化合物 | 现代成分证据 |
| `TARGETS` | 化合物 | 靶点 | 靶点证据或预测 |
| `PARTICIPATES_IN` | 靶点 | 通路 | 通路归属 |
| `STUDIED_IN` | 实体 | 研究 | 研究来源 |
| `REPORTS_OUTCOME` | 研究 | 结局 | 疗效或机制结局 |
| `HAS_SAFETY_SIGNAL` | 实体/研究 | 安全信号 | 风险与安全性 |

## 版本文件

| 文件 | 说明 |
|---|---|
| `sources.jsonl` | 来源记录 |
| `nodes.jsonl` | 五层及扩展实体 |
| `evidence.jsonl` | 原文与现代证据 |
| `edges.jsonl` | 断言关系 |
| `graph_metadata.json` | 图版本、父版本和输入证据包哈希 |
| `validation_report.json` | 质量闸结果 |
| `manifest.json` | 文件 SHA-256、计数与内容指纹 |

Neo4j 导出包的每条直接关系都在 `assertion_nodes.csv` 中有同 ID 的断言节点，以便证据回溯。
