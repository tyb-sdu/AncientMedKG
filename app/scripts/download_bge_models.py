#!/usr/bin/env python
"""Download the GPU retrieval models into a project-owned directory."""

from __future__ import annotations

import argparse
from pathlib import Path

from huggingface_hub import snapshot_download


MODELS = (
    "BAAI/bge-m3",
    "BAAI/bge-reranker-v2-m3",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models-dir", type=Path, required=True)
    parser.add_argument("--revision", default=None)
    args = parser.parse_args()

    for model_id in MODELS:
        target = args.models_dir / model_id
        target.parent.mkdir(parents=True, exist_ok=True)
        snapshot_download(
            repo_id=model_id,
            local_dir=target,
            revision=args.revision,
        )
        print(f"ready: {model_id} -> {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
