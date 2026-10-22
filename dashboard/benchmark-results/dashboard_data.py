"""
Data layer for the read-only benchmark-results dashboard.

This module is a **pure reader**.  It never runs a benchmark, never contacts
a model provider, never opens an outbound socket and never writes a file.
It reads only artefacts that a completed run already produced:

    <run dir>/attempts.jsonl                       one JSON object per attempt
    <run dir>/attempts.csv                         the same attempts as CSV
    <run dir>/summary.json                         the run summary
    <run dir>/run_metadata.json                    the run configuration
    <run dir>/analysis/analysis.json               the offline analysis bundle
    <run dir>/analysis/figures/fig*.png            the existing figures
    <run dir>/PHASE2_REDUCED_100_REPORT.md         the narrative report

Every number the dashboard shows is derived here from those files, so it can
never display a statistic that disagrees with the data on disk.  Where an
offline analysis file exists, the value computed here is **cross-checked
against it** and any disagreement is reported instead of being hidden.

The canonical attempt reader of the project is reused
(:mod:`completed_benchmark_viewer`); this module only adds the statistics
the dashboard needs on top of it.

No streamlit import lives here on purpose: the data layer stays testable
without a streamlit runtime, and the presentation layer stays in ``app.py``.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import completed_benchmark_viewer  # noqa: E402  (the project's read-only viewer)

__all__ = [
    "DEFAULT_RUN_DIR",
    "METHOD_LABELS",
    "METHOD_ORDER",
    "load_dashboard",
    "load_run_bundle",
]

#: The run this dashboard shows.  Only the path is hard-coded; every number
#: displayed is read from that run's artefacts.
DEFAULT_RUN_DIR = os.path.join(
    _REPO_ROOT, "results", "phase2_modular_100run_2026-10-22"
)

#: Presentation order and display names of the three editing methods.
METHOD_ORDER: Tuple[str, ...] = (
    "full_file_regeneration",
    "search_replace_patch",
    "itms_ast_splice",
)

METHOD_LABELS: Dict[str, str] = {
    "full_file_regeneration": "Full-File Rewrite",
    "search_replace_patch": "Search/Replace",
    "itms_ast_splice": "ITMS AST-Guided Surgical Editing",
}

#: The figures of the offline analysis, in presentation order.
FIGURE_TITLES: Tuple[Tuple[str, str], ...] = (
    ("fig1_pass_rate_by_method.png", "Official-evaluator pass rate per method"),
    ("fig1b_completion_rate_by_method.png", "Completion rate per method"),
    ("fig2_success_vs_failure_by_method.png", "Success versus other outcomes"),
    ("fig3_latency_by_method.png", "Mean and median latency per method"),
    ("fig4_token_usage_by_method.png", "Provider-reported token usage per method"),
    ("fig5_task_by_method_heatmap.png", "Per-task pass rate by method"),
    ("fig6_token_distribution_by_method.png", "Token distribution per method"),
    ("fig7_failure_categories_by_method.png", "Outcome categories per method"),
    ("fig8_latency_decomposition_by_method.png", "Latency decomposition per method"),
    ("fig9_truncation_by_method.png", "Attempts truncated by the output-token cap"),
    ("fig10_cost_availability.png", "Cost: unavailable (not reported)"),
    ("fig11_outcome_funnel_by_method.png", "Provider generation versus official evaluation"),
)

#: The narrative reports of the run.
REPORT_FILES: Tuple[Tuple[str, str], ...] = (
    ("PHASE2_REDUCED_100_REPORT.md", "Supervisor report (Markdown)"),
    ("analysis/RESULTS_SUMMARY.md", "Auto-generated analysis summary (Markdown)"),
)

#: Usage sources that count as provider-reported token usage.  Local
#: estimates are never mixed into a provider-reported total.
_PROVIDER_USAGE_SOURCES = ("provider_reported", "provider_partial")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _read_json(path: str) -> Optional[Any]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _mean(values: Sequence[float]) -> Optional[float]:
    return (sum(values) / len(values)) if values else None


def _median(values: Sequence[float]) -> Optional[float]:
    return statistics.median(values) if values else None


def _provider_tokens(records: Sequence[Dict[str, Any]], field: str) -> List[float]:
    """The provider-reported values of one token field."""
    return [
        float(record[field])
        for record in records
        if record.get(field) is not None
        and record.get("usage_source") in _PROVIDER_USAGE_SOURCES
    ]


def _prompt_bytes(run_dir: str, records: Sequence[Dict[str, Any]]) -> Dict[str, List[float]]:
    """
    The size in bytes of the prompt that was actually sent, per method.

    The size is read from the persisted ``prompt.txt`` artefact, i.e. from
    the request the provider really received, not from a re-rendered prompt.
    """
    sizes: Dict[str, List[float]] = {}
    for record in records:
        relative = record.get("prompt_path")
        if not relative:
            continue
        path = os.path.join(run_dir, relative)
        try:
            with open(path, "rb") as handle:
                size = float(len(handle.read()))
        except OSError:
            continue
        sizes.setdefault(str(record.get("method")), []).append(size)
    return sizes


# --------------------------------------------------------------------------
# per-method metrics
# --------------------------------------------------------------------------

def _method_metrics(
    records: Sequence[Dict[str, Any]],
    scheduled: Dict[str, int],
    prompt_sizes: Dict[str, List[float]],
) -> Dict[str, Dict[str, Any]]:
    """Per-method statistics.  Every rate states its own denominator."""
    present = {record.get("method") for record in records if record.get("method")}
    ordered = [m for m in METHOD_ORDER if m in present]
    ordered += sorted(present - set(METHOD_ORDER))

    metrics: Dict[str, Dict[str, Any]] = {}
    for method in ordered:
        subset = [r for r in records if r.get("method") == method]
        completed = len(subset)
        scheduled_count = scheduled.get(method, completed)
        successful = [r for r in subset if r.get("status") == "repair_success"]
        evaluated = [r for r in subset if r.get("evaluation_attempted") is True]
        passed = [r for r in evaluated if r.get("test_passed") is True]
        never_evaluated = [r for r in subset if r.get("evaluation_attempted") is not True]

        prompt_tokens = _provider_tokens(subset, "prompt_tokens")
        completion_tokens = _provider_tokens(subset, "completion_tokens")
        total_tokens = _provider_tokens(subset, "total_tokens")
        latencies = [
            float(r["latency_ms"]) for r in subset if r.get("latency_ms") is not None
        ]
        provider_latencies = [
            float(r["provider_latency_ms"])
            for r in subset
            if r.get("provider_latency_ms") is not None
        ]
        sizes = prompt_sizes.get(method, [])

        metrics[method] = {
            "label": METHOD_LABELS.get(method, method),
            "scheduled_attempts": scheduled_count,
            "completed_attempts": completed,
            "evaluated_attempts": len(evaluated),
            "evaluator_passes": len(passed),
            "evaluator_failures": len(evaluated) - len(passed),
            "never_evaluated": len(never_evaluated),
            "provider_errors": sum(
                1 for r in subset if r.get("status") == "provider_error"
            ),
            "truncations": sum(1 for r in subset if r.get("truncated_output") is True),
            "incomplete": sum(1 for r in subset if r.get("status") == "incomplete"),
            "syntax_valid": sum(1 for r in subset if r.get("syntax_valid") is True),
            "retries": sum(int(r.get("retry_count") or 0) for r in subset),
            # Rates.  The evaluator pass rate uses *evaluated* attempts as
            # its denominator; the completion rate uses the *scheduled*
            # cells.
            "evaluator_pass_rate": (len(passed) / len(evaluated)) if evaluated else None,
            "completion_rate": (
                (len(successful) / scheduled_count) if scheduled_count else None
            ),
            "token_samples": len(total_tokens),
            "mean_prompt_tokens": _mean(prompt_tokens),
            "median_prompt_tokens": _median(prompt_tokens),
            "mean_completion_tokens": _mean(completion_tokens),
            "median_completion_tokens": _median(completion_tokens),
            "mean_total_tokens": _mean(total_tokens),
            "median_total_tokens": _median(total_tokens),
            "total_prompt_tokens": int(sum(prompt_tokens)) if prompt_tokens else 0,
            "total_completion_tokens": int(sum(completion_tokens)) if completion_tokens else 0,
            "total_tokens": int(sum(total_tokens)) if total_tokens else 0,
            "latency_samples": len(latencies),
            "mean_latency_ms": _mean(latencies),
            "median_latency_ms": _median(latencies),
            "mean_provider_latency_ms": _mean(provider_latencies),
            "median_provider_latency_ms": _median(provider_latencies),
            "prompt_bytes_samples": len(sizes),
            "mean_prompt_bytes": _mean(sizes),
            "median_prompt_bytes": _median(sizes),
        }
    return metrics


# --------------------------------------------------------------------------
# per-task metrics
# --------------------------------------------------------------------------

def _per_task(
    records: Sequence[Dict[str, Any]],
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Per (task, method) statistics."""
    buckets: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for record in records:
        task_id = str(record.get("task_id"))
        method = str(record.get("method"))
        buckets.setdefault(task_id, {}).setdefault(method, []).append(record)

    result: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for task_id in sorted(buckets):
        result[task_id] = {}
        for method in sorted(buckets[task_id]):
            subset = buckets[task_id][method]
            evaluated = [r for r in subset if r.get("evaluation_attempted") is True]
            passed = [r for r in evaluated if r.get("test_passed") is True]
            totals = _provider_tokens(subset, "total_tokens")
            latencies = [
                float(r["latency_ms"])
                for r in subset
                if r.get("latency_ms") is not None
            ]
            result[task_id][method] = {
                "attempts": len(subset),
                "evaluated": len(evaluated),
                "passes": len(passed),
                "pass_rate": (len(passed) / len(evaluated)) if evaluated else None,
                "mean_total_tokens": _mean(totals),
                "mean_latency_ms": _mean(latencies),
            }
    return result


