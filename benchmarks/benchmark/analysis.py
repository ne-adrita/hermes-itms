"""
Metrics and analysis for the Phase 1 benchmark.

Denominator policy
------------------
Every metric states the denominator it uses, because "repair success rate"
and "test pass rate" do not share one:

``scheduled``
    every attempt in the schedule (tasks x methods x reps).  A failed or
    incomplete attempt stays in this denominator; it is never dropped.
``attempted``
    attempts that produced a provider response.
``candidate_produced``
    attempts where the model's output was parsed and applied to a candidate
    module.  Syntax-valid rate and test-pass rate use this denominator,
    because a malformed output cannot be syntactically valid or testable.
``measured``
    attempts for which the provider actually reported a value (tokens, cost).
    Missing measurements are reported as missing, never as zero.

Statistical unit
----------------
Tasks are the independent units (20 of them).  The five repetitions are
repeated observations of the same task, not five independent tasks.  All
comparisons are therefore paired at the task level, and the uncertainty is
reported as a bootstrap interval over tasks.  With 20 tasks no strong
significance claim is made.

Mock-mode results are labelled as pipeline tests, never as measurements.
"""

from __future__ import annotations

import math
import random
import statistics
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from benchmarks.benchmark.records import AttemptRecord, AttemptStatus, FailureCategory

__all__ = [
    "summarise_attempts",
    "method_metrics",
    "paired_task_comparison",
    "bootstrap_ci",
    "BOOTSTRAP_SEED",
]

#: Seed used for the percentile bootstrap over tasks. Recorded in the run
#: metadata so a confidence interval can be reproduced exactly.
BOOTSTRAP_SEED = 20260101


def _safe_div(numerator: float, denominator: float) -> Optional[float]:
    if not denominator:
        return None
    return numerator / denominator


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    if not sorted_values:
        return float("nan")
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * q
    lower = math.floor(pos)
    upper = math.ceil(pos)
    if lower == upper:
        return sorted_values[int(pos)]
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (pos - lower)


