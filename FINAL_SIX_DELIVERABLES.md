# 六项最终交付验收

验收日期：2026-08-03

## 总结

六项工程交付均已完成，聚合验收为 `valid=true`、`issues=[]`。当前版本是可在本地终端运行、可复现、可追溯、可导入 Neo4j 的研究证据平台。项目按用户授权规则自动处理，不要求人工审核：置信度低于 0.7 的记录丢弃，其余记录机器批准，并始终记录 `human_reviewed=false`。

“机器批准”只表示通过可复算的数据与置信度门，不代表临床有效、直接靶点结合或湿实验确认。自动图谱不会生成 `TREATS` 关系。

## 1. 二十二部古籍冻结版与古籍图谱 v2

- 冻结语料：22 部、26,949 页、26,949 条 FTS，SQLite `quick_check=ok`。
- 新增 10 部古籍：21,325 页自动纳入，28 页因未达到阈值排除；基础数据库 SHA-256 前后不变。
- 古籍候选图：537 个候选页、1,766 条候选证据、617 个节点、3,339 条关系。
- 自动批准图：21 个古籍来源、1,744 条证据、613 个节点、3,200 条关系；1,744/1,744 来源核验通过。
- 22 条低置信证据和 139 条受影响关系未进入正式图，其中 105 条 `HAS_TREATMENT_METHOD` 因低于阈值被丢弃。

## 2. 扩展评测与同名异方消歧

独立扩展集含 240 题：220 个有答案定位题、20 个无答案题，每部古籍 10 个正例。标签来自冻结 SQLite 的书 ID、物理 PDF 页和证据词，不来自检索排名。

来源定位规划器终测：

| 通道 | Recall@5 | Recall@10 | MRR@10 | 页码定位率 | 拒答准确率 |
| --- | ---: | ---: | ---: | ---: | ---: |
| keyword | 0.9955 | 0.9955 | 0.9795 | 1.0000 | 1.0000 |
| qwen-vector | 0.9955 | 0.9955 | 0.9795 | 1.0000 | 1.0000 |
| qwen-reranked-hybrid | 0.9955 | 0.9955 | 0.9795 | 1.0000 | 1.0000 |

这组指标评测的是“书名 + 主题词”结构化定位规划，不应冒充纯向量能力。唯一失败题是《疡医大全》“烫伤”标签第 47 页的词形/定位异常，已保留在失败案例中。

忍冬汤同名异方机器验收通过：两个 FormulaVariant 共享同一个 FormulaConcept，但物理页、组成指纹和证据 ID 均不同。

- 《医学心悟》第 138 页：金银花四两、甘草三钱；置信度 0.85；原文含“一切内外痈肿皆可立消”“水煎顿服”。
- 《医学心悟》第 227 页：结构化识别为黑料豆二两、土茯苓四两，并记录未定量金银花；置信度 0.95。该页 OCR 仍含个别错字，因此只陈述机器抽取结果，不补写未可靠识别的剂量。

## 3. 检索优化与原五十二题回归

普通语义查询仍使用 Qwen3-Embedding-8B、Qwen3-Reranker-8B、FTS5、FAISS 和 RRF。新增两项可解释规划：明确书名定位时先限制来源并做简繁词扩展；明显超出古籍时代边界的 CRISPR、mRNA、FAISS、ELISA 等问题直接拒答。

原 52 题最终回归：

| 通道 | Recall@5 | Recall@10 | MRR@10 | 页码定位率 | 拒答准确率 |
| --- | ---: | ---: | ---: | ---: | ---: |
| keyword | 0.8696 | 0.8913 | 0.6728 | 1.0000 | 1.0000 |
| qwen-vector | 0.5652 | 0.5870 | 0.4746 | 1.0000 | 1.0000 |
| qwen-reranked-hybrid | 0.9130 | 0.9565 | 0.8200 | 1.0000 | 1.0000 |

