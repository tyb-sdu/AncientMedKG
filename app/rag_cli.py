#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""现代文献本地预处理与 SQLite FTS5 关键词检索入口。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from rag_prep.config import ensure_dirs, load_config  # noqa: E402
from rag_prep.logging_utils import setup_logging  # noqa: E402
from rag_prep.repair import repair_metadata  # noqa: E402
from rag_prep.search import (  # noqa: E402
    build_index,
    doctor,
)
from rag_prep.dual_retrieval import (  # noqa: E402
    doctor_any_corpus,
    query_any_corpus,
    source_any_page,
)
from rag_prep.vector import build_vector_index  # noqa: E402
from rag_prep.gpu_retrieval import build_bge_vector_index  # noqa: E402
from rag_prep.qwen_retrieval import build_qwen_vector_index  # noqa: E402
from rag_prep.ancient_qwen_retrieval import build_ancient_qwen_vector_index  # noqa: E402
from rag_prep.pipeline import (  # noqa: E402
    run_chunk,
    run_extract,
    run_inventory,
    run_status,
    run_validate,
)
FUTURE_HELP = """
现代文献本地混合检索：
  python rag_cli.py embed
  python rag_cli.py query --retrieval hybrid "绿原酸促进创面修复的机制"
  python rag_cli.py source --doc-id DOC_ID --page 15
  python rag_cli.py doctor
"""


