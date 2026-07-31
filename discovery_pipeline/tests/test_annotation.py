from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from discovery_pipeline.annotation import (
    ANNOTATION_FIELDS,
    AnnotationError,
    finalize_annotation_adjudication,
    merge_annotation_reviews,
    prepare_annotation_batch,
    validate_annotation_batch,
)


def _canonical_sha(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class AnnotationTests(unittest.TestCase):
    def _inputs(self, root: Path) -> tuple[Path, Path, Path]:
        catalog = {
            "schema_version": 1,
            "catalog_id": "test-catalog",
            "candidates": [
                {
                    "candidate_id": f"compound:c{candidate}",
                    "canonical_name": f"compound {candidate}",
                }
                for candidate in range(3)
            ],
        }
        records = []
        contexts = ("burn_context", "wound_context", "compound_only")
        for candidate in range(3):
            for item in range(6):
                raw_text = f"compound {candidate} evidence {item}"
                records.append(
                    {
                        "locus_id": f"locus:c{candidate}:{item}",
                        "candidate_id": f"compound:c{candidate}",
                        "matched_terms": [f"compound {candidate}"],
                        "context_class": contexts[item % len(contexts)],
                        "context_terms": [contexts[item % len(contexts)]],
                        "review_status": "pending_full_text_review",
                        "evidence_status": (
                            "retrieval_candidate_not_scientific_evidence"
                        ),
                        "doc_id": f"doc:{candidate}:{item}",
                        "title": f"Study {candidate}-{item}",
                        "year": "2026",
                        "doi": f"10.example/{candidate}.{item}",
                        "source_filename": f"study-{candidate}-{item}.pdf",
                        "source_sha256": f"{candidate + 1:x}" * 64,
                        "pdf_page": item + 1,
                        "chunk_id": f"chunk:{candidate}:{item}",
                        "chunk_text_sha256": hashlib.sha256(
                            raw_text.encode("utf-8")
                        ).hexdigest(),
                        "snippet": raw_text,
                    }
                )
        loci_payload = "".join(
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
            for value in records
        )
        catalog_path = root / "catalog.json"
        loci_path = root / "loci.jsonl"
        summary_path = root / "summary.json"
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        loci_path.write_text(loci_payload, encoding="utf-8", newline="\n")
        summary_path.write_text(
            json.dumps(
                {
                    "catalog_sha256": _canonical_sha(catalog),
                    "loci_sha256": hashlib.sha256(
                        loci_payload.encode("utf-8")
                    ).hexdigest(),
                    "locus_count": len(records),
                }
            ),
            encoding="utf-8",
        )
        return catalog_path, loci_path, summary_path

    def _prepare(self, root: Path, name: str = "batch") -> Path:
        catalog, loci, summary = self._inputs(root)
        output = root / name
        manifest = prepare_annotation_batch(
            loci_path=loci,
            coverage_summary_path=summary,
            catalog_path=catalog,
            output_dir=output,
            batch_size=12,
            seed="fixed-test-seed",
        )
        self.assertEqual(manifest["batch_size"], 12)
        self.assertEqual(
            manifest["distribution"]["candidate"],
            {"compound:c0": 4, "compound:c1": 4, "compound:c2": 4},
        )
        self.assertEqual(
            manifest["distribution"]["context"],
            {"burn_context": 4, "compound_only": 2, "wound_context": 6},
        )
        validation = validate_annotation_batch(output / "batch_manifest.json")
        self.assertTrue(validation["valid"])
        self.assertTrue(validation["reviewer_orders_distinct"])
        return output

    def _complete_review(
        self,
        path: Path,
        reviewer_id: str,
        *,
        disagreement_locus: str = "",
    ) -> None:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = list(reader.fieldnames or [])
            rows = list(reader)
        for row in rows:
            row["reviewer_id"] = reviewer_id
            row["reviewed_at"] = "2026-07-31"
            row.update(
                {
                    "full_text_checked": "yes",
                    "source_page_verified": "yes",
                    "relevance_label": (
                        "direct_burn"
                        if row["locus_id"] == disagreement_locus
                        else "direct_wound"
                    ),
                    "study_type": "animal",
                    "evidence_direction": "supportive",
                    "supports_c1_source": "yes",
                    "supports_c2_exposure": "uncertain",
                    "supports_c3_burn_wound": "yes",
                    "supports_c4_target_pathway": "uncertain",
                    "supports_c5_safety": "no",
                    "confidence_1_to_5": "4",
                    "notes": "",
                }
            )
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    def test_prepare_is_deterministic_balanced_and_blinded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self._prepare(root, "first")
            second = self._prepare(root, "second")
            self.assertEqual(
                (first / "review_master.jsonl").read_bytes(),
                (second / "review_master.jsonl").read_bytes(),
            )
            master = [
                json.loads(line)
                for line in (first / "review_master.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertTrue(all(value["canonical_name"] for value in master))
            with (first / "reviewer_A.csv").open(
                "r", encoding="utf-8-sig", newline=""
            ) as handle:
                a_order = [row["locus_id"] for row in csv.DictReader(handle)]
            with (first / "reviewer_B.csv").open(
                "r", encoding="utf-8-sig", newline=""
            ) as handle:
                b_order = [row["locus_id"] for row in csv.DictReader(handle)]
            self.assertCountEqual(a_order, b_order)
            self.assertNotEqual(a_order, b_order)

    def test_merge_requires_adjudication_for_every_item(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            batch = self._prepare(root)
            with (batch / "reviewer_A.csv").open(
                "r", encoding="utf-8-sig", newline=""
            ) as handle:
                disagreement_locus = next(csv.DictReader(handle))["locus_id"]
            self._complete_review(batch / "reviewer_A.csv", "reviewer-alice")
            self._complete_review(
                batch / "reviewer_B.csv",
                "reviewer-bob",
                disagreement_locus=disagreement_locus,
            )
            report = merge_annotation_reviews(
                manifest_path=batch / "batch_manifest.json",
                reviewer_a_path=batch / "reviewer_A.csv",
                reviewer_b_path=batch / "reviewer_B.csv",
                output_dir=root / "merged",
            )
            self.assertEqual(report["item_count"], 12)
            self.assertEqual(report["strict_agreement_count"], 11)
            self.assertEqual(report["adjudication_required_count"], 12)
            self.assertEqual(report["scientific_evidence_approved_count"], 0)
            with (root / "merged" / "adjudication_queue.csv").open(
                "r", encoding="utf-8-sig", newline=""
            ) as handle:
                queue = list(csv.DictReader(handle))
            self.assertEqual(len(queue), 12)
            reasons = {row["adjudication_reason"] for row in queue}
            self.assertEqual(
                reasons,
                {"field_disagreement", "dual_agreement_confirmation"},
            )

    def test_merge_rejects_changed_source_field(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            batch = self._prepare(root)
            self._complete_review(batch / "reviewer_A.csv", "reviewer-alice")
            self._complete_review(batch / "reviewer_B.csv", "reviewer-bob")
            path = batch / "reviewer_B.csv"
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                fields = list(reader.fieldnames or [])
                rows = list(reader)
            rows[0]["pdf_page"] = "999"
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaisesRegex(AnnotationError, "fixed source field changed"):
                merge_annotation_reviews(
                    manifest_path=batch / "batch_manifest.json",
                    reviewer_a_path=batch / "reviewer_A.csv",
                    reviewer_b_path=batch / "reviewer_B.csv",
                    output_dir=root / "merged",
                )

    def test_finalize_requires_independent_adjudication_and_releases_approved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            batch = self._prepare(root)
            self._complete_review(batch / "reviewer_A.csv", "reviewer-alice")
            self._complete_review(batch / "reviewer_B.csv", "reviewer-bob")
            merged_dir = root / "merged"
            merge_annotation_reviews(
                manifest_path=batch / "batch_manifest.json",
                reviewer_a_path=batch / "reviewer_A.csv",
                reviewer_b_path=batch / "reviewer_B.csv",
                output_dir=merged_dir,
            )
            adjudication = merged_dir / "adjudication_queue.csv"
            with adjudication.open(
                "r", encoding="utf-8-sig", newline=""
            ) as handle:
                reader = csv.DictReader(handle)
                fields = list(reader.fieldnames or [])
                rows = list(reader)
            for index, row in enumerate(rows):
                for field in ANNOTATION_FIELDS:
                    row[f"final_{field}"] = row[f"a_{field}"]
                row["adjudicator_id"] = "adjudicator-carol"
                row["adjudicated_at"] = "2026-07-31"
                row["adjudication_decision"] = (
                    "reject" if index == 0 else "approve"
                )
                row["adjudication_notes"] = "source checked"
            with adjudication.open(
                "w", encoding="utf-8-sig", newline=""
            ) as handle:
                writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
                writer.writeheader()
                writer.writerows(rows)
            report = finalize_annotation_adjudication(
                batch_manifest_path=batch / "batch_manifest.json",
                agreement_report_path=merged_dir / "agreement_report.json",
                adjudication_path=adjudication,
                output_dir=root / "final",
            )
            self.assertEqual(report["item_count"], 12)
            self.assertEqual(report["approved_scientific_evidence_count"], 11)
            self.assertEqual(report["decision_counts"], {"approve": 11, "reject": 1})
            records = [
                json.loads(line)
                for line in (root / "final" / "final_annotations.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(sum(row["scientific_evidence_approved"] for row in records), 11)
            self.assertTrue(all(row["adjudicator_id"] == "adjudicator-carol" for row in records))

            rows[0]["adjudicator_id"] = "reviewer-alice"
            bad_adjudication = root / "bad_adjudication.csv"
            with bad_adjudication.open(
                "w", encoding="utf-8-sig", newline=""
            ) as handle:
                writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaisesRegex(AnnotationError, "adjudicator is not independent"):
                finalize_annotation_adjudication(
                    batch_manifest_path=batch / "batch_manifest.json",
                    agreement_report_path=merged_dir / "agreement_report.json",
                    adjudication_path=bad_adjudication,
                    output_dir=root / "bad-final",
                )

    def test_annotation_schema_stays_complete(self) -> None:
        self.assertEqual(len(ANNOTATION_FIELDS), 11)

    def test_batch_validation_rejects_file_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            batch = self._prepare(root)
            with (batch / "reviewer_A.csv").open("a", encoding="utf-8") as handle:
                handle.write("tampered\n")
            with self.assertRaisesRegex(AnnotationError, "missing or changed"):
                validate_annotation_batch(batch / "batch_manifest.json")


if __name__ == "__main__":
    unittest.main()
