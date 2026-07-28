from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Any, Iterable, Iterator


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    rows: list[dict[str, Any]] = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def iter_jsonl(path: str | Path) -> Iterator[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return
        yield  # pragma: no cover
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def write_jsonl(
    path: str | Path,
    rows: Iterable[dict[str, Any]],
    append: bool = False,
) -> int:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    count = 0
    with p.open(mode, encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def write_jsonl_atomic(
    path: str | Path, rows: Iterable[dict[str, Any]]
) -> int:
    """先写同目录临时文件，再原子替换，避免中断或并发造成半行。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    count = write_jsonl(tmp, rows, append=False)
    os.replace(tmp, p)
    return count


def append_jsonl(path: str | Path, row: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_csv(path: str | Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            out = {}
            for k in fieldnames:
                v = row.get(k, "")
                if isinstance(v, (list, dict)):
                    out[k] = json.dumps(v, ensure_ascii=False)
                else:
                    out[k] = v if v is not None else ""
            writer.writerow(out)


def load_done_ids(state_path: str | Path) -> set[str]:
    p = Path(state_path)
    if not p.exists():
        return set()
    done: set[str] = set()
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            doc_id = obj.get("doc_id")
            if doc_id:
                done.add(doc_id)
    return done


def mark_done(state_path: str | Path, doc_id: str, extra: dict[str, Any] | None = None) -> None:
    row = {"doc_id": doc_id}
    if extra:
        row.update(extra)
    append_jsonl(state_path, row)


def rewrite_jsonl_excluding(
    path: str | Path,
    exclude_ids: set[str],
    id_key: str = "doc_id",
) -> None:
    """强制重做时，从输出中移除指定 doc_id 的记录。"""
    p = Path(path)
    if not p.exists() or not exclude_ids:
        return
    kept = [r for r in read_jsonl(p) if r.get(id_key) not in exclude_ids]
    write_jsonl(p, kept, append=False)


def remove_state_ids(state_path: str | Path, exclude_ids: set[str]) -> None:
    p = Path(state_path)
    if not p.exists() or not exclude_ids:
        return
    kept = [r for r in read_jsonl(p) if r.get("doc_id") not in exclude_ids]
    write_jsonl(p, kept, append=False)
