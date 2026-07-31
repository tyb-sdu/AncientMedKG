#!/usr/bin/env python
"""Extract the last complete JSON object from a command log into a report file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def last_json_object(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    offset = 0
    objects: list[dict[str, Any]] = []
    while True:
        start = text.find("{", offset)
        if start < 0:
            break
        try:
            payload, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            offset = start + 1
            continue
        if isinstance(payload, dict):
            objects.append(payload)
        offset = end
    if not objects:
        raise ValueError("command log does not contain a complete JSON object")
    return objects[-1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract the last JSON object from a command log")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = last_json_object(args.input.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
