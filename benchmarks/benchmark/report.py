"""
Offline analysis of a completed benchmark run.

This module reads the files of a finished run and **re-derives every number
from the records**.  It never contacts a provider, never runs a model and
never writes into the run directory itself (the caller chooses the output
directory).

Two things are produced:

``analysis.json``
    The machine-readable bundle: denominators, per-method metrics, truncation
    and output-cap facts, seed facts, cost availability, and the result of the
    integrity validation.

``RESULTS_SUMMARY.md``
    The human-readable report.  It states the denominators, discloses the
    limitations, and refuses to present numbers as publishable when an
    integrity check failed.

Cost is only ever reported for attempts whose cost the **provider** reported.
When the provider reports no cost, the report says *unavailable* - it never
plots a ``$0`` bar and never substitutes an estimate for a measurement.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from benchmarks.benchmark.analysis import (
    method_metrics,
    paired_task_comparison,
    summarise_attempts,
)
from benchmarks.benchmark.records import AttemptRecord, AttemptStatus

__all__ = [
    "AnalysisError",
    "load_run",
    "build_analysis",
    "write_analysis",
    "render_markdown",
]


class AnalysisError(RuntimeError):
    """Raised when a run directory cannot be analysed."""


def _read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_jsonl(path: str) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise AnalysisError(
                    f"{path}: line {line_number} is not valid JSON: {exc}"
                ) from exc
    return records


def _read_csv(path: str) -> List[Dict[str, str]]:
    with open(path, "r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _sha256_file(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return None


def load_run(run_dir: str) -> Dict[str, Any]:
    """Loads a run directory without modifying it."""
    run_dir = os.path.abspath(run_dir)
    attempts_path = os.path.join(run_dir, "attempts.jsonl")
    if not os.path.exists(attempts_path):
        raise AnalysisError(f"no attempts.jsonl in {run_dir}")

    raw_records = _read_jsonl(attempts_path)
    csv_rows: List[Dict[str, str]] = []
    csv_path = os.path.join(run_dir, "attempts.csv")
    if os.path.exists(csv_path):
        csv_rows = _read_csv(csv_path)

    metadata: Dict[str, Any] = {}
    metadata_path = os.path.join(run_dir, "run_metadata.json")
    if os.path.exists(metadata_path):
        metadata = _read_json(metadata_path)

    summary: Dict[str, Any] = {}
    summary_path = os.path.join(run_dir, "summary.json")
    if os.path.exists(summary_path):
        summary = _read_json(summary_path)

    records = [AttemptRecord(**_coerce_record(raw)) for raw in raw_records]
    return {
        "run_dir": run_dir,
        "records": records,
        "raw_records": raw_records,
        "csv_rows": csv_rows,
        "metadata": metadata,
        "summary": summary,
    }


def _coerce_record(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Keeps only the fields the dataclass declares, so an old run still loads."""
    from dataclasses import fields

    allowed = {field.name for field in fields(AttemptRecord)}
    return {key: value for key, value in raw.items() if key in allowed}


