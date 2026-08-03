#!/usr/bin/env python
"""Build an independent ancient-text database with auto-accepted Kanripo pages."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CATALOG = SCRIPT_DIR / "kanripo_sources_v1.json"
POLICY_ID = "auto_include_gt_0_7"
CONFIDENCE_MODEL = "kanripo_text_quality_v1"
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
PUNCTUATION = set("，。！？；：、（）《》〈〉【】〔〕［］“”‘’…—·,.!?;:()[]{}<>")
PAGE_ANCHOR_RE = re.compile(r"<pb:([^>]+)>\s*¶?")
ORG_LINE_RE = re.compile(r"(?m)^#\+[^\n]*\n?")
DIRECT_BURN_TERMS = (
    "湯火傷",
    "汤火伤",
    "治湯火",
    "治汤火",
    "湯火灼",
    "汤火灼",
    "熱油及火燒",
    "热油及火烧",
    "火燒傷",
    "火烧伤",
    "湯潑火傷",
    "汤泼火伤",
    "湯火瘡",
    "汤火疮",
)
EXTERNAL_MEDICINE_TERMS = (
    "外科",
    "瘡",
    "疮",
    "瘍",
    "疡",
    "疽",
    "創",
    "创",
    "傷",
    "伤",
    "腫",
    "肿",
)


@dataclass(frozen=True)
class ParsedPage:
    source_file: str
    page_anchor: str
    text: str
    confidence: float
    confidence_components: dict[str, float | int]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def atomic_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            stream.write("\n")
    temporary.replace(path)


def load_catalog(path: Path) -> dict[str, Any]:
    catalog = json.loads(path.read_text(encoding="utf-8-sig"))
    books = catalog.get("books")
    if not isinstance(books, list) or not books:
        raise ValueError("catalog must contain a non-empty books list")
    repos = [str(book.get("repo") or "") for book in books]
    if any(not repo for repo in repos) or len(set(repos)) != len(repos):
        raise ValueError("catalog repository identifiers must be non-empty and unique")
    return catalog


def git_commit(repo_dir: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _run_git(*args: str, cwd: Path | None = None) -> str:
    command = ["git"]
    if cwd is not None:
        command.extend(("-C", str(cwd)))
    command.extend(args)
    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def fetch_sources(catalog_path: Path, sources_root: Path) -> dict[str, Any]:
    """Clone each public source and detach it at the catalogued commit."""
    catalog = load_catalog(catalog_path.resolve())
    sources_root = sources_root.resolve()
    sources_root.mkdir(parents=True, exist_ok=True)
    repository_base = str(
        catalog.get("repository_base") or "https://github.com/kanripo"
    ).rstrip("/")
    sources: list[dict[str, str]] = []
    for book in catalog["books"]:
        repo = str(book["repo"])
        expected = str(book["expected_commit"])
        target = sources_root / repo
        source_url = str(
            book.get("repository_url") or f"{repository_base}/{repo}.git"
        )
        if target.exists():
            if not (target / ".git").is_dir():
                raise FileExistsError(
                    f"source target is not a Git repository: {target}"
                )
            if _run_git("status", "--porcelain", cwd=target):
                raise RuntimeError(f"source repository has local changes: {target}")
            if git_commit(target) != expected:
                _run_git("fetch", "origin", expected, cwd=target)
                _run_git("checkout", "--detach", expected, cwd=target)
        else:
            _run_git("clone", "--no-checkout", source_url, str(target))
            _run_git("checkout", "--detach", expected, cwd=target)
        actual = git_commit(target)
        if actual != expected:
            raise RuntimeError(
                f"source commit mismatch for {repo}: expected {expected}, got {actual}"
            )
        sources.append(
            {
                "repo": repo,
                "title": str(book["title"]),
                "source_url": source_url,
                "commit": actual,
            }
        )
    report = {
        "schema_version": 1,
        "healthy": len(sources) == len(catalog["books"]),
        "source_count": len(sources),
        "sources": sources,
    }
    atomic_json(sources_root / "source_fetch_manifest.json", report)
    return report


def snapshot_sha256(repo_dir: Path) -> str:
    digest = hashlib.sha256()
    files = [repo_dir / "Readme.org", *sorted(repo_dir.glob("*.txt"))]
    for path in files:
        if not path.is_file():
            continue
        relative = path.relative_to(repo_dir).as_posix().encode("utf-8")
        data = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def normalize_transcription(text: str) -> str:
    text = ORG_LINE_RE.sub("", text)
    text = PAGE_ANCHOR_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("¶", "\n")
    lines = [re.sub(r"[ \t\u3000]+", "", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def text_quality_confidence(text: str) -> tuple[float, dict[str, float | int]]:
    """Return an auditable text-quality score, not an OCR probability."""
    visible = [char for char in text if not char.isspace()]
    if not visible:
        return 0.0, {
            "visible_chars": 0,
            "cjk_ratio": 0.0,
            "length_score": 0.0,
            "integrity_score": 0.0,
        }
    cjk_count = sum(bool(CJK_RE.fullmatch(char)) for char in visible)
    semantic = [char for char in visible if char not in PUNCTUATION]
    cjk_ratio = cjk_count / max(len(semantic), 1)
    length_score = min(len(visible) / 40.0, 1.0)
    bad_count = sum(char == "\ufffd" or ord(char) < 32 for char in visible)
    integrity_score = max(0.0, 1.0 - bad_count / len(visible) * 20.0)
    # Page anchoring contributes 0.50; content quality contributes the other 0.50.
    score = 0.50 + 0.25 * min(cjk_ratio, 1.0) + 0.15 * length_score + 0.10 * integrity_score
    return round(max(0.0, min(score, 1.0)), 6), {
        "visible_chars": len(visible),
        "cjk_ratio": round(cjk_ratio, 6),
        "length_score": round(length_score, 6),
        "integrity_score": round(integrity_score, 6),
    }


def parse_source_file(path: Path, preferred_edition: str) -> list[ParsedPage]:
    raw = path.read_text(encoding="utf-8")
    matches = [
        match
        for match in PAGE_ANCHOR_RE.finditer(raw)
        if f"_{preferred_edition}_" in match.group(1)
    ]
    pages: list[ParsedPage] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        text = normalize_transcription(raw[match.end() : end])
        confidence, components = text_quality_confidence(text)
        pages.append(
            ParsedPage(
                source_file=path.name,
                page_anchor=match.group(1),
                text=text,
                confidence=confidence,
                confidence_components=components,
            )
        )
    return pages


def parse_book(repo_dir: Path, preferred_edition: str) -> list[ParsedPage]:
    pages: list[ParsedPage] = []
    for path in sorted(repo_dir.glob("*.txt")):
        pages.extend(parse_source_file(path, preferred_edition))
    anchors = [page.page_anchor for page in pages]
    if len(anchors) != len(set(anchors)):
        raise ValueError(f"duplicate page anchors in {repo_dir.name}")
    return pages


def page_payloads(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT page_id, book_id, physical_page, pdf_page_label, text,
               reading_direction, average_confidence, low_confidence, payload_json
        FROM pages ORDER BY book_id, physical_page
        """
    ).fetchall()
    payloads: list[dict[str, Any]] = []
    for row in rows:
        payload = json.loads(row["payload_json"] or "{}")
        if not isinstance(payload, dict):
            payload = {"original_payload": payload}
        payload.update(
            {
                "book_id": row["book_id"],
                "physical_page": row["physical_page"],
                "pdf_page_label": row["pdf_page_label"],
                "text": row["text"],
                "reading_direction": row["reading_direction"],
            }
        )
        payloads.append(payload)
    return payloads


