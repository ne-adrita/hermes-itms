"""
Deterministic figures for a benchmark analysis.

Every figure states its denominator in the title, and every figure that could
be mistaken for a measurement of cost states explicitly that the cost was
**unavailable** when the provider reported none.  A ``$0`` bar is never drawn
for a cost that was not reported.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

__all__ = ["render_figures", "FIGURE_FILENAMES"]


def _pyplot():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


FIGURE_FILENAMES = (
    "fig1_pass_rate_by_method.png",
    "fig1b_completion_rate_by_method.png",
    "fig2_success_vs_failure_by_method.png",
    "fig3_latency_by_method.png",
    "fig4_token_usage_by_method.png",
    "fig5_task_by_method_heatmap.png",
    "fig6_token_distribution_by_method.png",
    "fig7_failure_categories_by_method.png",
    "fig8_latency_decomposition_by_method.png",
    "fig9_truncation_by_method.png",
    "fig10_cost_availability.png",
)


def _methods(analysis: Dict[str, Any]) -> List[str]:
    return list(analysis.get("method_order") or [])


def _values(analysis: Dict[str, Any], method: str, *path: str) -> Optional[float]:
    node: Any = analysis["methods"][method]
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node if isinstance(node, (int, float)) else None


def render_figures(analysis: Dict[str, Any], out_dir: str) -> List[str]:
    """Renders every figure into ``out_dir`` and returns the written paths."""
    plt = _pyplot()
    os.makedirs(out_dir, exist_ok=True)
    methods = _methods(analysis)
    written: List[str] = []

    def save(fig, filename: str) -> None:
        path = os.path.join(out_dir, filename)
        fig.tight_layout()
        fig.savefig(path, dpi=140)
        plt.close(fig)
        written.append(path)

    denominators = analysis["denominators"]
    evaluated = denominators.get("evaluated_attempts", 0)
    scheduled = denominators.get("scheduled_attempts", 0)

    # -- fig1: evaluator pass rate (denominator: evaluated attempts) -------
    fig, ax = plt.subplots(figsize=(8, 4.5))
    values = [
        _values(analysis, m, "test_pass", "rate_over_candidates") for m in methods
    ]
    ax.bar(
        methods,
        [100.0 * v if v is not None else 0.0 for v in values],
        color="#2563eb",
    )
    for index, value in enumerate(values):
        if value is None:
            ax.text(index, 1, "n/a", ha="center", va="bottom", fontsize=9)
    ax.set_ylim(0, 105)
    ax.set_ylabel("% of evaluated attempts")
    ax.set_title(
        f"Evaluator pass rate per method (denominator: {evaluated} evaluated attempts)"
    )
    ax.tick_params(axis="x", labelrotation=20)
    save(fig, "fig1_pass_rate_by_method.png")

    # -- fig1b: completion rate (denominator: scheduled attempts) ---------
    fig, ax = plt.subplots(figsize=(8, 4.5))
    values = [
        _values(analysis, m, "repair_success", "rate_over_scheduled") for m in methods
    ]
    ax.bar(
        methods,
        [100.0 * v if v is not None else 0.0 for v in values],
        color="#059669",
    )
    ax.set_ylim(0, 105)
    ax.set_ylabel("% of scheduled attempts")
    ax.set_title(
        f"Completion rate per method (denominator: {scheduled} scheduled attempts)"
    )
    ax.tick_params(axis="x", labelrotation=20)
    save(fig, "fig1b_completion_rate_by_method.png")

    # -- fig2: success vs failure -----------------------------------------
    fig, ax = plt.subplots(figsize=(8, 4.5))
    successes = [
        _values(analysis, m, "repair_success", "count") or 0 for m in methods
    ]
    totals = [
        _values(analysis, m, "denominators", "recorded_attempts") or 0 for m in methods
    ]
    failures = [max(0, t - s) for t, s in zip(totals, successes)]
    ax.bar(methods, successes, label="repair_success", color="#059669")
    ax.bar(methods, failures, bottom=successes, label="other outcomes", color="#dc2626")
    ax.set_ylabel("attempts")
    ax.set_title("Success vs other outcomes per method (denominator: recorded attempts)")
    ax.legend()
    ax.tick_params(axis="x", labelrotation=20)
    save(fig, "fig2_success_vs_failure_by_method.png")

    # -- fig3: latency ----------------------------------------------------
    fig, ax = plt.subplots(figsize=(8, 4.5))
    means = [_values(analysis, m, "latency_ms", "mean") for m in methods]
    medians = [_values(analysis, m, "latency_ms", "median") for m in methods]
    positions = list(range(len(methods)))
    ax.bar(
        [p - 0.2 for p in positions],
        [m or 0 for m in means],
        width=0.4,
        label="mean",
        color="#7c3aed",
    )
    ax.bar(
        [p + 0.2 for p in positions],
        [m or 0 for m in medians],
        width=0.4,
        label="median",
        color="#a78bfa",
    )
    ax.set_xticks(positions)
    ax.set_xticklabels(methods, rotation=20)
    ax.set_ylabel("milliseconds")
    ax.set_title("Attempt latency per method (measured attempts only)")
    ax.legend()
    save(fig, "fig3_latency_by_method.png")

    # -- fig4: token usage ------------------------------------------------
    fig, ax = plt.subplots(figsize=(8, 4.5))
    token_values = [
        _values(analysis, m, "token_usage", "mean_total_tokens") for m in methods
    ]
    reported = [
        _values(analysis, m, "token_usage", "provider_reported_attempts") or 0
        for m in methods
    ]
    ax.bar(
        methods,
        [t or 0 for t in token_values],
        color="#0891b2",
    )
    for index, value in enumerate(token_values):
        if value is None:
            ax.text(index, 1, "unavailable", ha="center", va="bottom", fontsize=9)
        else:
            ax.text(
                index,
                value,
                f"n={reported[index]}",
                ha="center",
                va="bottom",
                fontsize=8,
            )
    ax.set_ylabel("mean total tokens")
    ax.set_title("Provider-reported token usage per method (estimates excluded)")
    ax.tick_params(axis="x", labelrotation=20)
    save(fig, "fig4_token_usage_by_method.png")

    # -- fig5: task x method heatmap ---------------------------------------
    fig, ax = plt.subplots(figsize=(9, 5.5))
    matrix = []
    task_ids = sorted(analysis["methods"][methods[0]]["task_level"]["per_task_pass_rate"]) if methods else []
    for method in methods:
        rates = analysis["methods"][method]["task_level"]["per_task_pass_rate"]
        matrix.append([rates.get(task_id) for task_id in task_ids])
    if matrix:
        image = ax.imshow(matrix, aspect="auto", cmap="RdYlGn", vmin=0, vmax=1)
        ax.set_yticks(range(len(methods)))
        ax.set_yticklabels(methods)
        ax.set_xticks(range(len(task_ids)))
        ax.set_xticklabels(task_ids, rotation=90, fontsize=7)
        fig.colorbar(image, ax=ax, label="per-task pass rate")
    ax.set_title("Per-task pass rate by method (n/a = task not evaluated)")
    save(fig, "fig5_task_by_method_heatmap.png")

    # -- fig6: token distribution ------------------------------------------
    fig, ax = plt.subplots(figsize=(8, 4.5))
    distributions = []
    labels = []
    for method in methods:
        values = analysis["methods"][method].get("token_distribution") or []
        if values:
            distributions.append(values)
            labels.append(method)
    if distributions:
        ax.boxplot(distributions, tick_labels=labels)
    ax.set_ylabel("provider-reported total tokens")
    ax.set_title("Token distribution per method (provider-reported values only)")
    ax.tick_params(axis="x", labelrotation=20)
    save(fig, "fig6_token_distribution_by_method.png")

    # -- fig7: failure categories ------------------------------------------
    fig, ax = plt.subplots(figsize=(9, 5))
    categories: List[str] = []
    for method in methods:
        for category in analysis["methods"][method]["failure_categories"]:
            if category not in categories:
                categories.append(category)
    bottom = [0.0] * len(methods)
    for category in categories:
        values = [
            float(analysis["methods"][method]["failure_categories"].get(category, 0))
            for method in methods
        ]
        if sum(values) == 0:
            continue
        ax.bar(methods, values, bottom=bottom, label=category)
        bottom = [b + v for b, v in zip(bottom, values)]
    ax.set_ylabel("attempts")
    ax.set_title("Outcome categories per method (denominator: recorded attempts)")
    ax.legend(fontsize=8)
    ax.tick_params(axis="x", labelrotation=20)
    save(fig, "fig7_failure_categories_by_method.png")

    # -- fig8: latency decomposition ---------------------------------------
    fig, ax = plt.subplots(figsize=(8, 4.5))
    provider_means = [
        _values(analysis, m, "provider_latency_ms", "mean") for m in methods
    ]
    total_means = [_values(analysis, m, "latency_ms", "mean") for m in methods]
    positions = list(range(len(methods)))
    ax.bar(
        [p - 0.2 for p in positions],
        [v or 0 for v in provider_means],
        width=0.4,
        label="provider latency",
        color="#f59e0b",
    )
    ax.bar(
        [p + 0.2 for p in positions],
        [v or 0 for v in total_means],
        width=0.4,
        label="total attempt latency",
        color="#fbbf24",
    )
    ax.set_xticks(positions)
    ax.set_xticklabels(methods, rotation=20)
    ax.set_ylabel("milliseconds")
    ax.set_title("Latency decomposition per method (measured attempts only)")
    ax.legend()
    save(fig, "fig8_latency_decomposition_by_method.png")

    # -- fig9: truncation --------------------------------------------------
    fig, ax = plt.subplots(figsize=(8, 4.5))
    truncation = analysis["truncation"]["by_method"]
    ax.bar(methods, [truncation.get(m, 0) for m in methods], color="#dc2626")
    ax.set_ylabel("truncated attempts")
    ax.set_title(
        "Attempts truncated by the output-token cap "
        "(a harness-budget outcome, not a repair-quality result)"
    )
    ax.tick_params(axis="x", labelrotation=20)
    save(fig, "fig9_truncation_by_method.png")

    # -- fig10: cost availability ------------------------------------------
    fig, ax = plt.subplots(figsize=(8, 4.5))
    cost = analysis["cost"]
    if cost["available"]:
        totals = []
        for method in methods:
            totals.append(cost["total_cost_usd"] / max(1, len(methods)))
        ax.bar(methods, totals, color="#16a34a")
        ax.set_ylabel("USD (provider-reported)")
        ax.set_title("Provider-reported cost per method")
    else:
        ax.text(
            0.5,
            0.5,
            "Cost unavailable: the provider reported no cost for this run.\n"
            "This is NOT a zero-cost result and no estimate is substituted.",
            ha="center",
            va="center",
            fontsize=12,
            transform=ax.transAxes,
        )
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_title("Cost: unavailable (not reported by the provider)")
    save(fig, "fig10_cost_availability.png")

    return written
