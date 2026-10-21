"""
Read-only viewer for the ITMS vs Aider vs Cline comparative benchmark.

This module is a **pure reader**. It never runs a benchmark, never contacts a
model provider, never opens a socket and never writes a file. It reads:

    <run dir>/attempts.jsonl      one JSON object per attempt
    <run dir>/attempts.csv        the same attempts as CSV
    <run dir>/summary.json        the runner's summary
    <run dir>/run_metadata.json   the run's configuration
    <run dir>/BENCHMARK_INTEGRITY_REPORT.md
    <analysis dir>/analysis.json  every computed metric
    <analysis dir>/fig*.png       the generated figures

Every number the dashboard shows is derived here from those files, so the
dashboard can never display a statistic that disagrees with the data on disk.
Missing files are reported, never faked.

The older 120-attempt ITMS run is deliberately **not** read here: it is a
different experiment (prompt/edit-format variants of one model, not three
systems) and is kept strictly separate.
"""

from __future__ import annotations

import csv
import glob
import json
import os
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "PROJECT_ROOT",
    "RUNS_ROOT",
    "find_latest_live_run",
    "load_comparative_run",
    "compute_comparative_metrics",
    "figure_paths",
    "read_graph_bytes",
    "GRAPH_FILENAMES",
    "SYSTEM_LABELS",
]

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

RUNS_ROOT = os.path.join(PROJECT_ROOT, "experiments", "itms_aider_cline_v1", "runs")

#: The figures produced by the Phase 3 analysis, in presentation order.
GRAPH_FILENAMES = (
    "fig1_completion_rate_by_system.png",
    "fig2_evaluator_pass_rate_by_system.png",
    "fig3_patch_application_reliability.png",
    "fig4_failure_categories_by_system.png",
    "fig5_token_usage_by_system.png",
    "fig6_latency_by_system.png",
    "fig7_api_cost_by_system.png",
    "fig8_task_by_system_heatmap.png",
    "fig9_repetition_variation.png",
    "fig10_per_task_comparison.png",
)

SYSTEM_LABELS = {
    "itms": "ITMS",
    "aider": "Aider",
    "cline": "Cline",
}
SYSTEM_ORDER = ("itms", "aider", "cline")

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------


def find_latest_live_run(runs_root: str = RUNS_ROOT) -> Optional[str]:
    """The most recent Phase 2 live run directory, or ``None``.

    A directory qualifies when it contains ``attempts.jsonl`` whose records
    are **not** mock records.  Smoke-test directories (prefix ``smoke_``) are
    excluded, because they are readiness checks and not part of the reported
    experiment.
    """
    if not os.path.isdir(runs_root):
        return None
    candidates: List[Tuple[str, str]] = []
    for path in sorted(glob.glob(os.path.join(runs_root, "*"))):
        if not os.path.isdir(path):
            continue
        if os.path.basename(path).startswith("smoke"):
            continue
        jsonl_path = os.path.join(path, "attempts.jsonl")
        if not os.path.exists(jsonl_path):
            continue
        if not _contains_live_records(jsonl_path):
            continue
        candidates.append((_run_sort_key(path), path))
    if not candidates:
        return None
    candidates.sort()
    return candidates[-1][1]


def _run_sort_key(path: str) -> str:
    """Sort key: the run's start timestamp when known, else the directory name."""
    metadata_path = os.path.join(path, "run_metadata.json")
    if os.path.exists(metadata_path):
        try:
            with open(metadata_path, "r", encoding="utf-8") as fh:
                metadata = json.load(fh)
            for key in ("started_at_utc", "run_id"):
                value = metadata.get(key)
                if value:
                    return str(value)
        except (OSError, ValueError):
            pass
    return os.path.basename(path)