def book_rows(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute("SELECT * FROM books ORDER BY book_id")]


def copy_database(source: Path, target: Path) -> None:
    source_connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    target_connection = sqlite3.connect(target)
    try:
        source_connection.backup(target_connection)
    finally:
        target_connection.close()
        source_connection.close()


def build_auto70_copy(
    *,
    base_database: Path,
    sources_root: Path,
    output_dir: Path,
    catalog_path: Path = DEFAULT_CATALOG,
    confidence_threshold: float = 0.7,
    force: bool = False,
) -> dict[str, Any]:
    if not 0.0 <= confidence_threshold < 1.0:
        raise ValueError("confidence threshold must be >= 0 and < 1")
    base_database = base_database.resolve()
    sources_root = sources_root.resolve()
    output_dir = output_dir.resolve()
    if not base_database.is_file():
        raise FileNotFoundError(f"base database not found: {base_database}")
    output_database = output_dir / "ancient_rag.db"
    output_pages = output_dir / "pages.jsonl"
    if output_dir.exists() and any(output_dir.iterdir()) and not force:
        raise FileExistsError(f"output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    temporary_database = output_dir / "ancient_rag.db.tmp"
    temporary_database.unlink(missing_ok=True)
    for path in (output_database, output_pages):
        if force:
            path.unlink(missing_ok=True)

    base_sha_before = file_sha256(base_database)
    copy_database(base_database, temporary_database)
    catalog = load_catalog(catalog_path)
    connection = sqlite3.connect(temporary_database)
    connection.row_factory = sqlite3.Row
    source_manifest: list[dict[str, Any]] = []
    accepted_total = 0
    excluded_total = 0
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        for item in catalog["books"]:
            repo_name = str(item["repo"])
            repo_dir = sources_root / repo_name
            if not repo_dir.is_dir():
                raise FileNotFoundError(f"source repository missing: {repo_dir}")
            commit = git_commit(repo_dir)
            expected_commit = str(item.get("expected_commit") or "")
            if expected_commit and commit != expected_commit:
                raise ValueError(
                    f"{repo_name}: expected commit {expected_commit}, found {commit}"
                )
            snapshot_sha = snapshot_sha256(repo_dir)
            book_id = f"ancient:{snapshot_sha[:20]}"
            all_pages = parse_book(repo_dir, str(item["preferred_edition"]))
            accepted = [page for page in all_pages if page.confidence > confidence_threshold]
            excluded = [page for page in all_pages if page.confidence <= confidence_threshold]
            if not accepted:
                raise ValueError(f"{repo_name}: no pages exceeded {confidence_threshold}")
            connection.execute(
                "INSERT INTO books VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    book_id,
                    item["title"],
                    f"{repo_name}@{commit}",
                    f"https://github.com/kanripo/{repo_name}/tree/{commit}",
                    snapshot_sha,
                    len(accepted),
                    "kanripo_curated_transcription_v1",
                ),
            )
            for physical_page, page in enumerate(accepted, start=1):
                page_id = f"{book_id}:p{physical_page:06d}"
                payload = {
                    "schema_version": 1,
                    "page_id": page_id,
                    "book_id": book_id,
                    "title": item["title"],
                    "physical_page": physical_page,
                    "pdf_page_label": page.page_anchor,
                    "text": page.text,
                    "reading_direction": "vertical-rtl",
                    "source_sha256": snapshot_sha,
                    "quality": {
                        "average_confidence": page.confidence,
                        "low_confidence": False,
                        "confidence_model": CONFIDENCE_MODEL,
                        "confidence_semantics": "heuristic text-quality score; not OCR probability",
                        "components": page.confidence_components,
                    },
                    "provenance": {
                        "provider": "Kanripo",
                        "repository": f"https://github.com/kanripo/{repo_name}",
                        "source_commit": commit,
                        "snapshot_sha256": snapshot_sha,
                        "source_file": page.source_file,
                        "page_anchor": page.page_anchor,
                        "preferred_edition": item["preferred_edition"],
                    },
                    "acceptance": {
                        "policy_id": POLICY_ID,
                        "threshold": confidence_threshold,
                        "operator": ">",
                        "review_status": "auto_accepted_unreviewed",
                        "human_image_reviewed": False,
                        "human_review_required_by_policy": False,
                    },
                }
                payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                connection.execute(
                    "INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        page_id,
                        book_id,
                        physical_page,
                        page.page_anchor,
                        page.text,
                        "vertical-rtl",
                        page.confidence,
                        0,
                        payload_json,
                    ),
                )
                connection.execute(
                    "INSERT INTO pages_fts VALUES (?, ?, ?, ?)",
                    (page_id, book_id, item["title"], page.text),
                )
            accepted_total += len(accepted)
            excluded_total += len(excluded)
            source_manifest.append(
                {
                    "repo": repo_name,
                    "title": item["title"],
                    "book_id": book_id,
                    "preferred_edition": item["preferred_edition"],
                    "source_commit": commit,
                    "snapshot_sha256": snapshot_sha,
                    "source_url": f"https://github.com/kanripo/{repo_name}/tree/{commit}",
                    "project_fit": item["project_fit"],
                    "parsed_pages": len(all_pages),
                    "accepted_pages": len(accepted),
                    "excluded_pages": len(excluded),
                    "minimum_accepted_confidence": min(page.confidence for page in accepted),
                    "maximum_excluded_confidence": (
                        max(page.confidence for page in excluded) if excluded else None
                    ),
                    "review_status": "auto_accepted_unreviewed",
                }
            )
        connection.commit()
        counts = {
            "books": connection.execute("SELECT COUNT(*) FROM books").fetchone()[0],
            "pages": connection.execute("SELECT COUNT(*) FROM pages").fetchone()[0],
            "fts_rows": connection.execute("SELECT COUNT(*) FROM pages_fts").fetchone()[0],
        }
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        if quick_check != "ok" or counts["pages"] != counts["fts_rows"]:
            raise RuntimeError(f"output database verification failed: {counts}, {quick_check}")
        pages = page_payloads(connection)
        books = book_rows(connection)
    except Exception:
        connection.rollback()
        connection.close()
        temporary_database.unlink(missing_ok=True)
        raise
    else:
        connection.close()

    base_sha_after = file_sha256(base_database)
    if base_sha_before != base_sha_after:
        temporary_database.unlink(missing_ok=True)
        raise RuntimeError("base database changed while building the independent copy")
    temporary_database.replace(output_database)
    atomic_jsonl(output_pages, pages)
    atomic_jsonl(output_dir / "books.jsonl", books)
    atomic_json(output_dir / "kanripo_source_manifest.json", source_manifest)
    report = {
        "schema_version": 1,
        "policy_id": POLICY_ID,
        "confidence_threshold": confidence_threshold,
        "confidence_operator": ">",
        "confidence_model": CONFIDENCE_MODEL,
        "confidence_semantics": "heuristic text-quality score; not OCR probability",
        "base_database": str(base_database),
        "base_database_sha256_before": base_sha_before,
        "base_database_sha256_after": base_sha_after,
        "base_database_modified": False,
        "new_book_count": len(source_manifest),
        "new_pages_accepted": accepted_total,
        "new_pages_excluded": excluded_total,
        "output_counts": counts,
        "sqlite_quick_check": quick_check,
        "output_database": str(output_database),
        "output_database_sha256": file_sha256(output_database),
        "output_pages_jsonl": str(output_pages),
        "output_pages_jsonl_sha256": file_sha256(output_pages),
        "source_manifest": str(output_dir / "kanripo_source_manifest.json"),
        "review_status": "auto_accepted_unreviewed",
        "human_review_performed": False,
    }
    atomic_json(output_dir / "build_report.json", report)
    return report


