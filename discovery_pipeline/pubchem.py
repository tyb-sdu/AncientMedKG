from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable


PROPERTY_NAMES = (
    "Title",
    "MolecularFormula",
    "MolecularWeight",
    "InChIKey",
    "CanonicalSMILES",
    "IsomericSMILES",
)
PUG_REST_BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"


class PubChemResolutionError(ValueError):
    pass


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_json(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(payload)


def property_url(name: str) -> str:
    encoded_name = urllib.parse.quote(name, safe="")
    properties = ",".join(PROPERTY_NAMES)
    return (
        f"{PUG_REST_BASE}/compound/name/{encoded_name}/property/"
        f"{properties}/JSON"
    )


def fetch_json(
    url: str,
    *,
    timeout_seconds: float = 30.0,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> tuple[dict[str, Any], bytes]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "AncientMedKG/0.1 (research data resolution)"},
    )
    with opener(request, timeout=timeout_seconds) as response:
        payload = response.read()
    return json.loads(payload.decode("utf-8")), payload


def _resolve_candidate_with_payload(
    candidate: dict[str, Any],
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> tuple[dict[str, Any], bytes]:
    name = str(candidate["canonical_name"])
    url = property_url(name)
    response, payload = fetch_json(url, opener=opener)
    properties = response.get("PropertyTable", {}).get("Properties", [])
    if len(properties) != 1:
        raise PubChemResolutionError(
            f"{candidate['candidate_id']} returned {len(properties)} PubChem records"
        )
    record = dict(properties[0])
    cid = int(record["CID"])
    expected_cid = candidate.get("expected_pubchem_cid")
    if expected_cid is not None and cid != int(expected_cid):
        raise PubChemResolutionError(
            f"{candidate['candidate_id']} expected CID {expected_cid}, got {cid}"
        )
    if not str(record.get("InChIKey", "")).strip():
        raise PubChemResolutionError(
            f"{candidate['candidate_id']} has no PubChem InChIKey"
        )
    result = {
        **candidate,
        "pubchem": {
            "cid": cid,
            "title": record.get("Title", ""),
            "molecular_formula": record.get("MolecularFormula", ""),
            "molecular_weight": record.get("MolecularWeight"),
            "inchikey": record.get("InChIKey", ""),
            "canonical_smiles": record.get(
                "ConnectivitySMILES", record.get("CanonicalSMILES", "")
            ),
            "isomeric_smiles": record.get(
                "SMILES", record.get("IsomericSMILES", "")
            ),
            "query_url": url,
            "response_sha256": _sha256_bytes(payload),
            "identity_status": "resolved_requires_curator_review",
        },
    }
    return result, payload


def resolve_candidate(
    candidate: dict[str, Any],
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    result, _ = _resolve_candidate_with_payload(candidate, opener=opener)
    return result


def resolve_catalog(
    catalog: dict[str, Any],
    *,
    cache_dir: Path | None = None,
    delay_seconds: float = 0.2,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    resolved: list[dict[str, Any]] = []
    candidates = [dict(value) for value in catalog.get("candidates", [])]
    candidate_ids = [str(value.get("candidate_id", "")) for value in candidates]
    if not candidates or any(not value for value in candidate_ids):
        raise PubChemResolutionError("catalog candidates require candidate_id")
    if len(candidate_ids) != len(set(candidate_ids)):
        raise PubChemResolutionError("catalog contains duplicate candidate_id values")
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        expected_cache_paths = []
        for candidate in candidates:
            stem = str(candidate.get("candidate_id", "")).replace(":", "_")
            expected_cache_paths.extend(
                [cache_dir / f"{stem}.json", cache_dir / f"{stem}.response.json"]
            )
        existing = [path for path in expected_cache_paths if path.exists()]
        if existing:
            raise FileExistsError(f"refusing to overwrite PubChem cache file {existing[0]}")
    for index, candidate in enumerate(candidates):
        if not str(candidate.get("canonical_name", "")).strip():
            raise PubChemResolutionError(
                f"{candidate['candidate_id']} requires canonical_name"
            )
        result, raw_payload = _resolve_candidate_with_payload(
            candidate, opener=opener
        )
        resolved.append(result)
        if cache_dir is not None:
            stem = candidate["candidate_id"].replace(":", "_")
            path = cache_dir / f"{stem}.json"
            temporary = path.with_name(f".{path.name}.tmp")
            temporary.write_text(
                json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, path)
            raw_path = cache_dir / f"{stem}.response.json"
            raw_temporary = raw_path.with_name(f".{raw_path.name}.tmp")
            raw_temporary.write_bytes(raw_payload)
            os.replace(raw_temporary, raw_path)
        if delay_seconds and index + 1 < len(candidates):
            time.sleep(delay_seconds)
    identity_fingerprint = _sha256_json(
        [
            {
                "candidate_id": value["candidate_id"],
                "cid": value["pubchem"]["cid"],
                "inchikey": value["pubchem"]["inchikey"],
                "response_sha256": value["pubchem"]["response_sha256"],
            }
            for value in sorted(resolved, key=lambda item: item["candidate_id"])
        ]
    )
    return {
        "schema_version": 1,
        "catalog_id": catalog.get("catalog_id", ""),
        "catalog_sha256": _sha256_json(catalog),
        "source_service": "PubChem PUG REST",
        "resolution_status": "machine_resolved_requires_curator_review",
        "resolved_count": len(resolved),
        "identity_fingerprint": identity_fingerprint,
        "candidates": resolved,
    }
