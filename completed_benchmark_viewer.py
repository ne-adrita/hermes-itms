"""
Read-only viewer for the completed 120-attempt ITMS code-editing benchmark.

This module is a **pure reader**. It never runs a benchmark, never contacts a
model provider, never opens a socket and never writes a file. It only reads the
artefacts that a completed run already produced:

    <run dir>/attempts.jsonl      one JSON object per attempt
    <run dir>/attempts.csv        the same attempts as CSV
    <run dir>/summary.json        the run summary written by the runner
    <run dir>/run_metadata.json   the run configuration written by the runner

and the figure files of the offline analysis:

    <analysis dir>/fig*.png

Every number the dashboard shows is derived here from those files, so the
dashboard can never display a hard-coded statistic that disagrees with the
data on disk. Missing files are reported, never faked.

No streamlit import lives here on purpose: the data layer stays testable
without a streamlit runtime, and the presentation layer stays in ``app.py``.
"""

from __future__ import annotations

import csv
import json
import os
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "PROJECT_ROOT",
    "DEFAULT_RUN_DIR",
    "DEFAULT_ANALYSIS_DIR",
    "GRAPH_FILENAMES",
    "METHOD_ORDER",
    "load_run",
    "compute_overall",
    "compute_method_metrics",
    "graph_path",
    "read_graph_bytes",
    "summarise_problems",
]

#: Project root, resolved from this file's location so the dashboard never
#: depends on the current working directory.
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

DEFAULT_RUN_DIR = os.path.join(
    PROJECT_ROOT, "results", "full_20x5_20261022T122009Z"
)
DEFAULT_ANALYSIS_DIR = os.path.join(
    PROJECT_ROOT, "results", "full_20x5_20261022T122009Z"
)

#: The figures produced by the offline analysis, in presentation order.
GRAPH_FILENAMES = (
    "fig1_pass_rate_by_method.png",
    "fig1b_completion_rate_by_method.png",
    "fig2_success_vs_failure_by_method.png",
    "fig3_mean_execution_time_by_method.png",
    "fig4_mean_token_usage_by_method.png",
    "fig5_task_by_method_heatmap.png",
)

#: Preferred display order for the three editing methods.
METHOD_ORDER = (
    "full_file_regeneration",
    "search_replace_patch",
    "itms_ast_splice",
)

#: Usage sources that count as provider-reported token usage. Local estimates
#: are never mixed into a provider-reported total.
_PROVIDER_USAGE_SOURCES = ("provider_reported", "provider_partial")

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _read_json(path: str, problems: List[str], label: str) -> Optional[Any]:
    if not os.path.isfile(path):
        problems.append(f"missing {label}: {path}")
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        problems.append(f"unreadable {label} ({type(exc).__name__}: {exc}): {path}")
        return None


def _read_jsonl(path: str, problems: List[str]) -> List[Dict[str, Any]]:
    if not os.path.isfile(path):
        problems.append(f"missing attempts file: {path}")
        return []
    records: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError as exc:
                    problems.append(
                        f"malformed JSON on line {lineno} of {path}: {exc}"
                    )
                    continue
                if isinstance(record, dict):
                    records.append(record)
                else:
                    problems.append(f"line {lineno} of {path} is not a JSON object")
    except OSError as exc:
        problems.append(f"unreadable attempts file ({exc}): {path}")
    return records


def _read_csv_rows(path: str, problems: List[str]) -> List[Dict[str, str]]:
    if not os.path.isfile(path):
        problems.append(f"missing attempts file: {path}")
        return []
    try:
        with open(path, "r", encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))
    except OSError as exc:
        problems.append(f"unreadable attempts file ({exc}): {path}")
        return []


def load_run(
    run_dir: str = DEFAULT_RUN_DIR, analysis_dir: str = DEFAULT_ANALYSIS_DIR
) -> Dict[str, Any]:
    """Load a completed run. Never raises; problems are collected instead."""
    problems: List[str] = []
    warnings: List[str] = []

    attempts_path = os.path.join(run_dir, "attempts.jsonl")
    records = _read_jsonl(attempts_path, problems)
    csv_rows = _read_csv_rows(os.path.join(run_dir, "attempts.csv"), problems)
    summary = _read_json(
        os.path.join(run_dir, "summary.json"), problems, "run summary"
    )
    metadata = _read_json(
        os.path.join(run_dir, "run_metadata.json"), problems, "run metadata"
    )

    if not records:
        problems.append(
            "no attempt records could be loaded, so no statistics can be displayed"
        )

    # -- cross-checks between the artefacts -------------------------------
    if records and csv_rows:
        jsonl_ids = [r.get("attempt_id") for r in records]
        csv_ids = [row.get("attempt_id") for row in csv_rows]
        if jsonl_ids != csv_ids:
            warnings.append(
                "the JSONL and CSV attempt orders or contents do not match exactly"
            )
        else:
            warnings.append(
                f"JSONL and CSV agree on all {len(jsonl_ids)} attempt ids"
            )
    if records and isinstance(summary, dict):
        recorded = summary.get("recorded_attempts")
        if recorded is not None and recorded != len(records):
            warnings.append(
                f"summary.json reports {recorded} recorded attempts but "
                f"{len(records)} records were read from attempts.jsonl"
            )

    return {
        "run_dir": os.path.abspath(run_dir),
        "analysis_dir": os.path.abspath(analysis_dir),
        "attempts_path": attempts_path,
        "records": records,
        "csv_rows": csv_rows,
        "summary": summary if isinstance(summary, dict) else {},
        "metadata": metadata if isinstance(metadata, dict) else {},
        "problems": problems,
        "warnings": warnings,
    }


