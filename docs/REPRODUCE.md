# AncientMedRAG 复现指南

本文档描述如何仅使用本仓库和使用者自行准备的原始资料，构建现代文献库、古籍页库、Qwen 向量索引、双库检索与证据图谱。所有示例路径均相对于仓库根目录，不依赖固定主机、账号、端口或绝对目录。

## 1. 环境要求

基础要求：

- Git 2.40 或更高版本。
- Python 3.11；Python 3.12 可运行 CPU 部分。
- SQLite 编译时启用 FTS5。
- 足够保存原始资料、模型和运行产物的磁盘空间。

Qwen 主检索的参考配置为两张 CUDA GPU，嵌入模型和重排模型分别位于 `cuda:0` 与 `cuda:1`。也可以在 `app/config.yaml` 中修改设备，或设置：

```bash
export RAG_QWEN_EMBEDDING_DEVICE=cuda:0
export RAG_QWEN_RERANKER_DEVICE=cuda:1
```

古籍 OCR 建议使用独立虚拟环境，避免 PaddlePaddle 与 PyTorch 的 CUDA 依赖互相影响。

## 2. 安装依赖

RAG 与图谱环境：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-gpu.txt
```

只使用关键词和 CPU 预处理时，可安装 `requirements.txt`。古籍 OCR 环境单独安装：

```bash
python3.11 -m venv .venv-ocr
source .venv-ocr/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-ocr.txt
```

若客户 CUDA 版本不同，应按 PyTorch 与 PaddlePaddle 官方安装器选择匹配的 GPU wheel，其余包版本保持 requirements 文件所列版本。

## 3. 运行时目录

```text
corpus/
  modern_pdf/                 现代文献 PDF
  modern_metadata.csv         可选元数据表
  ancient_pdf/raw_flat/       基础古籍 PDF
  kanripo/                    脚本下载的定本仓库
models/                       锁定模型权重
runtime/
  modern/                     现代 JSONL、SQLite 与向量索引
  ancient_base/               基础古籍 OCR 页库
  ancient/                    合并 Kanripo 后的正式古籍页库
  ancient_ocr_work/           OCR 中间页结果
  reports/                    评测报告
  kg/                         图谱和导出
```

这些目录已被 `.gitignore` 排除。`modern_metadata.csv` 可省略；提供时支持以下列：`original_filename`、`new_filename`、`title`、`year`、`doi`、`title_source`、`year_source`。

`app/config.yaml` 使用仓库相对路径。任意 `paths.<name>` 都可通过 `RAG_<NAME>` 环境变量覆盖，例如：

```bash
export RAG_MODERN_PDF_DIR=/mnt/corpus/modern_pdf
export RAG_DATABASE=/mnt/runtime/modern/rag.db
```

## 4. 下载锁定模型

```bash
source .venv/bin/activate
python app/scripts/download_models.py --profile qwen
python app/scripts/download_models.py --profile qwen --list
```

下载器读取 `app/models.lock.json`，按固定 Hugging Face revision 写入 `models/Qwen/`。BGE 对照模型可用 `--profile bge` 下载。

## 5. 构建现代文献 RAG

将 PDF 放入 `corpus/modern_pdf/`，从仓库根目录依次执行：

```bash
source .venv/bin/activate
python app/rag_cli.py --config app/config.yaml inventory
python app/rag_cli.py --config app/config.yaml extract
python app/rag_cli.py --config app/config.yaml chunk
python app/rag_cli.py --config app/config.yaml repair
python app/rag_cli.py --config app/config.yaml validate
python app/rag_cli.py --config app/config.yaml index
python app/rag_cli.py --config app/config.yaml embed-qwen --resume
python app/scripts/freeze_corpus.py --config app/config.yaml
```

`inventory` 记录源文件 SHA-256；`extract` 保留物理 PDF 页码；`chunk` 不跨页切块；`repair` 规范化 DOI、语言与稳定 ID；`validate` 比较处理前后源文件哈希；`index` 构建 SQLite FTS5；`embed-qwen` 构建归一化 4,096 维 FAISS `IndexFlatIP`。所有长任务默认支持断点续跑，只有确认要丢弃当前阶段产物时才使用 `--force`。

## 6. 构建基础古籍页库

将基础古籍 PDF 放入 `corpus/ancient_pdf/raw_flat/`。在 OCR 环境中运行：

```bash
source .venv-ocr/bin/activate
python ancient_ocr/ancient_cli.py \
  --corpus-dir corpus/ancient_pdf/raw_flat \
  --data-dir runtime/ancient_base \
  --output-dir runtime/ancient_ocr_work \
  --model-home models/ocr inventory

