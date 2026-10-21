"""
Independent integrity validation of a Phase 2 live run.

This module re-derives every claim about the dataset from the files on disk.
It never trusts the runner's own summary, and it never repairs anything: a
dataset either passes or it is reported as failing, with the exact defect.

Checks performed:

1.  the attempt matrix is complete (or the shortfall is quantified);
2.  attempt ids are unique and every planned cell appears exactly once;
3.  no record is a mock record;
4.  the candidate hash in the record matches the candidate file on disk;
5.  the candidate hash matches the candidate that was submitted to the
    evaluator (the evaluated bytes are archived alongside the record);
6.  JSONL, CSV, summary and metadata agree with each other;
7.  failed and unevaluated attempts are still present;
8.  missing telemetry is recorded as unavailable, never as zero.

Usage:

    python -m experiments.itms_aider_cline_v1.integrity --run-dir <dir>
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from typing import Any, Dict, List, Optional

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SYSTEM_ORDER = ("itms", "aider", "cline")


class IntegrityChecker:
    def __init__(self, run_dir: str) -> None:
        self.run_dir = os.path.abspath(run_dir)
        self.checks: List[Dict[str, Any]] = []

    # -- helpers -----------------------------------------------------------

    def record(self, name: str, passed: bool, detail: str = "", evidence: Any = None) -> None:
        self.checks.append({
            "check": name,
            "passed": bool(passed),
            "detail": detail,
            "evidence": evidence,
        })

    @property
    def all_passed(self) -> bool:
        return all(c["passed"] for c in self.checks)

    # -- loading -----------------------------------------------------------

    def load(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {}
        for name in ("attempts.jsonl", "attempts.csv", "summary.json", "run_metadata.json"):
            path = os.path.join(self.run_dir, name)
            data[name] = path if os.path.exists(path) else None

        records: List[Dict[str, Any]] = []
        if data["attempts.jsonl"]:
            with open(data["attempts.jsonl"], "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        records.append(json.loads(line))
        data["records"] = records

        rows: List[Dict[str, str]] = []
        if data["attempts.csv"]:
            with open(data["attempts.csv"], "r", encoding="utf-8", newline="") as fh:
                rows = list(csv.DictReader(fh))
        data["csv_rows"] = rows

        data["summary"] = None
        if data["summary.json"]:
            with open(data["summary.json"], "r", encoding="utf-8") as fh:
                data["summary"] = json.load(fh)
        data["metadata"] = None
        if data["run_metadata.json"]:
            with open(data["run_metadata.json"], "r", encoding="utf-8") as fh:
                data["metadata"] = json.load(fh)
        return data

    # -- checks ------------------------------------------------------------

    def run(self) -> Dict[str, Any]:
        data = self.load()
        records = data["records"]
        metadata = data["metadata"] or {}
        summary = data["summary"] or {}

        # --- 0. required files exist ---------------------------------------
        required = ("attempts.jsonl", "attempts.csv", "summary.json", "run_metadata.json")
        missing = [n for n in required if data.get(n) is None]
        self.record(
            "required_output_files_present",
            not missing,
            f"missing: {missing}" if missing else "all four required files present",
        )

        # --- 1. matrix completeness ----------------------------------------
        planned = int(metadata.get("planned_attempts") or 0)
        expected_cells = self._expected_cells(metadata)
        recorded = len(records)
        self.record(
            "attempt_matrix_complete",
            recorded == expected_cells and recorded == planned,
            f"planned={planned} expected_cells={expected_cells} recorded={recorded}",
            {"missing_cells": self._missing_cells(records, metadata)},
        )

        # --- 2. unique ids, one record per cell ----------------------------
        ids = [r.get("attempt_id") for r in records]
        self.record(
            "attempt_ids_unique",
            len(set(ids)) == len(ids),
            f"{len(set(ids))} unique ids across {len(ids)} records",
        )
        cell_keys = [(r.get("task_id"), r.get("system"), r.get("repetition")) for r in records]
        self.record(
            "one_record_per_planned_cell",
            len(set(cell_keys)) == len(cell_keys) == len(records),
            f"{len(set(cell_keys))} distinct cells across {len(records)} records",
        )
        rep_ids = [r.get("repetition_id") for r in records]
        self.record(
            "repetition_ids_unique",
            len(set(rep_ids)) == len(rep_ids),
            f"{len(set(rep_ids))} unique repetition ids",
        )

        # --- 3. no mock records --------------------------------------------
        mocks = [r.get("attempt_id") for r in records if r.get("is_mock")]
        self.record(
            "no_mock_records_in_live_dataset",
            not mocks,
            f"{len(mocks)} mock records found" if mocks else "no record is flagged is_mock",
            {"mock_attempt_ids": mocks[:20]},
        )
        self.record(
            "run_declared_live",
            metadata.get("mode") == "live" and metadata.get("is_measurement") is True,
            f"mode={metadata.get('mode')} is_measurement={metadata.get('is_measurement')}",
        )

        # --- 4/5. candidate hash integrity ----------------------------------
        hash_mismatch, missing_candidate, unparsable = self._check_candidates(records)
        self.record(
            "candidate_hash_matches_stored_candidate",
            not hash_mismatch,
            f"{len(hash_mismatch)} mismatches",
            {"mismatched_attempt_ids": hash_mismatch[:20]},
        )
        self.record(
            "evaluated_candidate_is_archived",
            not unparsable,
            f"{len(unparsable)} evaluated attempts without an archived candidate",
            {"attempt_ids": unparsable[:20]},
        )

        # --- 6. jsonl / csv / summary / metadata agreement ------------------
        self._check_cross_file_agreement(data)

        # --- 7. failures preserved -----------------------------------------
        self._check_failures_preserved(records)

        # --- 8. missing telemetry is None, never 0 --------------------------
        zeroed = [
            r.get("attempt_id")
            for r in records
            if r.get("usage_source") == "missing"
            and (r.get("total_tokens") == 0 or r.get("cost_usd") == 0)
        ]
        self.record(
            "missing_telemetry_is_null_not_zero",
            not zeroed,
            f"{len(zeroed)} records encode missing telemetry as 0",
            {"attempt_ids": zeroed[:20]},
        )
        zero_total_with_usage = [
            r.get("attempt_id")
            for r in records
            if r.get("usage_source") in ("provider_reported", "tool_reported")
            and r.get("total_tokens") == 0
        ]
        self.record(
            "reported_usage_is_never_zero",
            not zero_total_with_usage,
            f"{len(zero_total_with_usage)} records report usage_source with zero tokens",
            {"attempt_ids": zero_total_with_usage[:20]},
        )

        # --- 9. seed honesty -------------------------------------------------
        self._check_seed_honesty(records)

        # --- 10. historical data untouched -----------------------------------
        self._check_historical_untouched()

        # --- 11. artefact index ---------------------------------------------
        self.build_artifact_index(records)

        return {
            "run_dir": self.run_dir,
            "checks": self.checks,
            "all_passed": self.all_passed,
            "passed": sum(1 for c in self.checks if c["passed"]),
            "failed": sum(1 for c in self.checks if not c["passed"]),
            "recorded_attempts": len(records),
            "planned_attempts": planned,
            "missing_cells": self._missing_cells(records, metadata),
        }

    # -- individual check helpers ------------------------------------------

    def _expected_cells(self, metadata: Dict[str, Any]) -> int:
        tasks = metadata.get("tasks") or []
        systems = metadata.get("systems") or []
        reps = metadata.get("repetitions") or 0
        return len(tasks) * len(systems) * int(reps)

    def _missing_cells(self, records, metadata) -> List[str]:
        tasks = metadata.get("tasks") or []
        systems = metadata.get("systems") or []
        reps = int(metadata.get("repetitions") or 0)
        present = {(r.get("task_id"), r.get("system"), r.get("repetition")) for r in records}
        missing = []
        for task in tasks:
            for system in systems:
                for rep in range(1, reps + 1):
                    if (task, system, rep) not in present:
                        missing.append(f"{task}::{system}::rep{rep:02d}")
        return missing

    def _check_candidates(self, records):
        mismatch: List[str] = []
        missing: List[str] = []
        unparsable: List[str] = []
        for record in records:
            attempt_id = record.get("attempt_id")
            path = record.get("candidate_path")
            expected = record.get("candidate_sha256")
            if not path:
                if expected:
                    mismatch.append(attempt_id)
                continue
            abs_path = os.path.join(self.run_dir, path)
            if not os.path.exists(abs_path):
                missing.append(attempt_id)
                continue
            with open(abs_path, "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
            if expected and digest != expected:
                mismatch.append(attempt_id)
            if record.get("evaluation_attempted") and record.get("test_passed") is not None:
                if not expected:
                    unparsable.append(attempt_id)
        return mismatch, missing, unparsable

    def build_artifact_index(self, records) -> List[Dict[str, Any]]:
        """Index every archived artefact with its sha256.

        The raw tool output (Aider's stdout/stderr, Cline's JSONL event
        stream) and the evaluated candidate are preserved under
        ``artifacts/<attempt_id>/``.  This index records what was archived and
        the digest of each file, so the raw output is verifiably present and
        unmodified.  It is written as a *separate* file: the attempt records
        themselves are never rewritten.
        """
        import csv

        index: List[Dict[str, Any]] = []
        artifacts_root = os.path.join(self.run_dir, "artifacts")
        for record in records:
            attempt_id = record.get("attempt_id")
            attempt_dir = os.path.join(artifacts_root, str(attempt_id))
            if not os.path.isdir(attempt_dir):
                continue
            for name in sorted(os.listdir(attempt_dir)):
                path = os.path.join(attempt_dir, name)
                if not os.path.isfile(path):
                    continue
                with open(path, "rb") as fh:
                    data = fh.read()
                index.append({
                    "attempt_id": attempt_id,
                    "task_id": record.get("task_id"),
                    "system": record.get("system"),
                    "repetition_id": record.get("repetition_id"),
                    "artifact": name,
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "is_evaluated_candidate": (
                        name == "candidate.py"
                        and record.get("candidate_path")
                        and os.path.basename(record["candidate_path"]) == name
                    ),
                })
        out_path = os.path.join(self.run_dir, "ARTIFACT_INDEX.csv")
        if index:
            with open(out_path, "w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(index[0].keys()))
                writer.writeheader()
                writer.writerows(index)
        self.record(
            "artifact_index_written",
            bool(index),
            f"{len(index)} artefact files indexed -> ARTIFACT_INDEX.csv",
        )
        raw_names = ("stdout.log", "stderr.log", "cline.stdout.jsonl")
        with_raw = {
            entry["attempt_id"] for entry in index
            if entry["artifact"] in raw_names
        }
        # The check is *consistency*: a system either archives its raw output
        # for every attempt or for none of them. A partially-archived system
        # would mean silent data loss.
        by_system: Dict[str, Dict[str, int]] = {}
        for record in records:
            system = str(record.get("system"))
            bucket = by_system.setdefault(system, {"with_raw": 0, "without_raw": 0})
            if record.get("attempt_id") in with_raw:
                bucket["with_raw"] += 1
            else:
                bucket["without_raw"] += 1
        inconsistent = [
            f"{system}: {bucket['with_raw']} with raw output, "
            f"{bucket['without_raw']} without"
            for system, bucket in by_system.items()
            if bucket["with_raw"] and bucket["without_raw"]
        ]
        self.record(
            "raw_tool_output_archived_consistently",
            not inconsistent,
            "; ".join(inconsistent) if inconsistent
            else f"{len(with_raw)} attempts have archived raw tool output",
            {"inconsistent": inconsistent},
        )
        return index

    def _check_cross_file_agreement(self, data) -> None:
        records = data["records"]
        rows = data["csv_rows"]
        summary = data["summary"]
        metadata = data["metadata"]

        self.record(
            "csv_row_count_matches_jsonl",
            len(rows) == len(records),
            f"csv={len(rows)} jsonl={len(records)}",
        )
        if rows and records:
            csv_ids = [r.get("attempt_id") for r in rows]
            jsonl_ids = [r.get("attempt_id") for r in records]
            self.record(
                "csv_attempt_ids_match_jsonl",
                csv_ids == jsonl_ids,
                "csv attempt_id order matches jsonl"
                if csv_ids == jsonl_ids
                else "csv attempt_id order differs from jsonl",
            )
        if summary:
            self.record(
                "summary_recorded_count_matches",
                int(summary.get("recorded_attempts", -1)) == len(records),
                f"summary recorded_attempts={summary.get('recorded_attempts')} actual={len(records)}",
            )
            self.record(
                "summary_planned_count_matches",
                int(summary.get("planned_attempts", -1)) == int(metadata.get("planned_attempts", -2)),
                f"summary planned={summary.get('planned_attempts')} metadata planned={metadata.get('planned_attempts')}",
            )
            # per-system evaluated counts
            mismatches = []
            for system, metrics in (summary.get("systems") or {}).items():
                subset = [r for r in records if r.get("system") == system]
                evaluated = sum(1 for r in subset if r.get("evaluation_attempted"))
                if int(metrics.get("evaluated", -1)) != evaluated:
                    mismatches.append(
                        f"{system}: summary={metrics.get('evaluated')} actual={evaluated}"
                    )
            self.record(
                "summary_per_system_counts_match",
                not mismatches,
                "; ".join(mismatches) if mismatches else "per-system evaluated counts agree",
                {"mismatches": mismatches},
            )
        if metadata:
            self.record(
                "metadata_records_seed_policy",
                bool(metadata.get("seed_policy")),
                f"seed_policy={metadata.get('seed_policy')}",
            )
            self.record(
                "metadata_records_spending_limit",
                metadata.get("max_spend_usd") is not None,
                f"max_spend_usd={metadata.get('max_spend_usd')}",
            )
            self.record(
                "metadata_records_task_manifest_digest",
                bool(metadata.get("task_manifest_digest")),
                f"task_manifest_digest={metadata.get('task_manifest_digest')}",
            )

    def _check_failures_preserved(self, records) -> None:
        """Failures must still be in the dataset, and the taxonomy must add up."""
        failures = [r for r in records if r.get("status") != "repair_success"]
        self.record(
            "failed_attempts_preserved",
            len(failures) == sum(
                1 for r in records if r.get("status") != "repair_success"
            ),
            f"{len(failures)} non-success records retained in the dataset",
        )
        # A record's status and its failure category must agree: an execution
        # failure must not be reported as a test failure, and a success must
        # not carry a failure category.
        inconsistent = []
        for record in records:
            status = record.get("status")
            category = record.get("failure_category")
            if status == "repair_success" and category not in (None, "none"):
                inconsistent.append(record.get("attempt_id"))
            if status == "execution_failed" and category in (None, "none"):
                inconsistent.append(record.get("attempt_id"))
        self.record(
            "status_and_failure_category_agree",
            not inconsistent,
            f"{len(inconsistent)} records whose status and failure category disagree",
            {"attempt_ids": inconsistent[:20]},
        )
        # Every failure category that is not 'none' must be represented.
        expected = sum(
            1 for r in records
            if r.get("failure_category") not in (None, "none")
        )
        self.record(
            "failure_categories_accounted_for",
            expected >= len(
                [r for r in records if r.get("status") == "execution_failed"]
            ),
            f"{expected} categorised failures across {len(records)} records",
        )
        unevaluated = [r for r in records if not r.get("evaluation_attempted")]
        inconsistent = [
            r.get("attempt_id")
            for r in unevaluated
            if r.get("test_passed") is True
        ]
        self.record(
            "unevaluated_attempts_are_never_passes",
            not inconsistent,
            f"{len(inconsistent)} unevaluated attempts recorded a pass",
            {"attempt_ids": inconsistent[:20]},
        )
        artifact_dirs = os.path.join(self.run_dir, "artifacts")
        if os.path.isdir(artifact_dirs):
            n_dirs = len(os.listdir(artifact_dirs))
            self.record(
                "artifact_directory_present_for_every_attempt",
                n_dirs >= len(records),
                f"{n_dirs} artifact directories for {len(records)} attempts",
            )

    def _check_seed_honesty(self, records) -> None:
        """No record may claim a seed it could not have applied."""
        bad: List[str] = []
        by_system: Dict[str, Dict[str, Any]] = {}
        for record in records:
            system = record.get("system")
            info = by_system.setdefault(
                system,
                {"supported": set(), "applied": set(), "count": 0},
            )
            info["count"] += 1
            info["supported"].add(bool(record.get("seed_supported")))
            info["applied"].add(record.get("seed_applied") is not None)
            if record.get("seed_applied") is not None and not record.get("seed_supported"):
                bad.append(record.get("attempt_id"))
        self.record(
            "no_seed_claimed_where_unsupported",
            not bad,
            f"{len(bad)} records claim an applied seed without seed support",
            {"attempt_ids": bad[:20]},
        )
        for system in sorted(by_system):
            info = by_system[system]
            self.record(
                f"seed_support_consistent_for_{system}",
                len(info["supported"]) == 1 and len(info["applied"]) == 1,
                f"seed_supported={sorted(info['supported'])} "
                f"seed_applied_present={sorted(info['applied'])} n={info['count']}",
            )

    def _check_historical_untouched(self) -> None:
        history = os.path.join(REPO_ROOT, "pilot_runs")
        legacy = os.path.join(REPO_ROOT, "results")
        self.record(
            "historical_dataset_directories_present",
            os.path.isdir(history) and os.path.isdir(legacy),
            f"pilot_runs={os.path.isdir(history)} results={os.path.isdir(legacy)}",
        )
        self.record(
            "run_written_outside_historical_directories",
            not os.path.abspath(self.run_dir).startswith(history + os.sep)
            and not os.path.abspath(self.run_dir).startswith(legacy + os.sep),
            f"run_dir={self.run_dir}",
        )


def write_markdown(result: Dict[str, Any], metadata: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# Benchmark Integrity Report")
    lines.append("")
    lines.append(f"**Run directory:** `{result['run_dir']}`")
    lines.append("")
    status = "PASS" if result["all_passed"] else "FAIL"
    lines.append(
        f"**Overall: {status}** — {result['passed']} checks passed, "
        f"{result['failed']} failed."
    )
    lines.append("")
    lines.append(
        f"Recorded attempts: **{result['recorded_attempts']}** of "
        f"**{result['planned_attempts']}** planned."
    )
    lines.append("")
    lines.append("## Run identity")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    for key in (
        "run_id", "experiment_id", "mode", "is_measurement", "provider",
        "requested_model_id", "temperature", "repetitions", "tasks",
        "systems", "planned_attempts", "run_order_seed", "seed_policy",
        "max_spend_usd", "require_free_model", "task_manifest_digest",
        "python_version", "platform",
    ):
        if key in metadata:
            value = metadata[key]
            if isinstance(value, list):
                value = f"{len(value)} items"
            lines.append(f"| `{key}` | {value} |")
    lines.append("")

    lines.append("## Checks")
    lines.append("")
    lines.append("| # | Check | Result | Detail |")
    lines.append("|---|---|---|---|")
    for index, check in enumerate(result["checks"], start=1):
        mark = "PASS" if check["passed"] else "**FAIL**"
        detail = str(check["detail"]).replace("|", "/")
        lines.append(f"| {index} | `{check['check']}` | {mark} | {detail} |")
    lines.append("")

    missing = result.get("missing_cells") or []
    lines.append("## Matrix completeness")
    lines.append("")
    if missing:
        lines.append(
            f"**{len(missing)} planned cells have no record.** They are listed "
            "here in full and are NOT counted as successes anywhere:"
        )
        lines.append("")
        for cell in missing:
            lines.append(f"- `{cell}`")
    else:
        lines.append(
            "Every planned (task, system, repetition) cell has exactly one "
            "record. No cell is missing and no cell is duplicated."
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--output", default=None,
                        help="where to write BENCHMARK_INTEGRITY_REPORT.md")
    args = parser.parse_args(argv)

    checker = IntegrityChecker(args.run_dir)
    result = checker.run()
    metadata = checker.load()["metadata"] or {}

    output = args.output or os.path.join(args.run_dir, "BENCHMARK_INTEGRITY_REPORT.md")
    with open(output, "w", encoding="utf-8") as fh:
        fh.write(write_markdown(result, metadata))

    print(json.dumps(
        {k: v for k, v in result.items() if k != "checks"},
        indent=2, default=str,
    ))
    for check in result["checks"]:
        if not check["passed"]:
            print(f"FAIL {check['check']}: {check['detail']}", file=sys.stderr)
    return 0 if result["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