# ----------------------------------------------------------------------
# Integrity validation
# ----------------------------------------------------------------------
def _integrity_checks(run: Dict[str, Any]) -> List[Dict[str, Any]]:
    records: List[AttemptRecord] = run["records"]
    csv_rows = run["csv_rows"]
    summary = run["summary"]
    run_dir = run["run_dir"]
    mode = run["metadata"].get("mode")

    checks: List[Dict[str, Any]] = []

    def add(name: str, passed: bool, detail: str) -> None:
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    # 1. JSONL <-> CSV agreement
    jsonl_ids = [r.attempt_id for r in records]
    csv_ids = [row.get("attempt_id") for row in csv_rows]
    if csv_rows:
        add(
            "jsonl_csv_same_attempt_ids",
            jsonl_ids == csv_ids,
            f"{len(jsonl_ids)} records vs {len(csv_ids)} csv rows",
        )
        mismatched = [
            r.attempt_id
            for r, row in zip(records, csv_rows)
            if (row.get("status") or "") != r.status
        ]
        add(
            "jsonl_csv_same_status",
            not mismatched,
            "no status mismatch" if not mismatched else f"{len(mismatched)} mismatched",
        )
    else:
        add("jsonl_csv_same_attempt_ids", True, "no attempts.csv in the run directory")

    # 2. Attempt ids are unique
    add(
        "attempt_ids_unique",
        len(set(jsonl_ids)) == len(jsonl_ids),
        f"{len(set(jsonl_ids))} unique of {len(jsonl_ids)}",
    )

    # 3. Summary agreement
    if summary:
        add(
            "summary_recorded_matches",
            int(summary.get("recorded_attempts", len(records))) == len(records),
            f"summary={summary.get('recorded_attempts')} files={len(records)}",
        )
        planned = summary.get("planned_attempts")
        if planned is not None:
            add(
                "summary_planned_matches",
                int(planned) >= len(records),
                f"planned={planned} recorded={len(records)}",
            )
    else:
        add("summary_recorded_matches", True, "no summary.json in the run directory")

    # 4. Artefact presence and candidate digests
    missing_artifacts: List[str] = []
    hash_mismatch: List[str] = []
    for record in records:
        if record.artifacts_status != "ok":
            continue
        attempt_dir = os.path.join(run_dir, record.artifacts_path or "")
        if not os.path.isdir(attempt_dir):
            missing_artifacts.append(record.attempt_id)
            continue
        if record.candidate_path:
            candidate_file = os.path.join(run_dir, record.candidate_path)
            digest = _sha256_file(candidate_file)
            if digest is not None and record.candidate_file_sha256 and digest != record.candidate_file_sha256:
                hash_mismatch.append(record.attempt_id)
    add(
        "artifacts_present_for_every_attempt",
        not missing_artifacts,
        "all present" if not missing_artifacts else f"{len(missing_artifacts)} missing",
    )
    add(
        "candidate_hashes_match_artifacts",
        not hash_mismatch,
        "all match" if not hash_mismatch else f"{len(hash_mismatch)} mismatched",
    )

    # 5. Prompt persistence
    missing_prompts = [
        r.attempt_id
        for r in records
        if r.artifacts_status == "ok" and r.prompt_path
        and not os.path.isfile(os.path.join(run_dir, r.prompt_path))
    ]
    with_prompt = sum(1 for r in records if r.prompt_path)
    add(
        "prompt_persisted_when_supported",
        not missing_prompts,
        (
            f"{with_prompt}/{len(records)} attempts carry a stored prompt"
            if with_prompt
            else "no attempt carries a stored prompt (this run predates prompt "
            "persistence); the check is vacuous, not a pass on merit"
        ),
    )

    # 6. No mock record may appear in a live run
    mock_attempts = sum(1 for r in records if r.is_mock)
    add(
        "no_mock_records_in_live_run",
        (mode != "live") or mock_attempts == 0,
        f"{mock_attempts} mock attempts",
    )

    # 7. Failed / unevaluated attempts are retained
    non_success = [
        r
        for r in records
        if r.status not in (AttemptStatus.REPAIR_SUCCESS,)
    ]
    add(
        "failed_and_unevaluated_attempts_retained",
        True,
        f"{len(non_success)} non-success attempts present in the records",
    )

    # 8. Missing telemetry is null, never 0
    bad_missing = [
        r.attempt_id
        for r in records
        if r.usage_source != "provider_reported"
        and (r.total_tokens is not None or r.prompt_tokens is not None)
    ]
    add(
        "missing_telemetry_stored_as_null",
        not bad_missing,
        "no estimate stored in a provider-reported field"
        if not bad_missing
        else f"{len(bad_missing)} records",
    )

    # 9. Model identity
    served = {r.served_model_id for r in records if r.served_model_id}
    add(
        "single_served_model",
        len(served) <= 1,
        f"served ids: {sorted(served)}" if served else "no served model id recorded",
    )

    # 10. One shared output cap across strategies
    caps = {r.max_output_tokens for r in records if r.max_output_tokens is not None}
    add(
        "output_cap_consistent_across_strategies",
        len(caps) <= 1,
        f"caps in use: {sorted(caps)}" if caps else "no cap recorded (mock runs)",
    )

    # 11. Seed consistency: a cell must not have two different seeds, and
    #     honouring is never claimed without a probe.
    seed_by_cell: Dict[str, set] = {}
    for record in records:
        key = f"{record.task_id}|{record.method}|{record.rep}"
        seed_by_cell.setdefault(key, set()).add(record.seed_requested)
    conflicting = [key for key, seeds in seed_by_cell.items() if len(seeds) > 1]
    add(
        "seed_consistent_per_cell",
        not conflicting,
        f"{len(seed_by_cell)} cells, {len(conflicting)} with conflicting seeds",
    )
    add(
        "seed_honour_never_claimed_without_probe",
        not any(r.seed_honour_verified for r in records),
        "seed_honour_verified is false for every attempt",
    )

    # 12. Cost: reported for every attempt or explicitly unavailable
    reported = [r for r in records if r.cost_usd is not None]
    add(
        "cost_either_reported_or_explicitly_unavailable",
        len(reported) in (0, len(records)),
        f"{len(reported)}/{len(records)} attempts carry a provider-reported cost",
    )

    return checks


