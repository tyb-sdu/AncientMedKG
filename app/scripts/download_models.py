#!/usr/bin/env python
"""Download the revision-locked retrieval models."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


APP_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = APP_ROOT.parent
DEFAULT_LOCK = APP_ROOT / "models.lock.json"


def load_models(lock_path: Path, profile: str) -> list[dict[str, Any]]:
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    rows = payload.get("models")
    if not isinstance(rows, list) or not rows:
        raise ValueError("model lock must contain a non-empty models list")
    selected = [
        row for row in rows if profile == "all" or row.get("profile") == profile
    ]
    if not selected:
        raise ValueError(f"model profile is empty: {profile}")
    required = {"model_id", "revision", "relative_path"}
    if any(not required <= set(row) for row in selected):
        raise ValueError("model lock entry is missing a required field")
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("qwen", "bge", "all"), default="qwen")
    parser.add_argument("--models-dir", type=Path, default=REPOSITORY_ROOT / "models")
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--list", action="store_true", help="仅显示锁定模型，不下载")
    args = parser.parse_args()

    models = load_models(args.lock.resolve(), args.profile)
    snapshot_download = None
    if not args.list:
        try:
            from huggingface_hub import snapshot_download as download
        except ImportError as exc:
            raise RuntimeError(
                "model download requires huggingface_hub; install requirements.txt"
            ) from exc
        snapshot_download = download
    for row in models:
        target = args.models_dir.resolve() / row["relative_path"]
        print(f"{row['model_id']}@{row['revision']} -> {target}")
        if args.list:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        snapshot_download(
            repo_id=row["model_id"],
            revision=row["revision"],
            local_dir=target,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
