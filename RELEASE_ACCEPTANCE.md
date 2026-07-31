# 中药烧伤 RAG 交付验收

验收日期：2026-07-31

## 发布结论

本项目按“113 页候选直接纳入版本化 vNext、原数据库保留回滚”的方案进入最终验收。人工逐页对照不再是本次里程碑的发布前置条件。

发布范围包括现代文献与古籍双库检索代码、可追溯页码定位、评测题集、OCR 审计与候选生成工具、复核覆盖层工具和测试。PDF、OCR 正文、数据库、向量索引、模型、候选 JSON、渲染页图和日志均不进入公开仓库。

## 验收证据

| 项目 | 结果 |
|---|---|
| 现代文献 | 584 篇、9,870 页、10,983 个 chunk |
| 古籍 | 12 部、5,624 页 |
| 古籍关键词检索 | Recall@10 0.8913，页码定位率 1.0 |
| 古籍 Qwen 向量检索 | Recall@10 0.6739，页码定位率 1.0 |
| 古籍 Qwen 重排混合 | Recall@10 0.7826，页码定位率 1.0 |
| P1 VLM 候选 | 113/113，失败 0 |
| 候选清单完整性 | 113 行，`valid=true`，`issues=[]` |
| 服务器完整仓库测试 | 68 passed |
| 深层健康检查 | 现代库、古籍 vNext、FTS 与全部向量指纹均健康 |
| 发布汇总门 | `valid=true`，`issues=[]` |

## vNext 纳入决策

- 113 页全部写入 vNext 推广审计记录。
- 105 个非空候选采用 PaddleOCR-VL 文本；8 个空候选保留原 OCR，避免清空页面。
- 原 `ancient_rag.db` 不修改，作为整库回滚点；vNext 使用独立 SQLite 数据库和独立索引目录。
- vNext 同时导出与数据库正文一致的 `pages.jsonl`；索引配置必须指向该文件，避免沿用旧语料指纹。
- 每页保留来源 SHA-256、原文本 SHA-256、候选文本 SHA-256、实际生效文本 SHA-256、质量标记和推广模式。
- vNext 重建 FTS 与向量索引后必须重新运行健康检查和 52 题检索回归。

## 已知限制

- 低置信古籍页仍可能存在字符识别错误，返回结果必须保留原 PDF 物理页码以便追溯。
- 当前无答案题仅 6 个，尚不足以支持可靠拒答阈值；非空检索结果不能直接解释为医学结论。
- VLM 候选中的 52 页带风险标记；本次按用户决定直接纳入 vNext，标记仍保留在推广审计中。

## 服务器发布门

在实际 Git 仓库执行：

```bash
python ancient_ocr/promote_vl_candidates.py \
  --manifest /path/to/vl_candidate_manifest_p1_final.csv \
  --candidate-root /path/to/paddleocr_vl_candidates_v3_p1_final \
  --database ancient_ocr/data/ancient_rag.db \
  --output-database ancient_ocr/data/versions/vl_vnext_2026-07-31/ancient_rag.db \
  --output-pages-jsonl ancient_ocr/data/versions/vl_vnext_2026-07-31/pages.jsonl
python -m pytest -q
python ancient_ocr/release_preflight.py --repository . \
  --output /tmp/public_release_preflight.json
git status --short
git diff --check
```

将 vNext 配置中的 `ancient_database` 指向新数据库、`ancient_pages_jsonl` 指向新导出文件，并将所有古籍 Qwen 索引路径指向全新目录。vNext 数据验收必须满足：113 条推广记录由 105 条候选采用和 8 条空候选回退组成、源库前后 SHA-256 相同、SQLite `quick_check=ok`、数据库页面数、FTS 行数与导出 JSONL 行数均为 5,624、页码定位率保持 1.0、关键词 Recall@10 不低于当前基线 0.8913。随后只有在全量测试通过、`release_preflight` 返回 `valid=true`、`git diff --check` 无错误，并确认暂存内容仅含代码、测试和公开说明后，才提交并推送 GitHub 里程碑。

`rag_cli.py doctor --deep` 会同时打印日志和 JSON。先把原始输出提取为纯 JSON，再与 promotion、52 题评测和 preflight 一起进入汇总门：

```bash
$PY app/rag_cli.py doctor --config app/config.vl_vnext_2026-07-31.yaml --deep \
  > /tmp/doctor_vnext.raw.txt
$PY ancient_ocr/extract_json_report.py /tmp/doctor_vnext.raw.txt \
  --output /tmp/doctor_vnext.json
```

四个输入均为服务器实际产物，验证通过才允许进入提交步骤：

```bash
python ancient_ocr/validate_vnext_release.py \
  --promotion-report /path/to/ancient_rag_promotion_report.json \
  --doctor-report /path/to/doctor_vnext.json \
  --evaluation-report /path/to/ancient_retrieval_eval_vnext.json \
  --preflight-report /path/to/public_release_preflight.json \
  --output /path/to/vnext_release_validation.json
```