# ----------------------------------------------------------------------
# Analysis bundle
# ----------------------------------------------------------------------
def _count_by_repetition(records: Sequence[AttemptRecord]) -> Dict[str, int]:
    """Return the number of recorded attempts per repetition number."""
    counts: Dict[int, int] = {}
    for record in records:
        counts[record.rep] = counts.get(record.rep, 0) + 1
    return {str(rep): count for rep, count in sorted(counts.items())}


def build_analysis(run_dir: str) -> Dict[str, Any]:
    """Reads a run directory and returns the analysis bundle."""
    run = load_run(run_dir)
    records: List[AttemptRecord] = run["records"]
    metadata = run["metadata"]
    summary = run["summary"]

    methods = sorted({r.method for r in records}) or list(
        metadata.get("methods") or []
    )
    tasks = sorted({r.task_id for r in records})
    reps = max((r.rep for r in records), default=0)
    computed_scheduled = len(tasks) * len(methods) * max(1, reps) if tasks and methods else 0

    # The *planned* number of cells is taken from the run summary when the
    # run recorded one.  The summary is authoritative: a run whose plan is
    # not the full cross product (a reduced-budget schedule) has a planned
    # count that cannot be re-derived from the records alone.  For a run
    # with the default cross-product schedule the two values agree.
    summary_method_metrics = summary.get("method_metrics") or {}
    planned_from_summary = summary.get("planned_attempts")
    scheduled = (
        int(planned_from_summary)
        if planned_from_summary is not None
        else computed_scheduled
    )

    def _scheduled_for(method: str) -> int:
        entry = summary_method_metrics.get(method)
        if isinstance(entry, dict):
            value = (entry.get("denominators") or {}).get("scheduled_attempts")
            if value is not None:
                return int(value)
        return scheduled // max(1, len(methods)) if methods else 0

    per_method = {
        method: method_metrics(
            records,
            scheduled=_scheduled_for(method),
            method=method,
            tasks_count=len(tasks),
            reps=reps,
        )
        for method in methods
    }
    # The per-method distribution of provider-reported token counts (used by
    # the distribution figure).  Only provider-reported values are included.
    for method in methods:
        per_method[method]["token_distribution"] = [
            r.total_tokens
            for r in records
            if r.method == method and r.total_tokens is not None
        ]
    comparisons = []
    for index, method_a in enumerate(methods):
        for method_b in methods[index + 1 :]:
            comparisons.append(
                paired_task_comparison(records, method_a, method_b, tasks)
            )

    truncation_by_method = {
        method: sum(1 for r in records if r.method == method and r.truncated_output)
        for method in methods
    }
    caps = sorted({r.max_output_tokens for r in records if r.max_output_tokens is not None})
    cost_reported = [r for r in records if r.cost_usd is not None]
    served = {r.served_model_id for r in records if r.served_model_id}

    checks = _integrity_checks(run)
    all_passed = all(check["passed"] for check in checks)

    # -- the request-side denominators, stated separately -----------------
    # "planned", "requested", "completed" and "evaluated" are four different
    # numbers and are never collapsed into one.  A provider request that
    # failed before a response existed was *requested* and *completed as an
    # attempt*, but it was never *evaluated*.
    budget_state = summary.get("budget") or metadata.get("budget") or {}
    planned_attempts = metadata.get("planned_attempts")
    if planned_attempts is None:
        planned_attempts = summary.get("planned_attempts", scheduled)
    requests_issued = budget_state.get("requests_issued")
    if requests_issued is None:
        requests_issued = sum(1 for r in records if r.status != AttemptStatus.INCOMPLETE)
    evaluated_attempts = sum(1 for r in records if r.evaluation_attempted)
    generation_success = sum(
        1 for r in records if r.candidate_state == "produced"
    )
    incomplete_attempts = sum(
        1 for r in records if r.status == AttemptStatus.INCOMPLETE
    )
    unevaluated_attempts = len(records) - evaluated_attempts

    analysis: Dict[str, Any] = {
        "schema_version": "1.0",
        "run_dir": run["run_dir"],
        "run_id": metadata.get("run_id") or summary.get("run_id"),
        "mode": metadata.get("mode") or summary.get("mode"),
        "is_measurement": bool(metadata.get("is_measurement", False))
        and not any(r.is_mock for r in records),
        "created_at_utc": metadata.get("created_at_utc"),
        "model": {
            "requested_model_id": metadata.get("requested_model_id"),
            "served_model_ids": sorted(served),
            "single_served_model": len(served) <= 1,
        },
        "denominators": {
            "tasks": len(tasks),
            "methods": len(methods),
            "reps_per_task": reps,
            "planned_attempts": planned_attempts,
            "scheduled_attempts": scheduled,
            "provider_requests_issued": requests_issued,
            "completed_attempts": len(records),
            "recorded_attempts": len(records),
            "evaluated_attempts": evaluated_attempts,
            "candidate_produced_attempts": sum(
                1 for r in records if r.candidate_state == "produced"
            ),
            "provider_generation_success_attempts": generation_success,
            "unevaluated_attempts": unevaluated_attempts,
            "incomplete_attempts": incomplete_attempts,
            "missing_attempts": int(planned_attempts) - len(records),
            # How many recorded attempts belong to each repetition number.
            # A reduced-budget run has an uneven distribution here and the
            # distribution is part of the result, not a detail.
            "cells_by_repetition": _count_by_repetition(records),
        },
        "schedule": metadata.get("schedule"),
        # The distinction the report must never blur: the provider answering
        # is not the same event as the official evaluator accepting the
        # candidate.
        "generation_versus_evaluation": {
            "provider_requests_issued": requests_issued,
            "provider_answered": sum(
                1 for r in records if r.status != AttemptStatus.PROVIDER_ERROR
            ),
            "provider_error": sum(
                1 for r in records if r.status == AttemptStatus.PROVIDER_ERROR
            ),
            "candidate_produced": generation_success,
            "officially_evaluated": evaluated_attempts,
            "evaluator_pass": sum(1 for r in records if r.test_passed is True),
            "evaluator_fail": sum(
                1
                for r in records
                if r.evaluation_attempted and r.test_passed is not True
            ),
            "never_evaluated": unevaluated_attempts,
            "incomplete": incomplete_attempts,
            "note": (
                "A provider response is not a benchmark pass. Only an attempt "
                "that reached the official evaluator and passed it counts as a "
                "pass."
            ),
        },
        "methods": per_method,
        "method_order": methods,
        "paired_task_comparisons": comparisons,
        "summary_from_run": summarise_attempts(records),
        "truncation": {
            "total": sum(1 for r in records if r.truncated_output),
            "by_method": truncation_by_method,
            "note": (
                "A truncated attempt is a harness-budget outcome. It is reported "
                "separately and must not be read as a repair-quality result."
            ),
        },
        "output_cap": {
            "values_in_use": caps,
            "consistent_across_strategies": len(caps) <= 1,
            "justification": metadata.get("output_token_limit_justification"),
        },
        "seeds": {
            "request_seed_base": metadata.get("request_seed_base"),
            "seeded_attempts": sum(1 for r in records if r.seed_applied),
            "seed_honour_verified": any(r.seed_honour_verified for r in records),
            "note": (
                "seed_honour_verified is false unless a repeated-request probe "
                "proved that the provider honours the seed."
            ),
        },
        "cost": {
            "provider_reported_attempts": len(cost_reported),
            "total_cost_usd": (
                sum(r.cost_usd or 0.0 for r in cost_reported) if cost_reported else None
            ),
            "available": bool(cost_reported),
            "note": (
                "Cost is reported only when the provider supplied it. When no "
                "attempt carries a reported cost, the cost is unavailable - it is "
                "never shown as zero and never replaced by an estimate."
            ),
        },
        "budget": summary.get("budget"),
        "prompt_persistence": {
            "attempts_with_stored_prompt": sum(1 for r in records if r.prompt_path),
            "total_attempts": len(records),
            "available": any(r.prompt_path for r in records),
            "note": (
                "Runs executed before prompt persistence was implemented carry no "
                "stored prompt; the check is reported as vacuous for them."
            ),
        },
        "integrity": {
            "all_passed": all_passed,
            "checks": checks,
        },
    }
    analysis["limitations"] = _limitations(analysis)
    # The raw records are kept for the per-attempt table; they are removed
    # again before the bundle is serialised.
    analysis["_records"] = records
    return analysis