def doctor(output_dir: Path, sources_root: Path | None = None) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    database = output_dir / "ancient_rag.db"
    pages_path = output_dir / "pages.jsonl"
    manifest_path = output_dir / "kanripo_source_manifest.json"
    issues: list[str] = []
    if not all(path.is_file() for path in (database, pages_path, manifest_path)):
        return {"healthy": False, "issues": ["required output files are missing"]}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        counts = {
            "books": connection.execute("SELECT COUNT(*) FROM books").fetchone()[0],
            "pages": connection.execute("SELECT COUNT(*) FROM pages").fetchone()[0],
            "fts_rows": connection.execute("SELECT COUNT(*) FROM pages_fts").fetchone()[0],
        }
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        auto_rows = connection.execute(
            "SELECT average_confidence, payload_json FROM pages WHERE payload_json LIKE ?",
            (f'%"policy_id":"{POLICY_ID}"%',),
        ).fetchall()
    jsonl_rows = sum(1 for line in pages_path.open(encoding="utf-8") if line.strip())
    if quick_check != "ok":
        issues.append(f"SQLite quick_check: {quick_check}")
    if counts["pages"] != counts["fts_rows"] or counts["pages"] != jsonl_rows:
        issues.append("database, FTS, and pages.jsonl row counts differ")
    for row in auto_rows:
        payload = json.loads(row["payload_json"])
        acceptance = payload.get("acceptance", {})
        if not float(row["average_confidence"]) > float(acceptance.get("threshold", 0.7)):
            issues.append(f"{payload.get('page_id')}: confidence is not above threshold")
        if acceptance.get("review_status") != "auto_accepted_unreviewed":
            issues.append(f"{payload.get('page_id')}: invalid review status")
    source_checks: list[dict[str, Any]] = []
    if sources_root is not None:
        for item in manifest:
            repo_dir = sources_root.resolve() / item["repo"]
            actual_commit = git_commit(repo_dir) if repo_dir.is_dir() else None
            actual_snapshot = snapshot_sha256(repo_dir) if repo_dir.is_dir() else None
            healthy = (
                actual_commit == item["source_commit"]
                and actual_snapshot == item["snapshot_sha256"]
            )
            source_checks.append(
                {
                    "repo": item["repo"],
                    "commit_matches": actual_commit == item["source_commit"],
                    "snapshot_matches": actual_snapshot == item["snapshot_sha256"],
                    "healthy": healthy,
                }
            )
            if not healthy:
                issues.append(f"{item['repo']}: source fingerprint mismatch")
    result = {
        "healthy": not issues,
        "issues": issues,
        "counts": counts,
        "pages_jsonl_rows": jsonl_rows,
        "sqlite_quick_check": quick_check,
        "auto_accepted_rows": len(auto_rows),
        "source_checks": source_checks,
        "database_sha256": file_sha256(database),
        "pages_jsonl_sha256": file_sha256(pages_path),
    }
    atomic_json(output_dir / "doctor_report.json", result)
    return result


