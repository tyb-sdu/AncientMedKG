from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def load_schema() -> dict[str, Any]:
    path = Path(__file__).with_name("schema.json")
    return json.loads(path.read_text(encoding="utf-8"))


def node_spec(entity_type: str) -> dict[str, Any]:
    schema = load_schema()
    try:
        return dict(schema["node_types"][entity_type])
    except KeyError as exc:
        raise ValueError(f"unknown entity_type: {entity_type}") from exc


def relationship_spec(predicate: str) -> dict[str, Any]:
    schema = load_schema()
    try:
        return dict(schema["relationship_types"][predicate])
    except KeyError as exc:
        raise ValueError(f"unknown predicate: {predicate}") from exc


def schema_version() -> str:
    return str(load_schema()["schema_version"])