def _mean(values: List[float]) -> Optional[float]:
    return (sum(values) / len(values)) if values else None


def compute_overall(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Overall experiment statistics, derived from the attempt records."""
    total = len(records)
    successful = [r for r in records if r.get("status") == "repair_success"]
    evaluated = [r for r in records if r.get("evaluation_attempted") is True]
    evaluator_passed = [r for r in evaluated if r.get("test_passed") is True]
    # An attempt that never reached the official evaluator failed *before*
    # evaluation (truncated output, unparsable output, provider error). It is
    # an output failure, not an evaluator failure.
    output_failures = [r for r in records if r.get("evaluation_attempted") is not True]
    methods = sorted({r.get("method") for r in records if r.get("method")})
    tasks = sorted({r.get("task_id") for r in records if r.get("task_id")})
    served = sorted({r.get("served_model_id") for r in records if r.get("served_model_id")})
    requested = sorted(
        {r.get("requested_model_id") for r in records if r.get("requested_model_id")}
    )
    failure_categories: Dict[str, int] = {}
    for r in output_failures:
        key = r.get("failure_category") or "unknown"
        failure_categories[key] = failure_categories.get(key, 0) + 1
    return {
        "total_scheduled": total,
        "successful": len(successful),
        "output_failures": len(output_failures),
        "reached_evaluation": len(evaluated),
        "evaluator_passed": len(evaluator_passed),
        "evaluator_failed": len(evaluated) - len(evaluator_passed),
        "completion_rate": (len(successful) / total) if total else None,
        "evaluator_pass_rate": (len(evaluator_passed) / len(evaluated))
        if evaluated
        else None,
        "methods": methods,
        "method_count": len(methods),
        "task_count": len(tasks),
        "served_model_ids": served,
        "requested_model_ids": requested,
        "mock_attempts": sum(1 for r in records if r.get("is_mock")),
        "failure_categories": failure_categories,
    }


def compute_method_metrics(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Per-method statistics. Every rate states its own denominator."""
    present = {r.get("method") for r in records if r.get("method")}
    ordered = [m for m in METHOD_ORDER if m in present]
    ordered += sorted(present - set(METHOD_ORDER))

    metrics: Dict[str, Dict[str, Any]] = {}
    for method in ordered:
        subset = [r for r in records if r.get("method") == method]
        scheduled = len(subset)
        successful = [r for r in subset if r.get("status") == "repair_success"]
        evaluated = [r for r in subset if r.get("evaluation_attempted") is True]
        evaluator_passed = [r for r in evaluated if r.get("test_passed") is True]
        output_failures = [
            r for r in subset if r.get("evaluation_attempted") is not True
        ]
        latencies = [
            float(r["latency_ms"]) for r in subset if r.get("latency_ms") is not None
        ]
        tokens = [
            float(r["total_tokens"])
            for r in subset
            if r.get("total_tokens") is not None
            and r.get("usage_source") in _PROVIDER_USAGE_SOURCES
        ]
        metrics[method] = {
            "scheduled": scheduled,
            "successful": len(successful),
            "output_failures": len(output_failures),
            "reached_evaluation": len(evaluated),
            "evaluator_passed": len(evaluator_passed),
            "completion_rate": (len(successful) / scheduled) if scheduled else None,
            "evaluator_pass_rate": (len(evaluator_passed) / len(evaluated))
            if evaluated
            else None,
            "mean_latency_ms": _mean(latencies),
            "latency_samples": len(latencies),
            "mean_total_tokens": _mean(tokens),
            "token_samples": len(tokens),
            "total_tokens": int(sum(tokens)) if tokens else 0,
        }
    return metrics


def graph_path(analysis_dir: str, filename: str) -> str:
    """Absolute path of a figure file (it may or may not exist)."""
    return os.path.join(os.path.abspath(analysis_dir), filename)


def read_graph_bytes(analysis_dir: str, filename: str) -> Optional[bytes]:
    """Return the bytes of a figure, or ``None`` when it is missing/invalid."""
    path = graph_path(analysis_dir, filename)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    if not data.startswith(_PNG_MAGIC):
        return None
    return data


def summarise_problems(problems: List[str]) -> Tuple[str, str]:
    """Split collected problems into (missing, malformed) headlines."""
    missing = [p for p in problems if p.startswith("missing")]
    malformed = [p for p in problems if not p.startswith("missing")]
    return (
        f"{len(missing)} required file(s) could not be found"
        if missing
        else "",
        f"{len(malformed)} file(s) could not be parsed"
        if malformed
        else "",
    )
