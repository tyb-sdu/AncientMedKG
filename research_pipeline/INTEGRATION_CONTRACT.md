# Research Pipeline 集成契约

## 所有权

- `research_pipeline/`：烧伤术语本体、忍冬汤证据包、专项题、查询规划、方案合规矩阵与 KG 转换入口。
- `knowledge_graph/`：五层图谱 schema、稳定 ID、构建、验证、来源反查、Neo4j/JSON-LD 导出和 doctor。
- `ancient_ocr/`、`app/`：本阶段只读，不由研究层改写。

## 输入

`build_kg_bundle.py` 接受：

1. `rendongtang_evidence_v1.json`；
2. 与发布版本一致的 `ancient_rag.db`；
3. `books` 与 `pages` 表中的来源 SHA、书 ID、页 ID、物理页和 OCR 文本。

数据库以 SQLite `mode=ro` 打开，并先执行 `PRAGMA quick_check`。任一书名、页码或证据词不一致即停止生成。

## 输出

输出严格采用 `knowledge_graph/examples/evidence_bundle.example.json` 的四段结构：

- `sources`：真实古籍来源必须含 `file_sha256` 和 `attributes.book_id`；
- `entities`：两个忍冬汤同名方分别生成来源绑定的 `FormulaVariant`，均含 `formula_name`、`composition`、`source_locator`；
- `evidence`：E1 定位同时含 `page_id`、`physical_page` 和 `page_text_sha256`；
- `assertions`：每条边引用证据，所有真实记录在影像终审前均为 `pending`。

输出是确定性的：同一证据 JSON 与同一 SQLite 字节内容应生成相同 evidence bundle、稳定节点 ID 和断言 ID。运行时间仅出现在下游 manifest，不参与内容 ID。

## 强制边界

- 禁止 `FormulaVariant -[TREATS]-> BurnPhenotype`，除非另有明确烧伤古籍原词且经批准。
- 当前忍冬汤烧伤关联仅为 `MECHANISM_TRANSFER`，证据等级 `E5`，断言方式 `hypothesis`，`direct_ancient_evidence=false`。
- `text_verified_requires_final_image_signoff` 和 `same_name_variant_requires_image_review` 只映射为 `pending`，绝不映射为 `approved`。
- 草案可用 `--allow-unreleased` 导出供检查，但不能作为发布图谱。

## 验收

集成至少执行：

```bash
python -m pytest -q
python -m research_pipeline.validate_assets --database <vNext-db> --output <report>
python -m research_pipeline.evaluate_specialized_retrieval --config <vNext-config> --output <report>
python -m knowledge_graph build --input <bundle> --output <draft>
python -m knowledge_graph verify-sources --graph <draft> --ancient-database <vNext-db> --output <report>
python -m knowledge_graph export-neo4j --graph <draft> --output <neo4j-dir> --allow-unreleased
```

另须证明 `validate --release` 因待审状态失败，且失败码只来自尚未批准的证据/断言，而非结构、来源或类型错误。