混合检索现在硬保护关键词 Top-10，再由 reranker 调整顺序并用向量候选补足空位。因此 hybrid Recall@10 不再低于关键词通道，且相对扩展前记录的 0.8261 提升到 0.9565。

## 4. 现代文献结构化证据

- 扫描 584 篇现代文献，得到 2,238 个候选定位。
- 按烧伤/创面语境和 0.7 门处理后，677 条进入结构化阶段。
- 最终批准 606 条可定位结构化证据，71 条因缺少足够研究类型、结局或安全字段而丢弃。
- 606 条证据覆盖：动物研究 202、体外研究 243、对照临床 93、随机试验 30、计算研究 9、分析化学 11、未明确 18。
- 每条记录保留 DOI、题名、PDF 页码、doc_id、chunk_id、原文、来源 SHA-256 和 chunk 文本 SHA-256；现代 `rag.db` 前后哈希不变。

## 5. 成分-靶点-通路-表型证据链

现代图包含 138 个来源、606 条证据、194 个实体、1,488 条关系，形成 97 条带完整溯源的“成分-靶点-通路-表型”候选机制链。

具体例子：

- 绿原酸，DOI `10.2147/ijn.s594688`，PDF 第 20 页，chunk `d1f8a66764c10a5471365246_p0020_c000`，机器置信度 0.9325。该片段支持抽取 NFKB1、NFE2L2、VEGFA、NLRP3，Nrf2/HO-1 与 NLRP3 通路，以及创面修复、炎症、氧化应激、血管生成和抗菌结局。
- 绿原酸，DOI `10.1111/wrr.70149`，PDF 第 17 页，chunk `4ee627c26df7b0a2641772c8_p0017_c000`，机器置信度 0.8742。该片段形成 TNF、IL6、IL1B、VEGFA、TGFB1，经 TGF-beta/Smad 到创面闭合、胶原沉积和血管生成等表型的候选链。

这些边表示同一可定位证据片段中的明确提及和机制关系信号，不等同于直接结合实验，也不自动转换成治疗结论。

## 6. 总图、导出与发布验收

最终合并图版本为 `tcm-burn-combined-auto70-2026-08-03-v1`：

- 159 个来源、807 个实体、2,350 条证据、4,688 条关系。
- 古籍和现代来源共 2,350/2,350 条证据通过 SQLite 页码/chunk/原文哈希核验。
- release validation、aggregate doctor、Neo4j/JSON-LD 指纹验证全部通过。
- 自动 `TREATS` 边为 0。
- 图内容指纹：`6840888ccf92c761b0392f497ed8eabb54c2c158cee4e853d498c2f665f8771e`。
- Neo4j 导出指纹：`731409cc3cc8f98fed18374606c8d18050095f8f1183c7ed63d25929c867a5cc`。
- 全仓测试：148 passed；release preflight：`valid=true`、`violations=[]`。
- deep doctor：现代 584/9,870/10,983 与古籍 22/26,949/26,949 均健康；两个 Qwen 4096 维索引零缺失、零孤儿，数据库/语料/页面/sidecar 指纹均匹配。

## 运行总闸

私有服务器产物不上传 GitHub。公开仓库只包含代码、测试、命令和脱敏汇总。最终总闸命令为：

```bash
python -m research_pipeline.validate_final_six_release \
  --corpus-build /private/corpus/build_report.json \
  --corpus-doctor /private/corpus/doctor_report.json \
  --ancient-kg /private/release/ancient_kg/pipeline_report.json \
  --formula /private/release/formula_disambiguation_v1.json \
  --extended-evaluation /private/release/extended_retrieval_eval_v4.json \
  --legacy-evaluation /private/release/legacy52_retrieval_final.json \
  --modern-structured /private/release/modern_structured/structured_evidence_report.json \
  --modern-kg /private/release/modern_kg_report.json \
  --combined /private/release/combined_release/combined_release_report.json \
  --preflight /private/release/release_preflight.json \
  --output /private/release/final_six_acceptance.json
```

公开脱敏验收摘要位于 `research_pipeline/reports/final_six_acceptance_v1.json`。