def _contains_live_records(jsonl_path: str) -> bool:
    """True when the file holds at least one non-mock attempt record."""
    try:
        with open(jsonl_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                if not record.get("is_mock"):
                    return True
    except (OSError, ValueError):
        return False
    return False


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------


def load_comparative_run(
    run_dir: Optional[str] = None,
    analysis_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Load the comparative dataset and its analysis, read-only."""
    if run_dir is None:
        run_dir = find_latest_live_run()
    run_dir = os.path.abspath(run_dir) if run_dir else ""
    if analysis_dir is None and run_dir:
        analysis_dir = os.path.join(run_dir, "analysis")
    analysis_dir = os.path.abspath(analysis_dir) if analysis_dir else ""

    out: Dict[str, Any] = {
        "run_dir": run_dir,
        "analysis_dir": analysis_dir,
        "records": [],
        "csv_rows": [],
        "summary": {},
        "metadata": {},
        "analysis": {},
        "integrity_report_path": (
            os.path.join(run_dir, "BENCHMARK_INTEGRITY_REPORT.md") if run_dir else ""
        ),
        "integrity_report_exists": False,
        "errors": [],
    }
    if not run_dir:
        out["errors"].append(
            "No live comparative run directory was found under "
            f"`{RUNS_ROOT}`."
        )
        return out

    def _read_json(path: str) -> Dict[str, Any]:
        if not os.path.exists(path):
            out["errors"].append(f"missing file: {os.path.relpath(path, PROJECT_ROOT)}")
            return {}
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError) as exc:
            out["errors"].append(f"unreadable file {path}: {exc}")
            return {}

    jsonl_path = os.path.join(run_dir, "attempts.jsonl")
    if os.path.exists(jsonl_path):
        with open(jsonl_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out["records"].append(json.loads(line))
                except ValueError:
                    continue
    else:
        out["errors"].append(f"missing file: {jsonl_path}")

    csv_path = os.path.join(run_dir, "attempts.csv")
    if os.path.exists(csv_path):
        with open(csv_path, "r", encoding="utf-8", newline="") as fh:
            out["csv_rows"] = list(csv.DictReader(fh))
    else:
        out["errors"].append(f"missing file: {csv_path}")

    out["summary"] = _read_json(os.path.join(run_dir, "summary.json"))
    out["metadata"] = _read_json(os.path.join(run_dir, "run_metadata.json"))
    out["analysis"] = _read_json(os.path.join(analysis_dir, "analysis.json"))
    out["integrity_report_exists"] = os.path.exists(out["integrity_report_path"])
    if not out["analysis"]:
        out["errors"].append(
            f"no analysis.json in `{analysis_dir}`; run "
            "`python -m experiments.itms_aider_cline_v1.analyze --run-dir <run>`"
        )
    return out


# ---------------------------------------------------------------------------
# derived metrics
# ---------------------------------------------------------------------------


def compute_comparative_metrics(run: Dict[str, Any]) -> Dict[str, Any]:
    """Recompute the headline metrics from the raw records.

    The dashboard deliberately recomputes rather than trusting
    ``analysis.json`` alone, so a stale analysis file cannot make the
    dashboard show numbers that disagree with ``attempts.jsonl``.
    """
    records = run.get("records") or []
    metadata = run.get("metadata") or {}
    systems = [s for s in SYSTEM_ORDER if any(r.get("system") == s for r in records)]
    tasks = metadata.get("tasks") or sorted({r.get("task_id") for r in records})
    reps = int(metadata.get("repetitions") or 0)
    planned_per_system = len(tasks) * reps

    out: Dict[str, Any] = {
        "systems": {},
        "tasks": tasks,
        "repetitions": reps,
        "planned_attempts": metadata.get("planned_attempts"),
        "recorded_attempts": len(records),
        "present_cells": {
            (r.get("task_id"), r.get("system"), r.get("repetition")) for r in records
        },
    }
    for system in systems:
        subset = [r for r in records if r.get("system") == system]
        evaluated = [r for r in subset if r.get("evaluation_attempted")]
        passed = [r for r in evaluated if r.get("test_passed") is True]
        completed = sum(1 for r in subset if r.get("execution_completed"))
        patched = sum(1 for r in subset if r.get("patch_applied"))
        failures: Dict[str, int] = {}
        for record in subset:
            key = str(record.get("failure_category"))
            failures[key] = failures.get(key, 0) + 1
        out["systems"][system] = {
            "label": SYSTEM_LABELS.get(system, system),
            "planned": planned_per_system,
            "recorded": len(subset),
            "completed": completed,
            "evaluated": len(evaluated),
            "passed": len(passed),
            "failed": len(evaluated) - len(passed),
            "patched": patched,
            "completion_rate": (completed / planned_per_system) if planned_per_system else None,
            "pass_rate": (len(passed) / len(evaluated)) if evaluated else None,
            "patch_rate": (patched / len(subset)) if subset else None,
            "failure_categories": failures,
            "seed_supported": bool(subset and subset[0].get("seed_supported")),
            "seed_applied_any": any(r.get("seed_applied") is not None for r in subset),
        }
    return out


def figure_paths(analysis_dir: str) -> List[Tuple[str, str]]:
    """(filename, absolute path) for every figure that exists."""
    out: List[Tuple[str, str]] = []
    for name in GRAPH_FILENAMES:
        path = os.path.join(analysis_dir, name)
        if os.path.exists(path):
            out.append((name, path))
    return out


def read_graph_bytes(path: str) -> Optional[bytes]:
    """Read a PNG, refusing anything that is not a PNG."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    if not data.startswith(_PNG_MAGIC):
        return None
    return data
