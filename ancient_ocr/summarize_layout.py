#!/usr/bin/env python
"""Summarize a layout-reordered ancient page sidecar."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = SCRIPT_DIR / "data" / "pages_layout_v2.jsonl"


def summarize(path: Path) -> dict[str, object]:
    columns: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    directions: Counter[str] = Counter()
    rows = 0
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            item = json.loads(line)
            rows += 1
            columns[str(item.get("column_count", 0))] += 1
            statuses[str(item.get("layout_status", ""))] += 1
            directions[str(item.get("reading_direction", ""))] += 1
    return {
        "input": str(path),
        "rows": rows,
        "column_counts": dict(columns),
        "layout_status": dict(statuses),
        "reading_direction": dict(directions),
        "source_data_modified": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Summarize layout sidecar")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    args = parser.parse_args()
    print(json.dumps(summarize(args.input.resolve()), ensure_ascii=False, indent=2))