python ancient_ocr/ancient_cli.py \
  --corpus-dir corpus/ancient_pdf/raw_flat \
  --data-dir runtime/ancient_base \
  --output-dir runtime/ancient_ocr_work \
  --model-home models/ocr run \
  --device gpu:0 --shard-index 0 --shard-count 1

python ancient_ocr/ancient_cli.py \
  --corpus-dir corpus/ancient_pdf/raw_flat \
  --data-dir runtime/ancient_base \
  --output-dir runtime/ancient_ocr_work finalize

python ancient_ocr/ancient_cli.py \
  --corpus-dir corpus/ancient_pdf/raw_flat \
  --data-dir runtime/ancient_base \
  --output-dir runtime/ancient_ocr_work doctor --deep
```

多 GPU 运行时，每张卡使用不同的 `--shard-index`，所有进程保持相同 `--shard-count`。全部分片完成后只执行一次 `finalize`。

## 7. 获取并导入 Kanripo 定本

回到 RAG 环境。`fetch` 会克隆清单中的 10 个公开仓库，并切换到 `kanripo_sources_v1.json` 锁定的提交：

```bash
source .venv/bin/activate
python ancient_ocr/kanripo_auto_ingest.py fetch \
  --sources-root corpus/kanripo

python ancient_ocr/kanripo_auto_ingest.py build \
  --base-database runtime/ancient_base/ancient_rag.db \
  --sources-root corpus/kanripo \
  --output-dir runtime/ancient \
  --confidence-threshold 0.7

python ancient_ocr/kanripo_auto_ingest.py doctor \
  --output-dir runtime/ancient \
  --sources-root corpus/kanripo

python ancient_ocr/kanripo_auto_ingest.py relevance-audit \
  --output-dir runtime/ancient

python ancient_ocr/reorder_ancient_pages.py \
  --database runtime/ancient/ancient_rag.db \
  --output runtime/ancient/pages_layout_v2.jsonl \
  --no-resume
```

`build` 从基础数据库生成独立副本，不改写基础库。定本文本按 `<pb:...>` 页锚点分割，记录来源仓库、提交、快照哈希、页 ID、物理页、文本哈希、置信度分量和机器批准策略。只有置信度 `>= 0.7` 的页进入正式页库。版面重排脚本为全部页生成只读 sidecar；没有 OCR 几何信息的定本页保留原始页文本。

## 8. 构建古籍 Qwen 索引

```bash
python app/rag_cli.py --config app/config.yaml embed-ancient-qwen --resume
python app/rag_cli.py --config app/config.yaml doctor --deep
```

正式配置指向 `runtime/ancient/`。若只构建基础古籍，应在复制的配置文件中把 `ancient_*` 路径改到 `runtime/ancient_base/`。

## 9. 查询、原文回读和完整性检查

```bash
python app/rag_cli.py query --mode modern \
  --retrieval qwen-reranked-hybrid --top-k 10 "绿原酸促进创面修复"

python app/rag_cli.py query --mode ancient \
  --retrieval qwen-reranked-hybrid --top-k 10 "忍冬 汤火伤"

python app/rag_cli.py query --mode dual \
  --retrieval qwen-reranked-hybrid --top-k 10 "金银花 甘草 烧伤"

python app/rag_cli.py source --mode auto --doc-id DOC_ID --page 20
python app/rag_cli.py doctor --deep
```

查询结果必须包含语料类型、题名、页码、文件名、稳定 ID、分通道分数和原文片段。原文回读不存在时返回非零退出码；`doctor --deep` 任一必需指纹、条目数或 ID 映射不一致时返回非零退出码。

## 10. 复算古籍指标

固定 52 题：

```bash
mkdir -p runtime/reports
python app/scripts/evaluate_ancient_retrieval.py \
  --config app/config.yaml \
  --output runtime/reports/ancient52.json
