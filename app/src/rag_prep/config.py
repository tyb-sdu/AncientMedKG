from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


def load_config(config_path: str | Path) -> dict[str, Any]:
    path = Path(config_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")
    with path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    configured_root = Path(cfg.get("project_root", "."))
    project_root = (
        configured_root.resolve()
        if configured_root.is_absolute()
        else (path.parent / configured_root).resolve()
    )
    cfg["_config_path"] = str(path)
    cfg["_project_root"] = str(project_root)

    paths = cfg.setdefault("paths", {})
    for key, value in list(paths.items()):
        p = Path(value)
        if not p.is_absolute():
            paths[key] = str((project_root / p).resolve())
        else:
            paths[key] = str(p)

    # Any configured path can be overridden without editing the tracked YAML.
    # Example: paths.modern_pdf_dir -> RAG_MODERN_PDF_DIR.
    for key in list(paths):
        env_name = f"RAG_{key.upper()}"
        if value := os.environ.get(env_name):
            paths[key] = str(Path(value).expanduser().resolve())

    qwen = cfg.setdefault("qwen", {})
    for key in ("embedding_device", "reranker_device"):
        env_name = f"RAG_QWEN_{key.upper()}"
        if value := os.environ.get(env_name):
            qwen[key] = value
    return cfg


def ensure_dirs(cfg: dict[str, Any]) -> None:
    for key in ("data_dir", "logs_dir", "state_dir"):
        Path(cfg["paths"][key]).mkdir(parents=True, exist_ok=True)
