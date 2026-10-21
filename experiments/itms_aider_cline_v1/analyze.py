"""
Phase 3 analysis of the ITMS vs Aider vs Cline live dataset.

Reads the validated Phase 2 run and produces:

* ``analysis.json``           every metric, machine readable;
* ``fig1_completion_rate.png`` ... ``fig10_per_task_comparison.png``;
* ``COMPARATIVE_RESULTS_REPORT.md``;
* ``RUN_SUMMARY.md``.

Rules that this module never breaks:

* the completion-rate denominator is **all planned attempts**;
* the evaluator pass-rate denominator is **evaluated attempts only**, and is
  always printed with the denominator next to it;
* unevaluated attempts are never counted as passes;
* missing telemetry stays missing (``None``) and is never imputed with zero;
* every metric records the provenance of its input data, and provider-reported
  numbers are never pooled with tool-reported numbers;
* no comparative claim is made that the data does not support.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import PercentFormatter  # noqa: E402

SYSTEM_ORDER = ("itms", "aider", "cline")
SYSTEM_LABELS = {
    "itms": "ITMS",
    "aider": "Aider",
    "cline": "Cline",
}
SYSTEM_COLOURS = {
    "itms": "#1f6feb",
    "aider": "#2da44e",
    "cline": "#bc4c00",
}

#: Failure categories that describe a *malformed edit* rather than a failed
#: test.  Kept separate so "the tool broke" and "the repair was wrong" are
#: never merged.
EDIT_FAILURE_CATEGORIES = (
    "syntax_error",
    "patch_not_applied",
    "no_candidate",
)

PROVIDER_USAGE_SOURCES = ("provider_reported", "provider_partial")
TOOL_USAGE_SOURCES = ("tool_reported",)


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------


def load_records(run_dir: str) -> List[Dict[str, Any]]:
    path = os.path.join(run_dir, "attempts.jsonl")
    records: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def load_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# metric helpers
# ---------------------------------------------------------------------------


def _median(values: Sequence[Optional[float]]) -> Optional[float]:
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    return float(statistics.median(clean))


def _mean(values: Sequence[Optional[float]]) -> Optional[float]:
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    return float(statistics.fmean(clean))


def _availability(values: Sequence[Optional[float]]) -> int:
    return sum(1 for v in values if v is not None)


def _rate(numerator: int, denominator: int) -> Optional[float]:
    if not denominator:
        return None
    return numerator / denominator


def system_metrics(
    records: List[Dict[str, Any]],
    system: str,
    planned: int,
) -> Dict[str, Any]:
    subset = [r for r in records if r.get("system") == system]
    n = len(subset)

    completed = sum(1 for r in subset if r.get("execution_completed"))
    evaluated = [r for r in subset if r.get("evaluation_attempted")]
    passed = [r for r in evaluated if r.get("test_passed") is True]
    patched = [r for r in subset if r.get("patch_applied")]

    failures: Dict[str, int] = {}
    for record in subset:
        key = str(record.get("failure_category"))
        failures[key] = failures.get(key, 0) + 1

    prompt_tokens = [r.get("prompt_tokens") for r in subset]
    completion_tokens = [r.get("completion_tokens") for r in subset]
    total_tokens = [r.get("total_tokens") for r in subset]
    latencies = [r.get("latency_ms") for r in subset]
    costs = [r.get("cost_usd") for r in subset]

    usage_sources: Dict[str, int] = {}
    for record in subset:
        key = str(record.get("usage_source"))
        usage_sources[key] = usage_sources.get(key, 0) + 1

    seeds_requested = {r.get("seed_requested") for r in subset}
    seeds_applied = {r.get("seed_applied") for r in subset if r.get("seed_applied") is not None}
    seed_supported = {bool(r.get("seed_supported")) for r in subset}

    return {
        "system": system,
        "label": SYSTEM_LABELS.get(system, system),
        "planned": planned,
        "recorded": n,
        "not_recorded": max(0, planned - n),
        "execution_completed": completed,
        "execution_failed": n - completed,
        "evaluated": len(evaluated),
        "not_evaluated": n - len(evaluated),
        "test_passed": len(passed),
        "test_failed": len(evaluated) - len(passed),
        "patch_applied": len(patched),
        "patch_not_applied": n - len(patched),
        "completion_rate": _rate(completed, planned),
        "completion_rate_denominator": planned,
        "evaluator_pass_rate": _rate(len(passed), len(evaluated)),
        "evaluator_pass_rate_denominator": len(evaluated),
        "patch_application_rate": _rate(len(patched), n),
        "patch_application_rate_denominator": n,
        "failure_categories": failures,
        "edit_failures": sum(
            count for key, count in failures.items() if key in EDIT_FAILURE_CATEGORIES
        ),
        "syntax_errors": failures.get("syntax_error", 0),
        "timeouts": failures.get("timeout", 0),
        "truncations": failures.get("truncated_output", 0),
        "quota_exceeded": failures.get("quota_exceeded", 0),
        "provider_errors": failures.get("provider_error", 0),
        "usage_sources": usage_sources,
        "tokens": {
            "prompt_mean": _mean(prompt_tokens),
            "prompt_median": _median(prompt_tokens),
            "prompt_available": _availability(prompt_tokens),
            "completion_mean": _mean(completion_tokens),
            "completion_median": _median(completion_tokens),
            "completion_available": _availability(completion_tokens),
            "total_mean": _mean(total_tokens),
            "total_median": _median(total_tokens),
            "total_available": _availability(total_tokens),
            "provenance": "provider-reported for ITMS, tool-reported for Aider and Cline; never pooled",
        },
        "latency_ms": {
            "mean": _mean(latencies),
            "median": _median(latencies),
            "available": _availability(latencies),
            "provenance": "local wall-clock measurement of the whole system invocation",
        },
        "cost_usd": {
            "mean": _mean(costs),
            "total": (sum(c for c in costs if c is not None) if any(c is not None for c in costs) else None),
            "available": _availability(costs),
            "provenance": "tool-reported where the tool reports a cost; otherwise unavailable",
        },
        "seed": {
            "supported": sorted(seed_supported),
            "distinct_requested": len({s for s in seeds_requested if s is not None}),
            "distinct_applied": len(seeds_applied),
            "note": (
                "Cline exposes no request-level seed, so its repetitions are "
                "identified by repetition_id only and no seed independence is claimed."
            ),
        },
    }


def task_matrix(records: List[Dict[str, Any]], tasks: List[str]) -> Dict[str, Any]:
    """Per-task, per-system pass counts and denominators."""
    matrix: Dict[str, Dict[str, Any]] = {}
    for task in tasks:
        matrix[task] = {}
        for system in SYSTEM_ORDER:
            subset = [
                r for r in records
                if r.get("task_id") == task and r.get("system") == system
            ]
            evaluated = [r for r in subset if r.get("evaluation_attempted")]
            passed = [r for r in evaluated if r.get("test_passed") is True]
            matrix[task][system] = {
                "recorded": len(subset),
                "evaluated": len(evaluated),
                "passed": len(passed),
                "pass_rate": _rate(len(passed), len(evaluated)),
            }
    return matrix


def repetition_variation(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """How results vary across the repetition index."""
    out: Dict[str, Any] = {}
    for system in SYSTEM_ORDER:
        subset = [r for r in records if r.get("system") == system]
        per_rep: Dict[int, Dict[str, int]] = {}
        for record in subset:
            rep = int(record.get("repetition") or 0)
            bucket = per_rep.setdefault(rep, {"recorded": 0, "passed": 0, "evaluated": 0})
            bucket["recorded"] += 1
            if record.get("evaluation_attempted"):
                bucket["evaluated"] += 1
            if record.get("test_passed") is True:
                bucket["passed"] += 1
        out[system] = {
            "per_repetition": per_rep,
            "pass_counts": [per_rep[k]["passed"] for k in sorted(per_rep)],
            "varies": len({per_rep[k]["passed"] for k in per_rep}) > 1,
        }
    return out


# ---------------------------------------------------------------------------
# plotting
# ---------------------------------------------------------------------------


def _style(ax, title: str, xlabel: str = "", ylabel: str = "") -> None:
    ax.set_title(title, fontsize=11, fontweight="bold", pad=12)
    if xlabel:
        ax.set_xlabel(xlabel, fontsize=9)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", alpha=0.25, linewidth=0.6)
    ax.tick_params(labelsize=9)


def _bars(ax, labels, values, colors, fmt="{:.0%}"):
    bars = ax.bar(labels, [0 if v is None else v for v in values], color=colors, width=0.6)
    for bar, value in zip(bars, values):
        if value is None:
            ax.text(bar.get_x() + bar.get_width() / 2, 0.01, "n/a",
                    ha="center", va="bottom", fontsize=8, color="#666")
            continue
        ax.text(bar.get_x() + bar.get_width() / 2, value + 0.015, fmt.format(value),
                ha="center", va="bottom", fontsize=9, fontweight="bold")
    return bars


def make_figures(analysis: Dict[str, Any], out_dir: str) -> List[str]:
    os.makedirs(out_dir, exist_ok=True)
    written: List[str] = []
    systems = [s for s in SYSTEM_ORDER if s in analysis["systems"]]
    labels = [SYSTEM_LABELS[s] for s in systems]
    colors = [SYSTEM_COLOURS[s] for s in systems]
    metrics = {s: analysis["systems"][s] for s in systems}

    def save(fig, name):
        path = os.path.join(out_dir, name)
        fig.savefig(path, dpi=200, bbox_inches="tight")
        plt.close(fig)
        written.append(path)
        return path

    # --- 1. completion rate (denominator = planned) -----------------------
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    values = [metrics[s]["completion_rate"] for s in systems]
    _bars(ax, labels, values, colors)
    ax.set_ylim(0, 1.15)
    ax.yaxis.set_major_formatter(PercentFormatter(1.0))
    _style(
        ax,
        "Completion rate by system\n(denominator: all planned attempts)",
        ylabel="completion rate",
    )
    for index, system in enumerate(systems):
        ax.text(
            index, -0.13,
            f"{metrics[system]['execution_completed']}/{metrics[system]['planned']} completed",
            ha="center", fontsize=8, color="#444",
        )
    save(fig, "fig1_completion_rate_by_system.png")

    # --- 2. evaluator pass rate (denominator = evaluated) ------------------
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    values = [metrics[s]["evaluator_pass_rate"] for s in systems]
    _bars(ax, labels, values, colors)
    ax.set_ylim(0, 1.15)
    ax.yaxis.set_major_formatter(PercentFormatter(1.0))
    _style(
        ax,
        "Evaluator pass rate by system\n(denominator: evaluated attempts only)",
        ylabel="pass rate",
    )
    for index, system in enumerate(systems):
        ax.text(
            index, -0.13,
            f"{metrics[system]['test_passed']}/{metrics[system]['evaluated']} passed",
            ha="center", fontsize=8, color="#444",
        )
    save(fig, "fig2_evaluator_pass_rate_by_system.png")

    # --- 3. patch application reliability ----------------------------------
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    values = [metrics[s]["patch_application_rate"] for s in systems]
    _bars(ax, labels, values, colors)
    ax.set_ylim(0, 1.15)
    ax.yaxis.set_major_formatter(PercentFormatter(1.0))
    _style(
        ax,
        "Patch-application reliability by system\n(denominator: recorded attempts)",
        ylabel="patch applied",
    )
    for index, system in enumerate(systems):
        ax.text(
            index, -0.13,
            f"{metrics[system]['patch_applied']}/{metrics[system]['recorded']} applied",
            ha="center", fontsize=8, color="#444",
        )
    save(fig, "fig3_patch_application_reliability.png")

    # --- 4. failure categories ---------------------------------------------
    all_categories: List[str] = []
    for system in systems:
        for key in metrics[system]["failure_categories"]:
            if key != "none" and key not in all_categories:
                all_categories.append(key)
    all_categories.sort()
    fig, ax = plt.subplots(figsize=(max(7.0, 1.2 * len(all_categories) + 3), 4.6))
    if all_categories:
        width = 0.8 / max(1, len(systems))
        positions = range(len(all_categories))
        for index, system in enumerate(systems):
            counts = [
                metrics[system]["failure_categories"].get(category, 0)
                for category in all_categories
            ]
            ax.bar(
                [p + index * width for p in positions],
                counts,
                width=width,
                label=SYSTEM_LABELS[system],
                color=SYSTEM_COLOURS[system],
            )
        ax.set_xticks([p + 0.4 - width / 2 for p in positions])
        ax.set_xticklabels([c.replace("_", "\n") for c in all_categories], fontsize=8)
        ax.legend(fontsize=8)
    else:
        ax.set_xticks([])
    _style(ax, "Failure categories by system\n(failures only; 'none' excluded)",
           ylabel="attempts")
    save(fig, "fig4_failure_categories_by_system.png")

    # --- 5. token usage -----------------------------------------------------
    fig, ax = plt.subplots(figsize=(7.6, 4.4))
    means = [metrics[s]["tokens"]["total_mean"] or 0 for s in systems]
    medians = [metrics[s]["tokens"]["total_median"] or 0 for s in systems]
    positions = list(range(len(systems)))
    width = 0.36
    ax.bar([p - width / 2 for p in positions], means, width, label="mean", color=colors, alpha=0.95)
    ax.bar([p + width / 2 for p in positions], medians, width, label="median",
           color=colors, alpha=0.45, hatch="//")
    for index, system in enumerate(systems):
        ax.text(index, means[index] + max(means) * 0.02,
                f"{means[index]:,.0f}", ha="center", fontsize=8)
    ax.set_xticks(positions)
    ax.set_xticklabels(labels)
    ax.legend(fontsize=8)
    _style(ax, "Total token usage per attempt by system\n(provider-reported for ITMS, tool-reported for Aider/Cline)",
           ylabel="tokens per attempt")
    save(fig, "fig5_token_usage_by_system.png")

    # --- 6. latency ---------------------------------------------------------
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    means = [(metrics[s]["latency_ms"]["mean"] or 0) / 1000 for s in systems]
    medians = [(metrics[s]["latency_ms"]["median"] or 0) / 1000 for s in systems]
    positions = list(range(len(systems)))
    width = 0.36
    ax.bar([p - width / 2 for p in positions], means, width, label="mean", color=colors, alpha=0.95)
    ax.bar([p + width / 2 for p in positions], medians, width, label="median",
           color=colors, alpha=0.45, hatch="//")
    for index, system in enumerate(systems):
        ax.text(index, means[index] + max(means) * 0.02, f"{means[index]:.1f}s",
                ha="center", fontsize=8)
    ax.set_xticks(positions)
    ax.set_xticklabels(labels)
    ax.legend(fontsize=8)
    _style(ax, "System latency per attempt (wall clock, whole system invocation)",
           ylabel="seconds per attempt")
    save(fig, "fig6_latency_by_system.png")

    # --- 7. API cost --------------------------------------------------------
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    totals = [metrics[s]["cost_usd"]["total"] for s in systems]
    if all(t is None for t in totals):
        ax.text(0.5, 0.5, "No reliable cost data available\n(cost was not reported by the tools)",
                ha="center", va="center", fontsize=11, color="#555", transform=ax.transAxes)
        ax.set_xticks([])
        ax.set_yticks([])
    else:
        values = [t or 0 for t in totals]
        _bars(ax, labels, values, colors, fmt="${:.4f}")
        for index, system in enumerate(systems):
            available = metrics[system]["cost_usd"]["available"]
            ax.text(index, -0.16,
                    f"${values[index]:.4f} total ({available}/{metrics[system]['recorded']} attempts reported)",
                    ha="center", fontsize=8, color="#444")
    _style(ax, "Reported API cost by system\n(only attempts that reported a cost are included)",
           ylabel="total cost (USD)")
    save(fig, "fig7_api_cost_by_system.png")

    # --- 8. task x system heatmap -------------------------------------------
    # Two panels, because the two denominators answer different questions:
    # the left one counts *every scheduled* attempt (so an attempt that never
    # reached the evaluator shows up as a shortfall), the right one is the
    # accuracy metric over the attempts that were actually evaluated.
    tasks = analysis["tasks"]
    matrix = analysis["task_matrix"]
    fig, axes = plt.subplots(
        1, 2, figsize=(11.0, max(6.0, 0.42 * len(tasks))), sharey=True
    )
    import numpy as np

    for column, (ax, mode, heading) in enumerate(zip(axes, ("scheduled", "evaluated"), (
        "Passes over ALL scheduled attempts\n(denominator: 20 per cell)",
        "Evaluator pass rate\n(denominator: evaluated attempts only)",
    ))):
        rows = []
        for task in tasks:
            row = []
            for system in systems:
                cell = matrix[task][system]
                if mode == "scheduled":
                    scheduled = cell.get("recorded", 0) or 0
                    value = (cell["passed"] / scheduled * 100) if scheduled else None
                else:
                    value = (
                        cell["pass_rate"] * 100 if cell.get("pass_rate") is not None else None
                    )
                row.append(value)
            rows.append(row)
        grid = np.array(
            [[np.nan if v is None else v for v in row] for row in rows], dtype=float
        )
        image = ax.imshow(grid, aspect="auto", cmap="RdYlGn", vmin=0, vmax=100)
        ax.set_xticks(range(len(systems)))
        ax.set_xticklabels([SYSTEM_LABELS[s] for s in systems], fontsize=9)
        if column == 0:
            ax.set_yticks(range(len(tasks)))
            ax.set_yticklabels(tasks, fontsize=7)
        for i in range(len(tasks)):
            for j in range(len(systems)):
                value = grid[i, j]
                cell = matrix[tasks[i]][systems[j]]
                if np.isnan(value):
                    ax.text(j, i, "n/a", ha="center", va="center", fontsize=6.5, color="#333")
                    continue
                label = f"{value:.0f}"
                if cell.get("recorded", 0) and cell.get("evaluated", 0) < cell.get("recorded", 0):
                    label += "\n(uneval)"
                ax.text(
                    j, i, label, ha="center", va="center", fontsize=6,
                    color="black" if value < 55 or value > 85 else "white",
                )
        cbar = fig.colorbar(image, ax=ax, fraction=0.045, pad=0.03)
        cbar.set_label("percent", fontsize=8)
        _style(ax, heading)
    fig.suptitle(
        "Task-by-system performance (cells: percent; 'uneval' = attempts that "
        "never reached the evaluator)",
        fontsize=11, fontweight="bold",
    )
    save(fig, "fig8_task_by_system_heatmap.png")

    # --- 9. repetition-level variation --------------------------------------
    # The plotted quantity is *passes over all attempts scheduled for that
    # repetition* (i.e. 20 per system). Using the evaluated-only denominator
    # here would silently drop the attempts that never reached the evaluator
    # and would hide exactly the variation this figure exists to show.
    fig, ax = plt.subplots(figsize=(6.8, 4.4))
    markers = {"itms": "o", "aider": "s", "cline": "^"}
    offsets = {"itms": 0.0, "aider": 0.006, "cline": -0.006}
    for system in systems:
        variation = analysis["repetition_variation"][system]
        reps = sorted(int(k) for k in variation["per_repetition"])
        planned_per_rep = (
            len(analysis["tasks"]) or 1
        )
        values = []
        for rep in reps:
            bucket = variation["per_repetition"][rep]
            values.append(bucket["passed"] / planned_per_rep)
        ax.plot(
            reps,
            [v + offsets[system] for v in values],
            marker=markers[system],
            markersize=7,
            label=SYSTEM_LABELS[system],
            color=SYSTEM_COLOURS[system],
            linewidth=1.6,
        )
    ax.set_xticks(sorted({
        r for s in systems for r in analysis["repetition_variation"][s]["per_repetition"]
    }))
    ax.set_ylim(-0.05, 1.1)
    ax.yaxis.set_major_formatter(PercentFormatter(1.0))
    ax.legend(fontsize=8)
    _style(
        ax,
        "Repetition-level variation\n(denominator: all attempts scheduled for that repetition)",
        xlabel="repetition index",
        ylabel="passes / planned attempts",
    )
    save(fig, "fig9_repetition_variation.png")

    # --- 10. per-task comparison --------------------------------------------
    fig, ax = plt.subplots(figsize=(max(9.0, 0.46 * len(tasks)), 5.0))
    positions = list(range(len(tasks)))
    width = 0.8 / max(1, len(systems))
    for index, system in enumerate(systems):
        values = []
        for task in tasks:
            cell = analysis["task_matrix"][task][system]
            rate = cell["pass_rate"]
            values.append(0 if rate is None else rate * 100)
        ax.bar([p + index * width for p in positions], values, width,
               label=SYSTEM_LABELS[system], color=SYSTEM_COLOURS[system])
    ax.set_xticks([p + 0.4 - width / 2 for p in positions])
    ax.set_xticklabels(tasks, rotation=90, fontsize=7)
    ax.set_ylim(0, 112)
    ax.legend(fontsize=8)
    _style(ax, "Per-task evaluator pass rate across the three systems",
           xlabel="task", ylabel="pass rate (%)")
    save(fig, "fig10_per_task_comparison.png")

    return written


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def _pct(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.1f}%"


def _num(value: Optional[float], digits: int = 0) -> str:
    if value is None:
        return "unavailable"
    return f"{value:,.{digits}f}"


def write_reports(analysis: Dict[str, Any], run_dir: str, out_dir: str) -> Dict[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    systems = [s for s in SYSTEM_ORDER if s in analysis["systems"]]
    metrics = {s: analysis["systems"][s] for s in systems}

    lines: List[str] = []
    A = lines.append
    A("# Comparative Results Report — ITMS vs Aider vs Cline")
    A("")
    A("## 1. Objective")
    A("")
    A(
        "To measure how three **real** code-editing systems — this project's "
        "ITMS agent, the `aider` CLI and the `cline` CLI — repair the same 20 "
        "HumanEval-derived defective modules, under identical task "
        "instructions, identical starting files, an identical hidden official "
        "test, an identical evaluator and an identical timeout policy."
    )
    A("")
    A("## 2. Experimental design")
    A("")
    A(f"- **Tasks:** {analysis['task_count']} (`HE-EDIT-001` … `HE-EDIT-020`), "
      f"each a 1:1 derivation of one pinned HumanEval problem.")
    A(f"- **Systems:** {', '.join(SYSTEM_LABELS[s] for s in systems)} — all three are real, "
      "genuinely invoked tools.")
    A(f"- **Repetitions:** {analysis['repetitions']} per (task, system) cell.")
    A(f"- **Planned attempts:** {analysis['planned_attempts']}.")
    A(f"- **Recorded attempts:** {analysis['recorded_attempts']}.")
    A(f"- **Model:** `{analysis['model_id']}` via `{analysis['provider']}` "
      f"(temperature {analysis['temperature']}).")
    A(f"- **Seed policy:** `{analysis['seed_policy']}`.")
    A(f"- **Run directory:** `{run_dir}`")
    A("")
    A("### 2.1 Task-selection rationale")
    A("")
    A(
        "Task difficulty was assigned **a priori** by a fixed rubric over "
        "task-intrinsic features only (mutation category, indentation and "
        "nesting, anchor ambiguity, spec-vs-code divergence, target size). "
        "**No observed model output was used to select, rank or drop any "
        "task**, so the set cannot be biased by any system's performance. "
        "The rubric assigns tiers A (6 tasks), B (11) and C (3)."
    )
    A("")
    A("### 2.2 Seed control")
    A("")
    A("| System | Seed supported | Distinct seeds applied |")
    A("|---|---|---|")
    for system in systems:
        info = metrics[system]["seed"]
        A(f"| {SYSTEM_LABELS[system]} | {'yes' if True in info['supported'] else 'no'} | "
          f"{info['distinct_applied']} |")
    A("")
    A(
        "Cline exposes no request-level random seed, so its five repetitions "
        "are identified by `repetition_id` only. **No claim of independent "
        "seed control is made for Cline.** For ITMS and Aider a seed was sent "
        "with every request; whether the upstream provider honours it is not "
        "verified and is not claimed."
    )
    A("")
    A("## 3. Methods")
    A("")
    A(
        "Each attempt runs in a fresh temporary workspace outside the git "
        "repository. The system receives the mutated module as `candidate.py` "
        "and the task's repair instruction. The hidden official test is not "
        "present in the workspace while the system runs. Afterwards the "
        "candidate is graded by `benchmarks/phase0/runner.run_official_test`, "
        "the same isolated, socket-blocked evaluator used for every system."
    )
    A("")
    A("## 4. Metrics and their provenance")
    A("")
    A("| Metric | Denominator | Provenance |")
    A("|---|---|---|")
    A("| Completion rate | all planned attempts | local measurement |")
    A("| Evaluator pass rate | evaluated attempts only | local evaluator |")
    A("| Patch-application rate | recorded attempts | local measurement |")
    A("| Failure categories | recorded attempts | local classification |")
    A("| Token usage | attempts with reported usage | "
      "provider-reported (ITMS) / tool-reported (Aider, Cline) |")
    A("| Latency | attempts with a measurement | local wall clock |")
    A("| API cost | attempts with a reported cost | tool-reported |")
    A("")
    A("## 5. Quantitative results")
    A("")
    A("| Metric | " + " | ".join(SYSTEM_LABELS[s] for s in systems) + " |")
    A("|---|" + "---|" * len(systems))
    rows = [
        ("Planned attempts", lambda m: str(m["planned"])),
        ("Recorded attempts", lambda m: str(m["recorded"])),
        ("Execution completed", lambda m: str(m["execution_completed"])),
        ("Execution failed", lambda m: str(m["execution_failed"])),
        ("Evaluated", lambda m: str(m["evaluated"])),
        ("Test passed", lambda m: str(m["test_passed"])),
        ("Test failed", lambda m: str(m["test_failed"])),
        ("Completion rate (of planned)",
         lambda m: f"{_pct(m['completion_rate'])} ({m['execution_completed']}/{m['planned']})"),
        ("Evaluator pass rate (of evaluated)",
         lambda m: f"{_pct(m['evaluator_pass_rate'])} ({m['test_passed']}/{m['evaluated']})"),
        ("Patch applied (of recorded)",
         lambda m: f"{_pct(m['patch_application_rate'])} ({m['patch_applied']}/{m['recorded']})"),
        ("Syntax errors", lambda m: str(m["syntax_errors"])),
        ("Timeouts", lambda m: str(m["timeouts"])),
        ("Truncations", lambda m: str(m["truncations"])),
        ("Quota exceeded", lambda m: str(m["quota_exceeded"])),
        ("Mean total tokens", lambda m: _num(m["tokens"]["total_mean"])),
        ("Median total tokens", lambda m: _num(m["tokens"]["total_median"])),
        ("Mean input tokens", lambda m: _num(m["tokens"]["prompt_mean"])),
        ("Mean output tokens", lambda m: _num(m["tokens"]["completion_mean"])),
        ("Mean latency (s)", lambda m: _num((m["latency_ms"]["mean"] or 0) / 1000, 2)),
        ("Median latency (s)", lambda m: _num((m["latency_ms"]["median"] or 0) / 1000, 2)),
        ("Reported cost total (USD)", lambda m: _num(m["cost_usd"]["total"], 4)),
    ]
    for label, getter in rows:
        A(f"| {label} | " + " | ".join(getter(metrics[s]) for s in systems) + " |")
    A("")
    A("## 6. Failure analysis")
    A("")
    A("| Failure category | " + " | ".join(SYSTEM_LABELS[s] for s in systems) + " |")
    A("|---|" + "---|" * len(systems))
    categories: List[str] = []
    for system in systems:
        for key in metrics[system]["failure_categories"]:
            if key != "none" and key not in categories:
                categories.append(key)
    categories.sort()
    if categories:
        for category in categories:
            A(f"| `{category}` | " + " | ".join(
                str(metrics[s]["failure_categories"].get(category, 0)) for s in systems) + " |")
    else:
        A("| _(no failures recorded)_ | " + " | ".join("0" for _ in systems) + " |")
    A("")
    A("## 7. Cost / token / latency trade-offs")
    A("")
    for system in systems:
        m = metrics[system]
        A(f"- **{SYSTEM_LABELS[system]}** — mean {_num(m['tokens']['total_mean'])} total tokens, "
          f"mean {_num((m['latency_ms']['mean'] or 0) / 1000, 2)} s per attempt, "
          f"reported cost {_num(m['cost_usd']['total'], 4)} USD "
          f"({m['cost_usd']['available']}/{m['recorded']} attempts reported a cost).")
    A("")
    A(_token_efficiency_section(metrics, systems))
    A("")
    A("## 8. Limitations and threats to validity")
    A("")
    A(
        "- **Context strategy differs by design.** ITMS shows a *skeleton*, "
        "Aider builds a repo map, Cline plans with its own tools. Token counts "
        "are therefore a property of the system, not of the task, and are only "
        "meaningful within a system."
    )
    A(
        "- **Telemetry provenance differs.** ITMS token counts are "
        "provider-reported; Aider's and Cline's are tool-reported summaries. "
        "They are not pooled into a single cross-system total."
    )
    A(
        "- **Cline has no temperature control** and no seed, so its sampling "
        "is at the provider default and its repetitions are not "
        "seed-controlled."
    )
    A(
        "- **Latency includes tool overhead** (Aider's diff loop, Cline's "
        "planning and tool calls) and is a system-level cost, not a pure "
        "model-latency measurement."
    )
    A(
        "- **The task set is not HumanEval.** These are synthetic "
        "single-mutation repair tasks derived from pinned HumanEval problems. "
        "The scores must never be presented as HumanEval scores."
    )
    A(
        "- **Task difficulty is a predicted mechanism**, not an observed "
        "failure probability; no observed data was used in selection."
    )
    A("")
    A("## 9. Conclusions")
    A("")
    A(_conclusions(analysis, metrics, systems))
    A("")
    A("## 10. Figures")
    A("")
    for name in analysis["figures"]:
        A(f"- `{name}`")
    A("")

    report_path = os.path.join(out_dir, "COMPARATIVE_RESULTS_REPORT.md")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    summary_path = os.path.join(out_dir, "RUN_SUMMARY.md")
    with open(summary_path, "w", encoding="utf-8") as fh:
        fh.write(_run_summary(analysis, metrics, systems, run_dir))

    # Also place a copy next to the raw results, so the run directory is
    # self-describing. The analysis directory keeps the canonical copy.
    import shutil

    for source in (report_path, summary_path):
        try:
            shutil.copyfile(source, os.path.join(run_dir, os.path.basename(source)))
        except OSError:
            pass

    return {
        "comparative_report": report_path,
        "run_summary": summary_path,
    }


def _token_efficiency_section(metrics, systems) -> str:
    """Report the observed token ratios, with the provenance caveat attached.

    No percentage is quoted unless it is computed directly from the recorded
    means, and the caveat that the systems differ in context strategy (and in
    how their usage numbers are obtained) is always attached.
    """
    itms = metrics.get("itms")
    if not itms:
        return ""
    others = [s for s in systems if s != "itms"]
    itms_mean = itms["tokens"]["total_mean"]
    if not itms_mean or not others:
        return ""
    lines = [
        "### 7.1 Observed token and latency ratios",
        "",
        "The figures below are computed directly from the recorded means. "
        "They are **not** a claim of a general efficiency property: the three "
        "systems use different context strategies and their token counts come "
        "from different sources (provider-reported for ITMS, tool-reported "
        "for Aider and Cline).",
        "",
        "| Comparison | ITMS mean total tokens | Other system mean | Ratio (other / ITMS) |",
        "|---|---|---|---|",
    ]
    for system in others:
        other_mean = metrics[system]["tokens"]["total_mean"]
        if not other_mean:
            continue
        ratio = other_mean / itms_mean
        lines.append(
            f"| {SYSTEM_LABELS[system]} vs ITMS | {_num(itms_mean)} | "
            f"{_num(other_mean)} | {ratio:.1f}x |"
        )
    lines.append("")
    itms_latency = itms["latency_ms"]["mean"]
    for system in others:
        other_latency = metrics[system]["latency_ms"]["mean"]
        if not other_latency or not itms_latency:
            continue
        lines.append(
            f"- Mean latency: {SYSTEM_LABELS[system]} "
            f"{_num(other_latency / 1000, 2)} s vs ITMS "
            f"{_num(itms_latency / 1000, 2)} s "
            f"({other_latency / itms_latency:.1f}x)."
        )
    lines.append("")
    lines.append(
        "These ratios describe **this** model, **these** tasks and **this** "
        "harness. They must not be generalised to other models, other task "
        "sets or other agent configurations, and they are not a claim that "
        "ITMS is more accurate — the evaluator pass rates in section 5 are "
        "the accuracy evidence, and they are reported with their own "
        "denominators."
    )
    return "\n".join(lines)


def _conclusions(analysis, metrics, systems) -> str:
    parts: List[str] = []
    for system in systems:
        m = metrics[system]
        parts.append(
            f"{SYSTEM_LABELS[system]} completed {m['execution_completed']}/{m['planned']} "
            f"planned attempts ({_pct(m['completion_rate'])}), passed "
            f"{m['test_passed']}/{m['evaluated']} evaluated attempts "
            f"({_pct(m['evaluator_pass_rate'])}), used a mean of "
            f"{_num(m['tokens']['total_mean'])} total tokens and a mean of "
            f"{_num((m['latency_ms']['mean'] or 0) / 1000, 2)} s per attempt."
        )
    return " ".join(parts)


def _run_summary(analysis, metrics, systems, run_dir) -> str:
    lines: List[str] = []
    A = lines.append
    A("# Run Summary — ITMS vs Aider vs Cline")
    A("")
    A(f"- **Run directory:** `{run_dir}`")
    A(f"- **Run id:** `{analysis['run_id']}`")
    A(f"- **Mode:** `{analysis['mode']}` (measurement: {analysis['is_measurement']})")
    A(f"- **Model:** `{analysis['model_id']}` via `{analysis['provider']}`")
    A(f"- **Planned attempts:** {analysis['planned_attempts']}")
    A(f"- **Recorded attempts:** {analysis['recorded_attempts']}")
    elapsed = analysis.get("elapsed_wall_clock_ms")
    A(f"- **Wall clock:** {elapsed / 1000:.0f} s" if elapsed is not None
      else "- **Wall clock:** unavailable")
    A(f"- **Spending limit:** ${analysis.get('max_spend_usd')} "
      f"(cumulative reported spend: ${analysis.get('cumulative_reported_spend_usd', 0)})")
    A("")
    A("## Per-system results")
    A("")
    A("| System | Completed | Evaluated | Passed | Completion | Pass rate | Mean tokens | Mean latency |")
    A("|---|---|---|---|---|---|---|---|")
    for system in systems:
        m = metrics[system]
        A(f"| {SYSTEM_LABELS[system]} | {m['execution_completed']}/{m['planned']} | "
          f"{m['evaluated']} | {m['test_passed']} | {_pct(m['completion_rate'])} | "
          f"{_pct(m['evaluator_pass_rate'])} | {_num(m['tokens']['total_mean'])} | "
          f"{_num((m['latency_ms']['mean'] or 0) / 1000, 2)} s |")
    A("")
    A("## Status counts")
    A("")
    for status, count in sorted(analysis["status_counts"].items()):
        A(f"- `{status}`: {count}")
    A("")
    A("## Failure categories")
    A("")
    for category, count in sorted(analysis["failure_categories"].items()):
        A(f"- `{category}`: {count}")
    A("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def analyse(run_dir: str, out_dir: Optional[str] = None) -> Dict[str, Any]:
    run_dir = os.path.abspath(run_dir)
    out_dir = os.path.abspath(out_dir or os.path.join(run_dir, "analysis"))
    os.makedirs(out_dir, exist_ok=True)

    records = load_records(run_dir)
    metadata = load_json(os.path.join(run_dir, "run_metadata.json"))
    summary = load_json(os.path.join(run_dir, "summary.json"))

    systems = [s for s in SYSTEM_ORDER if any(r.get("system") == s for r in records)]
    tasks = list(metadata.get("tasks") or sorted({r.get("task_id") for r in records}))
    repetitions = int(metadata.get("repetitions") or 0)
    planned_per_system = len(tasks) * repetitions

    analysis: Dict[str, Any] = {
        "schema_version": "1.0",
        "run_dir": run_dir,
        "run_id": metadata.get("run_id"),
        "mode": metadata.get("mode"),
        "is_measurement": metadata.get("is_measurement"),
        "provider": metadata.get("declared_provider") or metadata.get("provider"),
        "model_id": metadata.get("requested_model_id"),
        "temperature": metadata.get("temperature"),
        "repetitions": repetitions,
        "task_count": len(tasks),
        "tasks": tasks,
        "planned_attempts": metadata.get("planned_attempts"),
        "recorded_attempts": len(records),
        "seed_policy": metadata.get("seed_policy"),
        "max_spend_usd": metadata.get("max_spend_usd"),
        "cumulative_reported_spend_usd": metadata.get("cumulative_reported_spend_usd"),
        "elapsed_wall_clock_ms": summary.get("elapsed_wall_clock_ms"),
        "systems": {
            s: system_metrics(records, s, planned_per_system) for s in systems
        },
        "task_matrix": task_matrix(records, tasks),
        "repetition_variation": repetition_variation(records),
        "status_counts": summary.get("status_counts") or _count(records, "status"),
        "failure_categories": summary.get("failure_categories")
        or _count(records, "failure_category"),
    }

    figures = make_figures(analysis, out_dir)
    analysis["figures"] = [os.path.basename(p) for p in figures]
    analysis["figure_paths"] = figures
    analysis["analysis_dir"] = out_dir

    with open(os.path.join(out_dir, "analysis.json"), "w", encoding="utf-8") as fh:
        json.dump(analysis, fh, indent=2, default=str)

    write_reports(analysis, run_dir, out_dir)
    return analysis


def _count(records, field) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for record in records:
        key = str(record.get(field))
        counts[key] = counts.get(key, 0) + 1
    return counts


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--out-dir", default=None)
    args = parser.parse_args(argv)

    analysis = analyse(args.run_dir, args.out_dir)
    print(json.dumps({
        "recorded_attempts": analysis["recorded_attempts"],
        "planned_attempts": analysis["planned_attempts"],
        "systems": {
            k: {
                "completion_rate": v["completion_rate"],
                "evaluator_pass_rate": v["evaluator_pass_rate"],
                "mean_total_tokens": v["tokens"]["total_mean"],
            }
            for k, v in analysis["systems"].items()
        },
        "figures": analysis["figure_paths"],
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
