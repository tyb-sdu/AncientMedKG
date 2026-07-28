from __future__ import annotations

import logging
import sys
from pathlib import Path


def setup_logging(log_path: str | Path, verbose: bool = False) -> logging.Logger:
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("rag_prep")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.DEBUG if verbose else logging.INFO)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger


def progress_line(
    stage: str,
    done: int,
    total: int,
    failed: int = 0,
    extra: str = "",
) -> str:
    remaining = max(total - done, 0)
    pct = (done / total * 100.0) if total else 100.0
    base = (
        f"[{stage}] 完成={done}/{total} ({pct:.1f}%) "
        f"失败={failed} 剩余={remaining}"
    )
    if extra:
        return f"{base} | {extra}"
    return base