def _term_evidence(
    connection: sqlite3.Connection,
    book_id: str,
    terms: Iterable[str],
    *,
    sample_limit: int = 5,
) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT page_id, physical_page, pdf_page_label, text
        FROM pages WHERE book_id = ? ORDER BY physical_page
        """,
        (book_id,),
    ).fetchall()
    matched_terms: set[str] = set()
    matching_pages = 0
    samples: list[dict[str, Any]] = []
    for row in rows:
        text = str(row["text"] or "")
        page_terms = [term for term in terms if term and term in text]
        if not page_terms:
            continue
        matching_pages += 1
        matched_terms.update(page_terms)
        if len(samples) >= sample_limit:
            continue
        first = page_terms[0]
        start = max(text.find(first) - 45, 0)
        samples.append(
            {
                "page_id": row["page_id"],
                "physical_page": row["physical_page"],
                "page_anchor": row["pdf_page_label"],
                "matched_terms": page_terms,
                "snippet": text[start : start + 140].replace("\n", ""),
            }
        )
    return {
        "matched_terms": sorted(matched_terms),
        "matching_pages": matching_pages,
        "samples": samples,
    }


def relevance_audit(output_dir: Path) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    database = output_dir / "ancient_rag.db"
    manifest_path = output_dir / "kanripo_source_manifest.json"
    if not database.is_file() or not manifest_path.is_file():
        raise FileNotFoundError("database or Kanripo source manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    books: list[dict[str, Any]] = []
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        for item in manifest:
            catalog_fit = _term_evidence(
                connection, item["book_id"], item.get("project_fit", [])
            )
            burn_context = _term_evidence(
                connection, item["book_id"], DIRECT_BURN_TERMS
            )
            external_medicine = _term_evidence(
                connection, item["book_id"], EXTERNAL_MEDICINE_TERMS
            )
            books.append(
                {
                    "repo": item["repo"],
                    "title": item["title"],
                    "book_id": item["book_id"],
                    "source_commit": item["source_commit"],
                    "project_fit_terms": item.get("project_fit", []),
                    "catalog_fit": catalog_fit,
                    "burn_context": burn_context,
                    "external_medicine": external_medicine,
                    "has_alignment_evidence": bool(
                        catalog_fit["matching_pages"]
                        or burn_context["matching_pages"]
                        or external_medicine["matching_pages"]
                    ),
                }
            )
    report = {
        "schema_version": 1,
        "new_book_count": len(books),
        "books_with_alignment_evidence": sum(
            item["has_alignment_evidence"] for item in books
        ),
        "books_with_burn_context": sum(
            item["burn_context"]["matching_pages"] > 0 for item in books
        ),
        "books_with_external_medicine_evidence": sum(
            item["external_medicine"]["matching_pages"] > 0 for item in books
        ),
        "healthy": bool(books) and all(item["has_alignment_evidence"] for item in books),
        "books": books,
        "scope_note": (
            "Term hits demonstrate project alignment and page-level traceability; "
            "they do not establish clinical efficacy or formula equivalence."
        ),
    }
    atomic_json(output_dir / "relevance_report.json", report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    fetch = subparsers.add_parser("fetch")
    fetch.add_argument("--sources-root", type=Path, required=True)
    fetch.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    build = subparsers.add_parser("build")
    build.add_argument("--base-database", type=Path, required=True)
    build.add_argument("--sources-root", type=Path, required=True)
    build.add_argument("--output-dir", type=Path, required=True)
    build.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    build.add_argument("--confidence-threshold", type=float, default=0.7)
    build.add_argument("--force", action="store_true")
    check = subparsers.add_parser("doctor")
    check.add_argument("--output-dir", type=Path, required=True)
    check.add_argument("--sources-root", type=Path)
    relevance = subparsers.add_parser("relevance-audit")
    relevance.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "fetch":
        result = fetch_sources(args.catalog, args.sources_root)
    elif args.command == "build":
        result = build_auto70_copy(
            base_database=args.base_database,
            sources_root=args.sources_root,
            output_dir=args.output_dir,
            catalog_path=args.catalog,
            confidence_threshold=args.confidence_threshold,
            force=args.force,
        )
    elif args.command == "doctor":
        result = doctor(args.output_dir, args.sources_root)
    else:
        result = relevance_audit(args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("healthy", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
