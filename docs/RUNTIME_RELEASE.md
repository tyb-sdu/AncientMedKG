# 正式运行快照

GitHub Release `runtime-2026.08.04` 保存与本仓库代码版本对应的真实运行产物。该快照用于直接复核项目结果；`docs/REPRODUCE.md` 则用于从原始资料重新构建。

## 下载

在仓库 Releases 页面下载以下六个资产以及 `SHA256SUMS`、`FILE_SIZES.txt`：

- `AncientMedKG-runtime-2026.08.04-modern-rag.tar.gz`
- `AncientMedKG-runtime-2026.08.04-modern-qwen-index.tar`
- `AncientMedKG-runtime-2026.08.04-ancient-rag.tar.gz`
- `AncientMedKG-runtime-2026.08.04-ancient-qwen-index.tar`
- `AncientMedKG-runtime-2026.08.04-evidence-evaluation.tar.gz`
- `AncientMedKG-runtime-2026.08.04-build-logs.tar.gz`

安装 GitHub CLI 后也可执行：

```bash
gh release download runtime-2026.08.04 --repo tyb-sdu/AncientMedKG
sha256sum -c SHA256SUMS
```

Windows PowerShell 可逐项执行 `Get-FileHash -Algorithm SHA256 <文件名>`，并与 `release/runtime-2026.08.04/SHA256SUMS` 比较。

## 恢复目录

```bash
mkdir -p app/data ancient_ocr/data release_output

tar -xzf AncientMedKG-runtime-2026.08.04-modern-rag.tar.gz -C app/data
tar -xf AncientMedKG-runtime-2026.08.04-modern-qwen-index.tar -C app/data

tar -xzf AncientMedKG-runtime-2026.08.04-ancient-rag.tar.gz -C ancient_ocr/data
tar -xf AncientMedKG-runtime-2026.08.04-ancient-qwen-index.tar -C ancient_ocr/data

tar -xzf AncientMedKG-runtime-2026.08.04-evidence-evaluation.tar.gz -C release_output
tar -xzf AncientMedKG-runtime-2026.08.04-build-logs.tar.gz -C release_output
```

现代快照包含 584 篇资料、9,870 页和 10,983 个文本块。古籍快照包含 22 部古籍和 26,949 页。证据评测包包含候选图与批准图、JSONL、Neo4j CSV/Cypher、JSON-LD、来源验证、doctor、release preflight、52 题及扩展题检索结果、自动纳入与淘汰记录、现代结构化证据和成分发现运行结果。

## 数据边界

运行快照不包含受版权或许可限制的原始 PDF，也不包含模型权重、服务器地址、账号、密钥或本地绝对路径。四个含运行绝对路径的文本报告在发布副本中使用占位符脱敏；数据库、证据 JSONL、CSV 和索引二进制保持原始内容。快照用于工程复核，不构成药效、安全性或临床结论。
