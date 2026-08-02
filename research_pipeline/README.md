# 烧伤古籍研究流程

`research_pipeline/` 保存项目方案中位于 OCR/RAG 与五层知识图谱之间的研究层资产。它不修改原 PDF、SQLite、JSONL、向量索引或 `knowledge_graph/` 的 schema。

## 已交付资产

- `data/burn_ontology_v1.json`：39 个烧伤古籍检索术语，区分直接证据、迁移证据、上下文和排除项。
- `data/rendongtang_evidence_v1.json`：《医学心悟》忍冬汤两种同名异方及第 137 页上下文，古籍事实与烧伤迁移假说分层。
- `data/proposal_compliance_v1.json`：按项目方案逐项记录已完成、部分完成、未开始和实验阻塞项。
- `evaluation/rendongtang_questions_v1.json`：12 道有答案题和 3 道证据边界题。
- `query_planner.py`：繁简归一、受控术语规划、同名异方消歧和边界拒答。
- `build_kg_bundle.py`：从只读古籍库生成 `knowledge_graph` 可消费的真实证据草案。
- `build_ancient_candidate_kg.py`：扫描全部古籍，生成与正式图谱隔离的待复核候选层。

## 静态校验

```bash
python -m research_pipeline.validate_assets
python -m pytest research_pipeline/tests -q
```

## 真实数据库校验

```bash
python -m research_pipeline.validate_assets \
  --database ancient_ocr/data/versions/vl_vnext_2026-07-31/ancient_rag.db \
  --output research_pipeline/output/acceptance_20260731/asset_validation.json
```

## 专项检索

原始三通道基线与研究规划层必须分别报告。规划层不会改写 `app/` 检索器，也不使用题集中的期望页码；它读取题面、受控术语和同名异方语义特征，对越界医学结论直接拒答。

```bash
python -m research_pipeline.evaluate_specialized_retrieval \
  --config app/config.vl_vnext_2026-07-31.yaml \
  --output research_pipeline/output/acceptance_20260731/specialized_retrieval.json
```

## KG 草案

转换器从 SQLite 只读补齐 PDF SHA-256、`book_id`、`page_id`、物理页、页文 SHA-256 和最小充分原文。输出含真实原文，必须留在私有运行目录，不提交公开仓库。

```bash
python -m research_pipeline.build_kg_bundle \
  --database ancient_ocr/data/versions/vl_vnext_2026-07-31/ancient_rag.db \
  --output research_pipeline/output/acceptance_20260731/kg_evidence_bundle.json

python -m knowledge_graph build \
  --input research_pipeline/output/acceptance_20260731/kg_evidence_bundle.json \
  --output research_pipeline/output/acceptance_20260731/kg_draft

python -m knowledge_graph verify-sources \
  --graph research_pipeline/output/acceptance_20260731/kg_draft \
  --ancient-database ancient_ocr/data/versions/vl_vnext_2026-07-31/ancient_rag.db \
  --output research_pipeline/output/acceptance_20260731/kg_draft/source_verification.json

python -m knowledge_graph export-neo4j \
  --graph research_pipeline/output/acceptance_20260731/kg_draft \
  --output research_pipeline/output/acceptance_20260731/kg_draft/neo4j \
  --allow-unreleased
```

当前真实证据统一为 `pending`，需要关键页影像双人签署。不得用 `--release` 绕过审核；发布校验在签署前失败是预期行为。

## 12 部古籍候选层

候选层用于扩大内容覆盖，不替代上面的高质量忍冬汤样板图。它只读扫描
SQLite，要求直接烧伤词通过上下文与排除语境检查；迁移页必须同时命中至少
两个本体层。精确原文、候选清单和图文件必须存放在 Git 仓库外。

```bash
python -m research_pipeline.build_ancient_candidate_kg \
  --database ancient_ocr/data/versions/vl_vnext_2026-07-31/ancient_rag.db \
  --ontology research_pipeline/data/burn_ontology_v1.json \
  --output-bundle /private/kg/ancient-candidate-v1/kg_evidence_bundle.json \
  --output-manifest /private/kg/ancient-candidate-v1/candidate_pages.jsonl \
  --graph-version ancient-candidate-2026-07-31-v1 \
  --parent-version accepted-sample-2026-07-31

python -m knowledge_graph build \
  --input /private/kg/ancient-candidate-v1/kg_evidence_bundle.json \
  --output /private/kg/ancient-candidate-v1/graph

python -m knowledge_graph verify-sources \
  --graph /private/kg/ancient-candidate-v1/graph \
  --ancient-database ancient_ocr/data/versions/vl_vnext_2026-07-31/ancient_rag.db

python -m knowledge_graph export-neo4j \
  --graph /private/kg/ancient-candidate-v1/graph \
  --output /private/kg/ancient-candidate-v1/neo4j \
  --allow-unreleased
```

首个真实版本扫描 12 部、5,624 页，保留 212 个直接烧伤候选页和 102 个
创面迁移候选页，形成 369 个实体、1,316 条可定位证据和 2,234 条断言。
1,316 条引文全部回指原数据库成功，草案结构错误为 0。所有自动抽取内容均为
`pending`；发布门只因未审批证据和关系而阻断。脱敏验收结果见
`reports/ancient_candidate_kg_v1.json`。

同页病证与治法只生成 E5 `HAS_TREATMENT_METHOD` 候选假说，不生成
`TREATS`。候选层审核通过后也应按版本增量合并，不能覆盖已验收样板图。

## 科学边界

- 第 138 页内外痈肿二味方与第 227 页杨梅结毒方是组成不同的 `FormulaVariant`。
- 古籍事实只支持原文病证、组成、原量和内服煎法，不直接支持烧伤疗效、外敷或现代克数。
- 忍冬汤到烧伤表型只允许 `E4/E5 + MECHANISM_TRANSFER`，当前为未评分的 E5 假说。
- 规划层 15 题通过不替代 500 条双人标注、专家审核、成分与机制分析或实验验证。