def _add_common_args(
    target: argparse.ArgumentParser,
    *,
    suppress_defaults: bool = False,
) -> None:
    scalar_default = argparse.SUPPRESS if suppress_defaults else None
    flag_default = argparse.SUPPRESS if suppress_defaults else False
    target.add_argument(
        "--config",
        default=argparse.SUPPRESS if suppress_defaults else str(ROOT / "config.yaml"),
        help="配置文件路径（默认 ./config.yaml）",
    )
    target.add_argument(
        "--resume",
        action="store_true",
        default=flag_default,
        help="断点续跑（默认开启，可显式指定）",
    )
    target.add_argument(
        "--no-resume",
        action="store_true",
        default=flag_default,
        help="禁用断点续跑",
    )
    target.add_argument("--force", action="store_true", default=flag_default, help="强制重做当前阶段")
    target.add_argument("--doc-id", default=scalar_default, help="只处理指定文献")
    target.add_argument("--limit", type=int, default=scalar_default, help="小规模测试限制数量")
    target.add_argument("--verbose", action="store_true", default=flag_default, help="详细日志")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="中药烧伤项目 - 现代文献 RAG 预处理（本地终端）",
        epilog=FUTURE_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_common_args(p)

    common = argparse.ArgumentParser(add_help=False)
    _add_common_args(common, suppress_defaults=True)

    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("inventory", parents=[common], help="清点 PDF，生成 documents.* 与源校验")
    sub.add_parser("extract", parents=[common], help="逐页提取文本，生成 pages.jsonl")
    sub.add_parser("chunk", parents=[common], help="清洗切块，生成 chunks.jsonl")
    sub.add_parser("repair", parents=[common], help="修复 DOI、稳定 ID 与语言字段")
    sub.add_parser("validate", parents=[common], help="质量检测与源完整性核对")
    sub.add_parser("index", parents=[common], help="创建 SQLite FTS5 本地索引")
    sub.add_parser("embed", parents=[common], help="建立本地 ONNX E5 + FAISS 向量索引")
    sub.add_parser("embed-bge", parents=[common], help="建立服务器 GPU BGE-M3 + FAISS 向量旁路索引")
    sub.add_parser("embed-qwen", parents=[common], help="建立服务器 Qwen3-Embedding-8B + FAISS 高质量索引")
    sub.add_parser("embed-ancient-qwen", parents=[common], help="建立古籍页级 Qwen3-Embedding-8B + FAISS 向量索引")
    sub.add_parser("status", parents=[common], help="查看流水线状态")

    q = sub.add_parser("query", parents=[common], help="本地检索")
    q.add_argument("question")
    q.add_argument("--mode", choices=["modern", "ancient", "dual"], default="modern")
    q.add_argument("--top-k", type=int, default=None)
    q.add_argument(
        "--retrieval",
        choices=[
            "keyword",
            "vector",
            "hybrid",
            "bge-vector",
            "reranked-hybrid",
            "qwen-vector",
            "qwen-reranked-hybrid",
        ],
        default="hybrid",
    )
    s = sub.add_parser("source", parents=[common], help="按 doc_id 和物理页码输出原文")
    s.add_argument("--page", type=int, required=True)
    s.add_argument("--mode", choices=["auto", "modern", "ancient"], default="auto")
    d = sub.add_parser("doctor", parents=[common], help="检查源文件、数据与索引完整性")
    d.add_argument("--deep", action="store_true", help="同时校验冻结版本与 FAISS")
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = build_parser()
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    # 确保相对工程根
    cfg["project_root"] = str(ROOT)
    cfg["_project_root"] = str(ROOT)
    ensure_dirs(cfg)
    logger = setup_logging(cfg["paths"]["pipeline_log"], verbose=args.verbose)

    resume = True
    if args.no_resume:
        resume = False
    if args.resume:
        resume = True

    logger.info(f"命令={args.command} resume={resume} force={args.force} limit={args.limit}")
    common = dict(
        resume=resume,
        force=args.force,
        doc_id=args.doc_id,
        limit=args.limit,
    )

    if args.command == "inventory":
        run_inventory(cfg, logger, **common)
    elif args.command == "extract":
        run_extract(cfg, logger, **common)
    elif args.command == "chunk":
        run_chunk(cfg, logger, **common)
    elif args.command == "repair":
        result = repair_metadata(
            cfg, logger, doc_id=args.doc_id, limit=args.limit
        )
        if result.get("written") and any(
            result.get(key)
            for key in (
                "changed_ids",
                "changed_dois",
                "changed_languages",
                "changed_titles",
            )
        ):
            run_chunk(cfg, logger, resume=False, force=True)
    elif args.command == "validate":
        run_validate(cfg, logger)
    elif args.command == "index":
        build_index(cfg, logger, **common)
    elif args.command == "embed":
        result = build_vector_index(cfg, logger, **common)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "embed-bge":
        result = build_bge_vector_index(cfg, logger, **common)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "embed-qwen":
        result = build_qwen_vector_index(cfg, logger, **common)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "embed-ancient-qwen":
        result = build_ancient_qwen_vector_index(cfg, logger, **common)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "query":
        top_k = args.top_k or int(cfg.get("search", {}).get("default_top_k", 10))
        results = query_any_corpus(
            cfg, args.question, args.retrieval, top_k, mode=args.mode
        )
        if not results:
            print("未检索到匹配片段。")
        for i, row in enumerate(results, 1):
            print(f"[{i}] 融合排名={row.get('fusion_rank') or i}")
            print(f"语料: {row.get('corpus', 'modern')}")
            print(f"题名: {row['title']}")
            print(f"年份: {row['year'] or ''}")
            print(f"DOI: {row['doi'] or ''}")
            print(f"PDF页码: {row['pdf_page']}")
            if row.get("page_label"):
                print(f"页标签: {row['page_label']}")
            print(f"来源文件: {row['source_filename']}")
            print(f"doc_id: {row['doc_id']}")
            print(f"chunk_id: {row['chunk_id']}")
            kw = row.get("keyword_score")
            vec = row.get("vector_score")
            fusion = row.get("fusion_score")
            reranker = row.get("reranker_score")
            print(f"关键词分数: {'' if kw is None else kw}")
            print(f"向量分数: {'' if vec is None else vec}")
            print(f"融合分数: {'' if fusion is None else fusion}")
            print(f"重排分数: {'' if reranker is None else reranker}")
            print(f"原文片段: {row['snippet']}")
            print()
    elif args.command == "source":
        if not args.doc_id:
            parser.error("source 必须指定 --doc-id")
        row = source_any_page(cfg, args.doc_id, args.page, mode=args.mode)
        if not row:
            print("未找到指定文献页。", file=sys.stderr)
            return 1
        print(f"语料: {row.get('corpus', 'modern')}")
        print(f"题名: {row['title']}")
        print(f"年份: {row['year'] or ''}")
        print(f"DOI: {row['doi'] or ''}")
        print(f"PDF页码: {row['pdf_page']}")
        if row.get("page_label"):
            print(f"页标签: {row['page_label']}")
        print(f"来源文件: {row['source_filename']}")
        print(f"doc_id: {row['doc_id']}")
        print("原文:")
        print(row["text"])
    elif args.command == "doctor":
        result = doctor(cfg, logger, deep=args.deep)
        result = doctor_any_corpus(result, cfg, deep=args.deep)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if not result["healthy"]:
            return 1
    elif args.command == "status":
        run_status(cfg, logger)
    else:
        parser.error(f"未知命令: {args.command}")
        return 1

    logger.info(f"阶段完成: {args.command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
