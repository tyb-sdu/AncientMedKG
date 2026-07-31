# 忍冬汤现代文献双盲标注指南

## 操作原则

1. A、B两位审阅人必须独立填写各自CSV，不互看答案。
2. 每行都要打开原PDF并定位到`pdf_page`；`snippet`只用于初筛，不能单独作为证据。
3. 不得修改题名、DOI、文件名、页码、`doc_id`、`chunk_id`或任何SHA-256字段。
4. 所有标注列均为必填；不确定时使用`uncertain`，不要猜测。
5. 两份表完成后运行合并命令；第三位仲裁者复核全部500条，包括A/B完全一致的条目。
6. `reviewed_at`和`adjudicated_at`统一使用`YYYY-MM-DD`格式。

## 字段取值

| 字段 | 允许值与含义 |
| --- | --- |
| `full_text_checked` | `yes`已核全文；`no`未核全文 |
| `source_page_verified` | `yes`已对照PDF页；`no`未完成页码核验 |
| `relevance_label` | `direct_burn`直接烧伤；`direct_wound`直接创面；`mechanistic_support`机制支持；`formula_exposure`方剂/暴露；`safety`安全性；`background_only`仅背景；`irrelevant`不相关；`uncertain`不确定 |
| `study_type` | `randomized_trial`随机试验；`controlled_clinical`对照临床；`observational_clinical`观察性临床；`animal`动物；`in_vitro`体外；`analytical_chemistry`分析化学；`systematic_review`系统综述；`narrative_review`叙述综述；`computational`计算研究；`other`其他；`uncertain`不确定 |
| `evidence_direction` | `supportive`支持；`null`无效；`adverse`不利/有害；`mixed`混合；`not_applicable`不适用；`uncertain`不确定 |
| `supports_c1_source` | 是否支持药材来源：`yes`/`no`/`uncertain` |
| `supports_c2_exposure` | 是否支持方剂可提取/暴露：`yes`/`no`/`uncertain` |
| `supports_c3_burn_wound` | 是否支持烧伤/创面相关性：`yes`/`no`/`uncertain` |
| `supports_c4_target_pathway` | 是否支持实验靶点/通路：`yes`/`no`/`uncertain` |
| `supports_c5_safety` | 是否支持安全性/可验证性：`yes`/`no`/`uncertain` |
| `confidence_1_to_5` | 1至5整数；5为最高把握 |
| `reviewed_at` | 本行审阅日期，格式`YYYY-MM-DD` |
| `notes` | 简短记录排除原因、研究限制或需要仲裁的问题 |

## 仲裁与放行

仲裁者必须不同于A、B审阅人，并填写`adjudicated_at`及所有`final_*`列。`adjudication_decision`只能是：

- `approve`：已核全文与PDF页，研究类型明确，属于可用证据，且置信度至少3分；
- `reject`：不是可用科学证据；
- `needs_more_information`：需要更清晰全文、补充来源或专科复核。

标注批准只代表该文献定位可作为证据记录，不等于化合物C0身份通过，也不自动证明靶点、通路、疗效、安全性或烧伤治疗结论。