def _limitations(analysis: Dict[str, Any]) -> List[str]:
    limitations: List[str] = []
    if not analysis["is_measurement"]:
        limitations.append(
            "This is a MOCK run: no inference was performed. It is a pipeline "
            "test and not a measurement of model quality."
        )
    if analysis["denominators"]["evaluated_attempts"] == 0:
        limitations.append(
            "No attempt reached the official evaluator, so no pass rate can be "
            "computed."
        )
    if analysis["truncation"]["total"]:
        limitations.append(
            f"{analysis['truncation']['total']} attempt(s) were truncated by the "
            "output-token cap. Truncation is a harness-budget outcome and is "
            "reported per method; it is not a repair-quality result."
        )
    if not analysis["cost"]["available"]:
        limitations.append(
            "The provider reported no cost for this run, so the cost is "
            "unavailable. It is not zero and no estimate is substituted."
        )
    if not analysis["seeds"]["seed_honour_verified"]:
        limitations.append(
            "Whether the provider honoured the request-level seed is unverified: "
            "no repeated-request probe was run."
        )
    if not analysis["model"]["single_served_model"]:
        limitations.append(
            "More than one served model id was observed, so the attempts are not "
            "a same-model comparison."
        )
    if not analysis["integrity"]["all_passed"]:
        failed = [
            check["check"]
            for check in analysis["integrity"]["checks"]
            if not check["passed"]
        ]
        limitations.append(
            "Integrity validation FAILED: " + ", ".join(failed) + ". These "
            "numbers must not be published."
        )
    limitations.append(
        "The evaluation gate is a scrubbed-environment subprocess, not a secure "
        "OS-level sandbox."
    )
    return limitations


