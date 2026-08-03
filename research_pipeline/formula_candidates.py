from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping


class FormulaCandidateError(ValueError):
    pass


_DOSE_PATTERN = re.compile(
    r"(?P<value>[〇零一二三四五六七八九十百千半\d]+)"
    r"\s*(?P<unit>[兩两錢钱分斤升合枚片個个撮])"
)
_SEPARATORS = " ：:，,、。；;\t\r\n"
_UNIT_NORMALIZATION = {
    "兩": "两",
    "錢": "钱",
    "個": "个",
}


def load_formula_lexicon(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FormulaCandidateError(f"cannot read formula lexicon {path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("formulas"), list):
        raise FormulaCandidateError("formula lexicon must contain a formulas list")
    seen_formula_ids: set[str] = set()
    for index, formula in enumerate(payload["formulas"]):
        if not isinstance(formula, dict):
            raise FormulaCandidateError(f"formulas[{index}] must be an object")
        formula_id = str(formula.get("formula_id", "")).strip()
        canonical_name = str(formula.get("canonical_name", "")).strip()
        aliases = {str(value).strip() for value in formula.get("aliases", [])}
        if not formula_id or not canonical_name or not aliases:
            raise FormulaCandidateError(
                f"formulas[{index}] requires formula_id, canonical_name, and aliases"
            )
        if formula_id in seen_formula_ids:
            raise FormulaCandidateError(f"duplicate formula_id: {formula_id}")
        seen_formula_ids.add(formula_id)
        seen_herb_ids: set[str] = set()
        for herb_index, herb in enumerate(formula.get("ingredients", [])):
            if not isinstance(herb, dict):
                raise FormulaCandidateError(
                    f"formulas[{index}].ingredients[{herb_index}] must be an object"
                )
            herb_id = str(herb.get("herb_id", "")).strip()
            herb_name = str(herb.get("canonical_name", "")).strip()
            herb_aliases = {str(value).strip() for value in herb.get("aliases", [])}
            if not herb_id or not herb_name or not herb_aliases:
                raise FormulaCandidateError(
                    f"formulas[{index}].ingredients[{herb_index}] is incomplete"
                )
            if herb_id in seen_herb_ids:
                raise FormulaCandidateError(
                    f"duplicate herb_id in {formula_id}: {herb_id}"
                )
            seen_herb_ids.add(herb_id)
        minimum = int(formula.get("minimum_dosed_ingredients", 2))
        if minimum < 2:
            raise FormulaCandidateError(
                f"{formula_id} minimum_dosed_ingredients must be at least 2"
            )
    return payload


def _occurrences(text: str, aliases: list[str]) -> list[tuple[int, int, str]]:
    values: list[tuple[int, int, str]] = []
    for alias in sorted(set(aliases), key=lambda value: (-len(value), value)):
        start = 0
        while True:
            index = text.find(alias, start)
            if index < 0:
                break
            values.append((index, index + len(alias), alias))
            start = index + 1
    retained: list[tuple[int, int, str]] = []
    for start, end, alias in sorted(
        values, key=lambda value: (value[0], -(value[1] - value[0]), value[2])
    ):
        if retained and start < retained[-1][1]:
            continue
        retained.append((start, end, alias))
    return retained


def _ingredient_mentions(
    text: str,
    *,
    offset: int,
    ingredient: Mapping[str, Any],
) -> list[dict[str, Any]]:
    mentions: list[dict[str, Any]] = []
    aliases = [str(value) for value in ingredient.get("aliases", [])]
    for start, end, alias in _occurrences(text, aliases):
        suffix = text[end : end + 16]
        stripped = suffix.lstrip(_SEPARATORS)
        separator_count = len(suffix) - len(stripped)
        dose = _DOSE_PATTERN.match(stripped)
        record: dict[str, Any] = {
            "herb_id": str(ingredient["herb_id"]),
            "canonical_name": str(ingredient["canonical_name"]),
            "surface": alias,
            "start": offset + start,
            "end": offset + end,
            "dose_parsed": False,
        }
        if dose is not None:
            dose_start = end + separator_count
            dose_end = dose_start + len(dose.group(0))
            unit = _UNIT_NORMALIZATION.get(dose.group("unit"), dose.group("unit"))
            record.update(
                {
                    "dose_parsed": True,
                    "dose_value": dose.group("value"),
                    "dose_unit": unit,
                    "dose_text_original": text[dose_start:dose_end],
                    "dose_end": offset + dose_end,
                }
            )
        mentions.append(record)
    return mentions


def _semantic_confidence(
    *,
    ingredient_count: int,
    dosed_count: int,
    expected_count: int,
    preparation_present: bool,
) -> float:
    confidence = 0.4
    confidence += min(0.3, ingredient_count * 0.1)
    confidence += min(0.2, dosed_count * 0.1)
    if preparation_present:
        confidence += 0.05
    if expected_count and dosed_count == expected_count:
        confidence += 0.05
    return round(min(1.0, confidence), 6)


def extract_formula_candidates(
    text: str,
    lexicon: Mapping[str, Any],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for formula in lexicon.get("formulas", []):
        aliases = [str(value) for value in formula.get("aliases", [])]
        after = int(formula.get("window_after_chars", 240))
        minimum_dosed = int(formula.get("minimum_dosed_ingredients", 2))
        per_formula: list[dict[str, Any]] = []
        name_occurrences = _occurrences(text, aliases)
        for occurrence_index, (name_start, name_end, name_surface) in enumerate(
            name_occurrences
        ):
            next_start = (
                name_occurrences[occurrence_index + 1][0]
                if occurrence_index + 1 < len(name_occurrences)
                else len(text)
            )
            # Composition is parsed forward from the explicit heading so a second
            # same-name entry cannot inherit doses from the preceding entry.
            window_start = name_start
            window_end = min(next_start, name_end + after)
            window = text[window_start:window_end]
            mentions: list[dict[str, Any]] = []
            for ingredient in formula.get("ingredients", []):
                mentions.extend(
                    _ingredient_mentions(
                        window,
                        offset=window_start,
                        ingredient=ingredient,
                    )
                )
            nearest_by_herb: dict[str, dict[str, Any]] = {}
            for mention in mentions:
                herb_id = str(mention["herb_id"])
                distance = min(
                    abs(int(mention["start"]) - name_end),
                    abs(name_start - int(mention["end"])),
                )
                rank = (not bool(mention["dose_parsed"]), distance, int(mention["start"]))
                prior = nearest_by_herb.get(herb_id)
                if prior is None or rank < prior["_rank"]:
                    nearest_by_herb[herb_id] = {**mention, "_rank": rank}
            retained_mentions = [
                {key: value for key, value in mention.items() if key != "_rank"}
                for mention in sorted(
                    nearest_by_herb.values(), key=lambda value: int(value["start"])
                )
            ]
            dosed = [value for value in retained_mentions if value["dose_parsed"]]
            if len(dosed) < minimum_dosed:
                continue
            preparation_markers = sorted(
                {
                    str(marker)
                    for marker in formula.get("preparation_markers", [])
                    if str(marker) and str(marker) in window
                }
            )
            confidence = _semantic_confidence(
                ingredient_count=len(retained_mentions),
                dosed_count=len(dosed),
                expected_count=len(formula.get("ingredients", [])),
                preparation_present=bool(preparation_markers),
            )
            composition = [
                {
                    "herb": str(value["canonical_name"]),
                    "dose_value": str(value["dose_value"]),
                    "dose_unit": str(value["dose_unit"]),
                    "dose_text_original": str(value["dose_text_original"]),
                }
                for value in dosed
            ]
            per_formula.append(
                {
                    "formula_id": str(formula["formula_id"]),
                    "canonical_name": str(formula["canonical_name"]),
                    "name_surface": name_surface,
                    "name_start": name_start,
                    "name_end": name_end,
                    "window_start": window_start,
                    "window_end": window_end,
                    "quote": window,
                    "composition": composition,
                    "ingredient_mentions": retained_mentions,
                    "undosed_ingredients": [
                        str(value["canonical_name"])
                        for value in retained_mentions
                        if not value["dose_parsed"]
                    ],
                    "preparation_markers": preparation_markers,
                    "semantic_confidence": confidence,
                    "extraction_policy": "explicit_name_two_dosed_target_herbs_v1",
                    "composition_scope": "target_lexicon_exact_doses_only",
                    "composition_complete": len(dosed)
                    == len(formula.get("ingredients", [])),
                }
            )
        candidates.extend(per_formula)
    return sorted(
        candidates,
        key=lambda value: (int(value["name_start"]), str(value["formula_id"])),
    )
