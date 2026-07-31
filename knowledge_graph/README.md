# AncientMedKG 五层知识图谱

本目录实现项目书要求的“古籍—疾病—治法—方剂—药材”五层知识图谱交付链。它独立于现有 RAG/OCR 数据库，只读核验证据，不修改原 PDF、`rag.db`、`ancient_rag.db` 或向量索引。

## 数据边界

核心五层：

| 层 | 实体 |
|---|---|
| L1 古籍 | `ClassicWork`、`Edition`、`Passage` |
| L2 疾病 | `Disease`、`Syndrome`、`BurnStage`、`BurnPhenotype` |
| L3 治法 | `TreatmentMethod` |
| L4 方剂 | `FormulaConcept`、`FormulaVariant` |
| L5 药材 | `Herb` |

`Compound`、`Target`、`Pathway`、`Study`、`Outcome`、`SafetySignal` 属于 `EXT` 现代研究扩展域，不冒充五层古籍直接证据。

证据等级固定为 E1-E5：

- E1：古籍原文或原始研究中的直接证据。
- E2：权威整理、药典、指南或系统综述。
- E3：多来源交叉支持。
- E4：数据库、语义或网络计算推断。
- E5：专家假设。

`MECHANISM_TRANSFER` 只能使用 E4/E5，并且只能标记为 `inferred`、`predicted` 或 `hypothesis`。古籍没有直接烧伤术语时，禁止用 `TREATS -> BurnPhenotype` 伪装成直接疗效。

## 为什么方剂分为概念和变体

`FormulaConcept` 表示名称层面的方剂概念；`FormulaVariant` 绑定组成指纹和来源定位。稳定 ID 同时包含：

1. 规范方名；
2. 规范化组成与剂量的 SHA-256；
3. 来源、版本和页码定位。

因此同名但组成不同、或出现在不同来源页的方剂不会误合并。各变体用 `VARIANT_OF` 指向方剂概念。

## 输入证据包

公开的合成样例位于 `examples/evidence_bundle.example.json`。真实证据包应由研究流程生成，至少包含：

- `sources`：PDF 文件 SHA-256、DOI、古籍版本和数据库内部 `book_id`/`doc_id`；
- `entities`：实体类型、规范名、别名、外部数据库 ID 和类型特异属性；
- `evidence`：页码、`page_id`/`chunk_id`、原文、原文 SHA-256、等级和复核状态；
- `assertions`：主语、谓词、宾语、证据列表、断言方式、置信度和复核状态。

真实古籍引文、受版权保护的 PDF、数据库和索引均不得提交到公开仓库。

## 构建与验收

从仓库根目录运行：

```bash
python -m knowledge_graph build \
  --input research_pipeline/output/kg_evidence_bundle.json \
  --output private_build/kg_v1 \
  --release

python -m knowledge_graph validate \
  --graph private_build/kg_v1 \
  --release \
  --output private_build/kg_v1/release_validation.json

python -m knowledge_graph verify-sources \
  --graph private_build/kg_v1 \
  --ancient-database ancient_ocr/data/versions/vl_vnext_2026-07-31/ancient_rag.db \
  --modern-database app/data/rag.db \
  --output private_build/kg_v1/source_verification.json

python -m knowledge_graph export-neo4j \
  --graph private_build/kg_v1 \
  --output private_build/kg_v1/neo4j
```

`build` 和 `export-neo4j` 默认拒绝覆盖已有版本文件。修订数据时创建新版本目录，并在输入 `metadata.parent_version` 记录父版本。

发布门包括：

- 关键路径关系证据可追溯率 100%；
- 其他关系证据可追溯率不低于 98%；
- 方剂变体组成和来源定位完整率 100%；
- E1 必须有原文和 PDF 页定位；
- release 中证据与关系必须已批准；
- 所有关系端点、类型、等级和断言方式符合 `schema.json`；
- 每个输出文件都有 SHA-256，加载时自动复核。

## Neo4j 导入

导出目录是自包含包，包含实体、来源、证据、断言、直接关系、溯源关系、约束、示例查询和 JSON-LD。

Neo4j 5.x 离线导入示例：

```bash
neo4j-admin database import full ancientmedkg \
  --nodes=nodes.csv \
  --nodes=source_nodes.csv \
  --nodes=evidence_nodes.csv \
  --nodes=assertion_nodes.csv \
  --relationships=relationships.csv \
  --relationships=provenance_relationships.csv

cypher-shell -d ancientmedkg -f constraints.cypher
cypher-shell -d ancientmedkg -f example_queries.cypher
```

直接关系便于检索；同一关系还会被重建为 `Assertion` 节点，通过 `SUPPORTED_BY` 回到 `EvidenceSpan`，再通过 `EXTRACTED_FROM` 回到带 SHA-256 的来源文档。

## 回滚

系统不原地修改数据版本。回滚只需让运行配置重新指向上一版本的 Neo4j 数据库或导入目录，并核对上一版本 manifest 的 `content_fingerprint`。原 RAG/OCR 数据库始终保持不变。

## 测试

```bash
python -m unittest discover -s knowledge_graph/tests -v
python -m pytest knowledge_graph/tests -q
```

样例数据仅用于结构测试，不代表医学结论。真实科研结论是否成立仍取决于实体抽取复核、现代研究质量评价和后续实验验证。