```

22 部古籍来源定位集：

```bash
python -m research_pipeline.build_extended_evaluation \
  --database runtime/ancient/ancient_rag.db \
  --output runtime/reports/ancient_locator_questions.json \
  --report runtime/reports/ancient_locator_build.json

python app/scripts/evaluate_ancient_retrieval.py \
  --config app/config.yaml \
  --questions runtime/reports/ancient_locator_questions.json \
  --output runtime/reports/ancient_locator_metrics.json
```

评测器先回读标签页并核对证据词，再运行检索。扩展集是书名与主题词的来源定位回归，不应解释为盲法临床语义评测或纯向量性能。

## 11. 自动证据与知识图谱

首先校验冻结的烧伤术语本体和《医学心悟》忍冬汤同名异方证据链：

```bash
python -m research_pipeline.validate_domain_assets
```

该命令核对 39 条术语、123 个词形、自动批准阈值、三个来源页、两个组成指纹及 E1/E5 关系边界。校验失败时不要继续构建发布图谱。

古籍证据抽取、阈值处理、源核验和导出：

```bash
python -m research_pipeline.run_automatic_ancient_kg \
  --database runtime/ancient/ancient_rag.db \
  --output-root runtime/kg/ancient \
  --candidate-graph-version ancient-candidate-v1 \
  --approved-graph-version ancient-approved-v1 \
  --threshold 0.7
```

现代证据扫描与结构化：

```bash
python -m discovery_pipeline scan-corpus \
  --database runtime/modern/rag.db \
  --output runtime/discovery/scan

python -m discovery_pipeline automatic-loci \
  --loci runtime/discovery/scan/compound_loci.jsonl \
  --database runtime/modern/rag.db \
  --output runtime/discovery/approved \
  --threshold 0.7

python discovery_pipeline/structured_evidence.py \
  --approved-loci runtime/discovery/approved/approved_loci.jsonl \
  --database runtime/modern/rag.db \
  --output-dir runtime/discovery/structured \
  --threshold 0.7

python -m discovery_pipeline.automatic_modern_kg \
  --structured-evidence runtime/discovery/structured/approved_structured_evidence.jsonl \
  --catalog discovery_pipeline/data/compound_candidates_v1.json \
  --database runtime/modern/rag.db \
  --output-bundle runtime/kg/modern_bundle.json \
  --output-report runtime/kg/modern_bundle_report.json \
  --graph-version modern-approved-v1 \
  --threshold 0.7

python -m knowledge_graph build \
  --input runtime/kg/modern_bundle.json \
  --output runtime/kg/modern/graph --release
```

合并并导出：

```bash
python -m research_pipeline.finalize_combined_release \
  --ancient-graph runtime/kg/ancient/approved_graph \
  --modern-graph runtime/kg/modern/graph \
  --ancient-database runtime/ancient/ancient_rag.db \
  --modern-database runtime/modern/rag.db \
  --output-root runtime/kg/combined \
  --graph-version combined-v1
```

图谱字段定义见 `knowledge_graph/DATA_DICTIONARY.md`，发现管线字段定义见 `discovery_pipeline/DATA_DICTIONARY.md`。图谱发布会重新打开两个 SQLite 数据库，并按稳定 ID、页码、chunk、原文和哈希逐条验证证据。

## 12. 交付前检查

```bash
python -m compileall -q app ancient_ocr knowledge_graph research_pipeline discovery_pipeline
python ancient_ocr/release_preflight.py --repository .
git diff --check
```

复现成功的最低标准是：SQLite `quick_check=ok`，JSONL 与数据库计数相等，FTS5 无缺失或孤儿记录，源文件哈希未变化，必需向量索引健康，`source` 可按稳定 ID 与物理页回读原文。精确参考数量还要求输入文件集合和锁定模型完全一致。