def bootstrap_ci(
    values: Sequence[float],
    iterations: int = 2000,
    seed: int = BOOTSTRAP_SEED,
    confidence: float = 0.95,
) -> Dict[str, Optional[float]]:
    """Percentile bootstrap confidence interval over the given values."""
    clean = [float(v) for v in values]
    if not clean:
        return {"mean": None, "ci_low": None, "ci_high": None, "n": 0}
    if len(clean) == 1:
        return {"mean": clean[0], "ci_low": clean[0], "ci_high": clean[0], "n": 1}
    rng = random.Random(seed)
    means: List[float] = []
    n = len(clean)
    for _ in range(iterations):
        sample = [clean[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    alpha = (1.0 - confidence) / 2.0
    return {
        "mean": sum(clean) / n,
        "ci_low": _percentile(means, alpha),
        "ci_high": _percentile(means, 1.0 - alpha),
        "n": n,
    }


def method_metrics(
    records: Sequence[AttemptRecord],
    scheduled: int,
    method: str,
    tasks_count: int,
    reps: int,
) -> Dict[str, Any]:
    """Compute the metric bundle for one editing method."""
    subset = [r for r in records if r.method == method]
    attempted = [r for r in subset if r.status != AttemptStatus.PROVIDER_ERROR]
    candidates = [
        r
        for r in subset
        if r.status in (AttemptStatus.REPAIR_SUCCESS, AttemptStatus.REPAIR_FAILED)
    ]
    syntax_valid = [r for r in candidates if r.syntax_valid is True]
    # A repair counts as successful only when the attempt is a completed
    # repair attempt. An attempt that was demoted (for example because its
    # artefacts could not be written) is never counted as a success, even if
    # the official test happened to pass before the demotion.
    test_passed = [r for r in candidates if r.test_passed is True]
    repair_successes = [r for r in candidates if r.status == AttemptStatus.REPAIR_SUCCESS]

    # Provider-reported usage only. Local estimates live in separate record
    # fields and are never included in a provider-reported total.
    usage_records = [r for r in subset if r.total_tokens is not None]
    partial_usage_records = [
        r
        for r in subset
        if r.total_tokens is None
        and (r.prompt_tokens is not None or r.completion_tokens is not None)
    ]
    estimated_only_records = [
        r
        for r in subset
        if r.total_tokens is None
        and r.prompt_tokens is None
        and r.completion_tokens is None
        and (r.estimated_prompt_tokens is not None or r.estimated_completion_tokens is not None)
    ]
    cost_records = [r for r in subset if r.cost_usd is not None]

    latencies = [r.latency_ms for r in subset if r.latency_ms is not None]
    provider_latencies = [
        r.provider_latency_ms for r in subset if r.provider_latency_ms is not None
    ]

    # Task-level view: a task counts as solved by this method when at least
    # one repetition produced a passing candidate. A repetition counts as a
    # pass only when the attempt completed as a repair success, so an attempt
    # demoted for an artefact-persistence failure is never counted as a pass
    # (this must agree with ``solved_tasks`` and ``repair_success``).
    per_task: Dict[str, List[AttemptRecord]] = {}
    for record in subset:
        per_task.setdefault(record.task_id, []).append(record)
    solved_tasks = sum(
        1
        for task_records in per_task.values()
        if any(r.status == AttemptStatus.REPAIR_SUCCESS for r in task_records)
    )
    task_pass_rates = {
        task_id: (
            sum(
                1
                for r in task_records
                if r.status == AttemptStatus.REPAIR_SUCCESS
            )
            / len(task_records)
            if task_records
            else None
        )
        for task_id, task_records in per_task.items()
    }

    return {
        "method": method,
        "denominators": {
            "scheduled_attempts": scheduled,
            "recorded_attempts": len(subset),
            "attempted_attempts": len(attempted),
            "candidate_produced_attempts": len(candidates),
            "tasks": tasks_count,
            "reps_per_task": reps,
        },
        "repair_success": {
            "count": len(repair_successes),
            "rate_over_scheduled": _safe_div(len(repair_successes), scheduled),
            "rate_over_attempted": _safe_div(len(repair_successes), len(attempted)),
            "note": (
                "A repair counts as successful only for a completed repair "
                "attempt; attempts demoted for an artefact-persistence failure "
                "are never counted as successes."
            ),
        },
        "task_level": {
            "tasks_with_at_least_one_success": solved_tasks,
            "tasks_total": tasks_count,
            "task_success_rate": _safe_div(solved_tasks, tasks_count),
            "mean_task_pass_rate": (
                sum(v for v in task_pass_rates.values() if v is not None)
                / len(task_pass_rates)
                if task_pass_rates
                else None
            ),
            "per_task_pass_rate": task_pass_rates,
        },
        "syntax_valid": {
            "count": len(syntax_valid),
            "rate_over_candidates": _safe_div(len(syntax_valid), len(candidates)),
        },
        "test_pass": {
            "count": len(test_passed),
            "rate_over_candidates": _safe_div(len(test_passed), len(candidates)),
        },
        "malformed_output": {
            "count": sum(1 for r in subset if r.status == AttemptStatus.OUTPUT_PARSE_ERROR),
            "rate_over_scheduled": _safe_div(
                sum(1 for r in subset if r.status == AttemptStatus.OUTPUT_PARSE_ERROR),
                scheduled,
            ),
        },
        "provider_failures": {
            "count": sum(1 for r in subset if r.status == AttemptStatus.PROVIDER_ERROR),
            "rate_over_scheduled": _safe_div(
                sum(1 for r in subset if r.status == AttemptStatus.PROVIDER_ERROR), scheduled
            ),
        },
        "timeouts": {
            "count": sum(1 for r in subset if r.timed_out),
            "rate_over_scheduled": _safe_div(
                sum(1 for r in subset if r.timed_out), scheduled
            ),
        },
        "failure_categories": _count_by(subset, lambda r: r.failure_category),
        "status_counts": _count_by(subset, lambda r: r.status),
        "latency_ms": {
            "mean": (sum(latencies) / len(latencies)) if latencies else None,
            "median": statistics.median(latencies) if latencies else None,
            "min": min(latencies) if latencies else None,
            "max": max(latencies) if latencies else None,
            "measured_attempts": len(latencies),
        },
        "provider_latency_ms": {
            "mean": (
                sum(provider_latencies) / len(provider_latencies)
                if provider_latencies
                else None
            ),
            "median": statistics.median(provider_latencies) if provider_latencies else None,
            "measured_attempts": len(provider_latencies),
        },
        "token_usage": {
            "provider_reported_attempts": len(usage_records),
            "provider_partial_attempts": len(partial_usage_records),
            "local_estimate_only_attempts": len(estimated_only_records),
            # An attempt is "missing" only when the provider reported no token
            # value at all. A partially reported attempt is counted
            # separately and is never silently treated as complete.
            "missing_attempts": len(subset)
            - len(usage_records)
            - len(partial_usage_records),
            "prompt_tokens_total": (
                sum(r.prompt_tokens or 0 for r in usage_records) if usage_records else None
            ),
            "completion_tokens_total": (
                sum(r.completion_tokens or 0 for r in usage_records)
                if usage_records
                else None
            ),
            "total_tokens_total": (
                sum(r.total_tokens or 0 for r in usage_records) if usage_records else None
            ),
            "mean_total_tokens": (
                sum(r.total_tokens or 0 for r in usage_records) / len(usage_records)
                if usage_records
                else None
            ),
            "note": (
                "Totals include provider-reported values only. Local token "
                "estimates are stored per attempt in "
                "estimated_prompt_tokens / estimated_completion_tokens and are "
                "never added to a provider-reported total."
            ),
        },
        "cost": {
            "provider_reported_attempts": len(cost_records),
            "missing_attempts": len(subset) - len(cost_records),
            "total_cost_usd": (
                sum(r.cost_usd or 0.0 for r in cost_records) if cost_records else None
            ),
            "source": cost_records[0].cost_source if cost_records else None,
        },
        "mock_attempts": sum(1 for r in subset if r.is_mock),
    }


def _count_by(records: Iterable[AttemptRecord], key) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for record in records:
        value = key(record)
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def paired_task_comparison(
    records: Sequence[AttemptRecord],
    method_a: str,
    method_b: str,
    tasks: Sequence[str],
    seed: int = BOOTSTRAP_SEED,
) -> Dict[str, Any]:
    """
    Paired task-level comparison of two editing methods.

    For every task the per-repair success rate of each method is computed, and
    the difference (a - b) is summarised with a bootstrap interval over tasks.
    """
    def task_rates(method: str) -> Dict[str, List[float]]:
        buckets: Dict[str, List[float]] = {}
        for record in records:
            if record.method != method:
                continue
            buckets.setdefault(record.task_id, []).append(
                1.0 if record.status == AttemptStatus.REPAIR_SUCCESS else 0.0
            )
        return {
            task_id: (sum(values) / len(values) if values else 0.0)
            for task_id, values in buckets.items()
        }

    rates_a = task_rates(method_a)
    rates_b = task_rates(method_b)
    shared = [t for t in tasks if t in rates_a and t in rates_b]
    differences = [rates_a[t] - rates_b[t] for t in shared]

    wins = sum(1 for d in differences if d > 0)
    losses = sum(1 for d in differences if d < 0)
    ties = len(differences) - wins - losses

    return {
        "method_a": method_a,
        "method_b": method_b,
        "unit_of_analysis": "task",
        "paired_tasks": len(shared),
        "mean_difference_a_minus_b": (
            sum(differences) / len(differences) if differences else None
        ),
        "bootstrap_95ci": bootstrap_ci(differences, seed=seed),
        "task_wins_a": wins,
        "task_losses_a": losses,
        "task_ties": ties,
        "per_task_difference": {t: rates_a[t] - rates_b[t] for t in shared},
        "caveat": (
            "20 tasks; the bootstrap interval over tasks reflects task-level "
            "variability only. No strong statistical-significance claim is made, "
            "and this is not a HumanEval pass rate."
        ),
    }


def summarise_attempts(records: Sequence[AttemptRecord]) -> Dict[str, Any]:
    """Summarise a set of attempt records."""
    methods = sorted({r.method for r in records})
    is_mock = any(r.is_mock for r in records)
    return {
        "schema_version": "1.0",
        "attempts_recorded": len(records),
        "attempts_with_local_token_estimates": sum(
            1 for r in records if r.estimated_prompt_tokens is not None
            or r.estimated_completion_tokens is not None
        ),
        "attempts_truncated_by_provider": sum(1 for r in records if r.truncated_output),
        "observed_served_model_ids": sorted(
            {r.served_model_id for r in records if r.served_model_id}
        ),
        "methods": methods,
        "contains_mock_results": is_mock,
        "is_measurement": not is_mock,
        "mock_warning": (
            "MOCK RUN — pipeline test only; not a measurement of model quality."
            if is_mock
            else None
        ),
        "status_counts": _count_by(records, lambda r: r.status),
        "failure_categories": _count_by(records, lambda r: r.failure_category),
        "attempts_with_provider_reported_usage": sum(
            1 for r in records if r.total_tokens is not None
        ),
        "attempts_with_provider_reported_cost": sum(
            1 for r in records if r.cost_usd is not None
        ),
    }