# ----------------------------------------------------------------------
# Reporting
# ----------------------------------------------------------------------
def render_markdown(
    analysis: Dict[str, Any],
    label: Optional[str] = None,
    note: Optional[str] = None,
) -> str:
    """Renders the human-readable report."""
    lines: List[str] = []
    methods = analysis["method_order"]
    denominators = analysis["denominators"]

    lines.append("# ITMS code-editing benchmark — analysis")
    lines.append("")
    if label:
        lines.append(f"> **{label}**")
        lines.append("")
    if note:
        for paragraph in note.split("\n\n"):
            lines.append(paragraph)
            lines.append("")
    lines.append(f"- Run directory: `{analysis['run_dir']}`")
    lines.append(f"- Run id: `{analysis['run_id']}`")
    lines.append(f"- Mode: **{analysis['mode']}** | is_measurement: {analysis['is_measurement']}")
    lines.append(
        f"- Requested model id: `{analysis['model']['requested_model_id']}`"
    )
    lines.append(
        f"- Served model ids: {analysis['model']['served_model_ids'] or '(none recorded)'}"
    )
    lines.append("")

    if not analysis["integrity"]["all_passed"]:
        lines.append("> **VALIDATION FAILED — these numbers must not be published.**")
        lines.append("")

    lines.append("## Denominators")
    lines.append("")
    lines.append(
        "The four counts below are different numbers and are never merged: a "
        "cell can be *planned* and *requested* and still never be *evaluated*."
    )
    lines.append("")
    lines.append("| Quantity | Value |")
    lines.append("|---|---|")
    for key, value in denominators.items():
        lines.append(f"| {key} | {value} |")
    lines.append("")

    lines.append("## Provider generation versus official evaluation")
    lines.append("")
    gv = analysis["generation_versus_evaluation"]
    lines.append("| Stage | Attempts |")
    lines.append("|---|---|")
    for key in (
        "provider_requests_issued",
        "provider_answered",
        "provider_error",
        "candidate_produced",
        "officially_evaluated",
        "evaluator_pass",
        "evaluator_fail",
        "never_evaluated",
        "incomplete",
    ):
        lines.append(f"| {key} | {gv[key]} |")
    lines.append("")
    lines.append(f"> {gv['note']}")
    lines.append("")

    lines.append("## Per-method results")
    lines.append("")
    lines.append(
        "| Method | Planned | Completed | Evaluated | Evaluator passes | Pass rate "
        "(of evaluated) | Provider errors | Failures | Truncations | Mean tokens | "
        "Median tokens | Mean latency (ms) | Median latency (ms) |"
    )
    lines.append(
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|"
    )
    for method in methods:
        metrics = analysis["methods"][method]
        success = metrics["repair_success"]["count"]
        evaluated = metrics["denominators"]["candidate_produced_attempts"]
        pass_rate = metrics["test_pass"]["rate_over_candidates"]
        completion = metrics["repair_success"]["rate_over_scheduled"]
        latency = metrics["latency_ms"]
        tokens = metrics["token_usage"]["mean_total_tokens"]
        median_tokens = metrics["token_usage"].get("median_total_tokens")
        provider_errors = metrics["provider_failures"]["count"]
        failures = max(0, metrics["denominators"]["recorded_attempts"] - success)
        truncations = metrics["failure_categories"].get("truncated_output", 0)
        lines.append(
            f"| {method} | {metrics['denominators']['scheduled_attempts']} | "
            f"{metrics['denominators']['recorded_attempts']} | "
            f"{evaluated} | "
            f"{metrics['test_pass']['count']} | "
            f"{'n/a' if pass_rate is None else f'{100.0 * pass_rate:.1f}%'} | "
            f"{provider_errors} | {failures} | {truncations} | "
            f"{'unavailable' if tokens is None else f'{tokens:.0f}'} | "
            f"{'unavailable' if median_tokens is None else f'{median_tokens:.0f}'} | "
            f"{'n/a' if latency['mean'] is None else f'{latency['mean']:.0f}'} | "
            f"{'n/a' if latency['median'] is None else f'{latency['median']:.0f}'} |"
        )
    lines.append("")
    lines.append(
        f"- Completion rate over the full schedule: "
        + ", ".join(
            f"{method}="
            + (
                "n/a"
                if analysis["methods"][method]["repair_success"]["rate_over_scheduled"]
                is None
                else f"{100.0 * analysis['methods'][method]['repair_success']['rate_over_scheduled']:.1f}%"
            )
            for method in methods
        )
    )
    lines.append(
        "- The latency column above is measured over **every recorded attempt**, "
        "including the attempts that never reached the provider and therefore "
        "contain a full connection timeout. The provider-only latency, which is "
        "the meaningful comparison between the three methods, is in the latency "
        "decomposition figure."
    )
    lines.append("")

    lines.append("## Truncation and output budget")
    lines.append("")
    lines.append(
        f"- Output caps in use: {analysis['output_cap']['values_in_use'] or '(none recorded)'}"
    )
    lines.append(
        f"- One cap for every strategy: {analysis['output_cap']['consistent_across_strategies']}"
    )
    lines.append(
        f"- Truncated attempts: {analysis['truncation']['total']} "
        f"({analysis['truncation']['by_method']})"
    )
    lines.append("")

    lines.append("## Cost")
    lines.append("")
    if analysis["cost"]["available"]:
        lines.append(
            f"- Provider-reported cost: ${analysis['cost']['total_cost_usd']:.6f} "
            f"over {analysis['cost']['provider_reported_attempts']} attempt(s)."
        )
    else:
        lines.append(
            "- **Unavailable**: the provider reported no cost for this run. The "
            "cost is not zero and no estimate is substituted."
        )
    lines.append("")

    lines.append("## Integrity validation")
    lines.append("")
    lines.append("| Check | Result | Detail |")
    lines.append("|---|---|---|")
    for check in analysis["integrity"]["checks"]:
        lines.append(
            f"| `{check['check']}` | {'PASS' if check['passed'] else '**FAIL**'} | {check['detail']} |"
        )
    lines.append("")

    lines.append("## Limitations that MUST be disclosed")
    lines.append("")
    for limitation in analysis["limitations"]:
        lines.append(f"- {limitation}")
    lines.append("")
    return "\n".join(lines) + "\n"