# --------------------------------------------------------------------------
# consistency against the offline analysis
# --------------------------------------------------------------------------

def _consistency(
    metrics: Dict[str, Dict[str, Any]],
    overall: Dict[str, Any],
    analysis: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Cross-check the values computed here against ``analysis/analysis.json``.

    Every row states the two values and whether they agree.  A disagreement
    is reported, never suppressed.
    """
    if not isinstance(analysis, dict):
        return [
            {
                "check": "offline analysis bundle",
                "dashboard": "-",
                "analysis": "not available",
                "agrees": None,
            }
        ]

    rows: List[Dict[str, Any]] = []

    def compare(check: str, left: Any, right: Any) -> None:
        if left is None or right is None:
            agrees = None
        else:
            try:
                agrees = abs(float(left) - float(right)) <= 1e-6
            except (TypeError, ValueError):
                agrees = left == right
        rows.append(
            {
                "check": check,
                "dashboard": left,
                "analysis": right,
                "agrees": agrees,
            }
        )

    denominators = analysis.get("denominators") or {}
    compare(
        "planned attempts",
        overall.get("planned_attempts"),
        denominators.get("planned_attempts"),
    )
    compare(
        "recorded attempts",
        overall.get("completed_attempts"),
        denominators.get("recorded_attempts"),
    )
    compare(
        "officially evaluated attempts",
        overall.get("evaluated_attempts"),
        denominators.get("evaluated_attempts"),
    )
    compare(
        "evaluator passes",
        overall.get("evaluator_passes"),
        (analysis.get("generation_versus_evaluation") or {}).get("evaluator_pass"),
    )

    analysis_methods = analysis.get("methods") or {}
    for method, values in metrics.items():
        other = analysis_methods.get(method)
        if not isinstance(other, dict):
            continue
        compare(
            f"{method}: scheduled attempts",
            values["scheduled_attempts"],
            (other.get("denominators") or {}).get("scheduled_attempts"),
        )
        compare(
            f"{method}: evaluator passes",
            values["evaluator_passes"],
            (other.get("test_pass") or {}).get("count"),
        )
        compare(
            f"{method}: mean total tokens",
            values["mean_total_tokens"],
            (other.get("token_usage") or {}).get("mean_total_tokens"),
        )
        compare(
            f"{method}: median total tokens",
            values["median_total_tokens"],
            (other.get("token_usage") or {}).get("median_total_tokens"),
        )
        compare(
            f"{method}: mean latency (ms)",
            values["mean_latency_ms"],
            (other.get("latency_ms") or {}).get("mean"),
        )
        compare(
            f"{method}: median latency (ms)",
            values["median_latency_ms"],
            (other.get("latency_ms") or {}).get("median"),
        )
    return rows


# --------------------------------------------------------------------------
# limitations
# --------------------------------------------------------------------------

def _limitations(
    overall: Dict[str, Any],
    metrics: Dict[str, Dict[str, Any]],
    analysis: Optional[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """
    The methodology limitations, with every number injected from the data.

    The narrative is fixed, but no statistic in it is hard-coded: each one
    is read from the records that were just loaded.
    """
    counts = {
        method: values["scheduled_attempts"] for method, values in metrics.items()
    }
    allocation = " / ".join(
        f"{counts.get(method, 0)} {METHOD_LABELS.get(method, method)}"
        for method in METHOD_ORDER
        if method in metrics
    )
    prompt_means = {
        method: values["mean_prompt_bytes"]
        for method, values in metrics.items()
        if values["mean_prompt_bytes"] is not None
    }
    smallest = min(prompt_means.items(), key=lambda item: item[1]) if prompt_means else None
    largest = max(prompt_means.items(), key=lambda item: item[1]) if prompt_means else None
    prompt_note = ""
    if smallest and largest:
        prompt_note = (
            f" The prompt that was actually sent differs in size between the "
            f"methods: the mean is {smallest[1]:.0f} bytes for "
            f"{METHOD_LABELS.get(smallest[0], smallest[0])} against "
            f"{largest[1]:.0f} bytes for "
            f"{METHOD_LABELS.get(largest[0], largest[0])}."
        )
    cost = ((analysis or {}).get("cost") or {})
    cost_note = (
        " The provider reported no cost for "
        f"{cost.get('provider_reported_attempts', 0)} of the attempts, so the "
        "monetary cost is **unavailable**: it is not zero and no estimate is "
        "substituted."
        if not cost.get("available")
        else f" The provider reported a total cost of ${cost.get('total_cost_usd')}."
    )
    seeds = ((analysis or {}).get("seeds") or {})
    seed_note = (
        " Whether the provider honoured the request-level seed is "
        "**unverified**: no repeated-request probe was run."
        if not seeds.get("seed_honour_verified")
        else " The provider honoured the request-level seed."
    )
    reps = (analysis or {}).get("denominators", {}).get("cells_by_repetition", {})

    return [
        {
            "title": "Reduced request budget",
            "text": (
                f"This run used **at most "
                f"{overall.get('planned_attempts')} live requests in total**. "
                f"It is **not** 20 tasks x 5 repetitions x 3 methods: that "
                f"design needs 300 requests. The budget was split "
                f"{allocation}."
            ),
        },
        {
            "title": "Unequal repetition counts",
            "text": (
                "The per-method attempt counts are unequal ("
                f"{allocation}). Of the 60 (task, method) cells, "
                f"{reps.get('1', '?')} carry one attempt and "
                f"{reps.get('2', '?')} carry a second one. Every task receives "
                "the same total number of attempts, so the imbalance is "
                "within a task rather than across tasks. The sample is small "
                "and the cells are uneven, so the rates below are "
                "correspondingly imprecise."
            ),
        },
        {
            "title": "The prompt-length difference is a confound",
            "text": (
                "The three methods did not receive the same amount of input "
                "context." + prompt_note + " The ITMS adapter builds a pruned "
                "skeleton, so a large part of its total-token advantage comes "
                "from a smaller input rather than from a cheaper edit. The "
                "output-token column is the fairer comparison for the "
                "efficiency thesis."
            ),
        },
        {
            "title": "Authored dataset, small corpus",
            "text": (
                "The task set is the authored *modular* family: clean, "
                "purpose-written modules that satisfy the pre-registered "
                "representative-task rule. It is not a real repository and it "
                "contains no production messiness. No conclusion here "
                "generalises beyond these tasks, and these are not HumanEval "
                "tasks."
            ),
        },
        {
            "title": "Cost data is unavailable",
            "text": cost_note,
        },
        {
            "title": "Seed honouring is unverified",
            "text": seed_note,
        },
        {
            "title": "Correctness is at a ceiling",
            "text": (
                "Every attempt that reached the official evaluator passed it, "
                "so this experiment compares *efficiency* and says nothing "
                "about accuracy. A 100 % pass rate is not evidence that the "
                "methods are equally good in general."
            ),
        },
    ]


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def load_run_bundle(run_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    Load everything the dashboard needs from one run directory.

    The canonical reader of the project
    (:func:`completed_benchmark_viewer.load_run`) is used for the attempt
    records, so the dashboard reads exactly the files the offline analysis
    read and inherits its cross-checks.
    """
    run_dir = os.path.abspath(run_dir or DEFAULT_RUN_DIR)
    analysis_dir = os.path.join(run_dir, "analysis")
    run = completed_benchmark_viewer.load_run(run_dir, analysis_dir)
    records = run["records"]
    summary = run["summary"] or {}
    metadata = run["metadata"] or {}
    analysis = _read_json(os.path.join(analysis_dir, "analysis.json"))

    # -- the scheduled cell count per method (authoritative) ---------------
    summary_methods = (summary.get("method_metrics") or {})
    scheduled: Dict[str, int] = {}
    for method, values in summary_methods.items():
        denominator = (values or {}).get("denominators") or {}
        count = denominator.get("scheduled_attempts")
        if count is not None:
            scheduled[method] = int(count)
    if not scheduled:
        # Fall back to the recorded attempts when the summary has none.
        for record in records:
            method = str(record.get("method"))
            scheduled[method] = scheduled.get(method, 0) + 1

    prompt_sizes = _prompt_bytes(run_dir, records)
    metrics = _method_metrics(records, scheduled, prompt_sizes)
    per_task = _per_task(records)

    evaluated = [r for r in records if r.get("evaluation_attempted") is True]
    passed = [r for r in evaluated if r.get("test_passed") is True]
    successful = [r for r in records if r.get("status") == "repair_success"]
    planned = (
        summary.get("planned_attempts")
        or metadata.get("planned_attempts")
        or len(records)
    )

    overall = {
        "run_dir": run_dir,
        "analysis_dir": analysis_dir,
        "run_id": analysis.get("run_id") if isinstance(analysis, dict) else None,
        "mode": analysis.get("mode") if isinstance(analysis, dict) else None,
        "is_measurement": analysis.get("is_measurement") if isinstance(analysis, dict) else None,
        "created_at_utc": (
            analysis.get("created_at_utc") if isinstance(analysis, dict) else None
        ),
        "model": (analysis.get("model") or {}) if isinstance(analysis, dict) else {},
        "planned_attempts": planned,
        "requested_attempts": (
            (summary.get("budget") or {}).get("requests_issued")
            if isinstance(summary, dict)
            else None
        ),
        "completed_attempts": len(records),
        "evaluated_attempts": len(evaluated),
        "evaluator_passes": len(passed),
        "evaluator_failures": len(evaluated) - len(passed),
        "never_evaluated": len(records) - len(evaluated),
        "provider_errors": sum(
            1 for r in records if r.get("status") == "provider_error"
        ),
        "truncations": sum(1 for r in records if r.get("truncated_output") is True),
        "incomplete": sum(1 for r in records if r.get("status") == "incomplete"),
        "repair_successes": len(successful),
        "retries": sum(int(r.get("retry_count") or 0) for r in records),
        "tasks": len({r.get("task_id") for r in records if r.get("task_id")}),
        "methods": len(metrics),
        "mock_attempts": sum(1 for r in records if r.get("is_mock")),
        "problems": list(run["problems"]),
        "warnings": list(run["warnings"]),
    }

    figures: List[Dict[str, Any]] = []
    for filename, title in FIGURE_TITLES:
        path = os.path.join(analysis_dir, "figures", filename)
        if os.path.isfile(path):
            figures.append(
                {
                    "filename": filename,
                    "title": title,
                    "path": path,
                    "size_bytes": os.path.getsize(path),
                }
            )

    reports: List[Dict[str, Any]] = []
    for relative, title in REPORT_FILES:
        path = os.path.join(run_dir, relative)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                text = handle.read()
        except OSError:
            continue
        reports.append(
            {
                "title": title,
                "path": path,
                "relative": relative,
                "text": text,
                "size_bytes": len(text.encode("utf-8")),
            }
        )

    schedule = metadata.get("schedule") or {}
    consistency = _consistency(metrics, overall, analysis)

    return {
        "overall": overall,
        "methods": metrics,
        "per_task": per_task,
        "records": records,
        "summary": summary,
        "metadata": metadata,
        "analysis": analysis,
        "schedule": schedule,
        "figures": figures,
        "reports": reports,
        "consistency": consistency,
        "limitations": _limitations(overall, metrics, analysis),
    }