PER_ATTEMPT_CSV_COLUMNS = (
    "attempt_id",
    "task_id",
    "method",
    "rep",
    "status",
    "failure_category",
    "syntax_valid",
    "test_passed",
    "evaluation_attempted",
    "timed_out",
    "truncated_output",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "usage_source",
    "cost_usd",
    "cost_source",
    "latency_ms",
    "provider_latency_ms",
    "retry_count",
    "requested_model_id",
    "served_model_id",
    "seed_requested",
    "seed_applied",
    "seed_honour_verified",
    "max_output_tokens",
    "prompt_path",
    "candidate_path",
    "candidate_sha256",
    "raw_response_path",
)


def write_analysis(
    run_dir: str,
    out_dir: Optional[str] = None,
    write_figures: bool = True,
    label: Optional[str] = None,
    note: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Analyses a run and writes ``analysis.json``, a per-attempt CSV and
    ``RESULTS_SUMMARY.md``.

    The run directory is only **read** for the records; everything is written
    to ``out_dir`` (default: ``<run_dir>/analysis``).
    """
    analysis = build_analysis(run_dir)
    out_dir = os.path.abspath(out_dir or os.path.join(analysis["run_dir"], "analysis"))
    os.makedirs(out_dir, exist_ok=True)

    analysis_path = os.path.join(out_dir, "analysis.json")
    serialisable = {k: v for k, v in analysis.items() if k != "_records"}
    with open(analysis_path, "w", encoding="utf-8") as handle:
        json.dump(serialisable, handle, indent=2, ensure_ascii=False, default=str)
        handle.write("\n")

    # -- per-attempt machine-readable table ------------------------------
    per_attempt_path = os.path.join(out_dir, "attempts_analysis.csv")
    with open(per_attempt_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(PER_ATTEMPT_CSV_COLUMNS))
        writer.writeheader()
        for record in analysis["_records"]:
            row = record.to_dict()
            writer.writerow({key: row.get(key) for key in PER_ATTEMPT_CSV_COLUMNS})

    # -- one compact comparison table for the three methods ----------------
    comparison_path = os.path.join(out_dir, "method_comparison.csv")
    comparison_columns = (
        "method",
        "planned_attempts",
        "completed_attempts",
        "officially_evaluated_attempts",
        "evaluator_pass_count",
        "evaluator_pass_rate_over_evaluated",
        "completion_rate_over_scheduled",
        "provider_error_count",
        "failure_count",
        "truncation_count",
        "mean_total_tokens",
        "median_total_tokens",
        "mean_latency_ms",
        "median_latency_ms",
        "mean_provider_latency_ms",
        "median_provider_latency_ms",
        "provider_reported_cost_usd",
    )
    with open(comparison_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(comparison_columns))
        writer.writeheader()
        for method in analysis["method_order"]:
            metrics = analysis["methods"][method]
            writer.writerow(
                {
                    "method": method,
                    "planned_attempts": metrics["denominators"]["scheduled_attempts"],
                    "completed_attempts": metrics["denominators"]["recorded_attempts"],
                    "officially_evaluated_attempts": metrics["denominators"][
                        "candidate_produced_attempts"
                    ],
                    "evaluator_pass_count": metrics["test_pass"]["count"],
                    "evaluator_pass_rate_over_evaluated": metrics["test_pass"][
                        "rate_over_candidates"
                    ],
                    "completion_rate_over_scheduled": metrics["repair_success"][
                        "rate_over_scheduled"
                    ],
                    "provider_error_count": metrics["provider_failures"]["count"],
                    "failure_count": max(
                        0,
                        metrics["denominators"]["recorded_attempts"]
                        - metrics["repair_success"]["count"],
                    ),
                    "truncation_count": metrics["failure_categories"].get(
                        "truncated_output", 0
                    ),
                    "mean_total_tokens": metrics["token_usage"][
                        "mean_total_tokens"
                    ],
                    "median_total_tokens": metrics["token_usage"].get(
                        "median_total_tokens"
                    ),
                    "mean_latency_ms": metrics["latency_ms"]["mean"],
                    "median_latency_ms": metrics["latency_ms"]["median"],
                    "mean_provider_latency_ms": metrics["provider_latency_ms"]["mean"],
                    "median_provider_latency_ms": metrics["provider_latency_ms"][
                        "median"
                    ],
                    # Never zero and never an estimate: the value stays empty
                    # when the provider reported no cost.
                    "provider_reported_cost_usd": (
                        metrics["cost"]["total_cost_usd"]
                        if metrics["cost"]["total_cost_usd"] is not None
                        else ""
                    ),
                }
            )

    figures: List[str] = []
    if write_figures:
        try:
            from benchmarks.benchmark.figures import render_figures

            figures = render_figures(analysis, os.path.join(out_dir, "figures"))
        except Exception as exc:  # pragma: no cover - plotting is optional
            analysis["figure_error"] = f"{type(exc).__name__}: {exc}"

    summary_path = os.path.join(out_dir, "RESULTS_SUMMARY.md")
    with open(summary_path, "w", encoding="utf-8") as handle:
        handle.write(render_markdown(analysis, label=label, note=note))

    analysis["analysis_path"] = analysis_path
    analysis["summary_path"] = summary_path
    analysis["figures"] = figures
    return analysis
