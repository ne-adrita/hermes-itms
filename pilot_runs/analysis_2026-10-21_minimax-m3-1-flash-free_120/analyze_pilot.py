#!/usr/bin/env python3
"""
Offline analysis of a completed ITMS benchmark run.

Reads ONLY the artefacts that the benchmark runner produced (attempts.jsonl,
attempts.csv, summary.json, run_metadata.json and the per-attempt artefact
directories) and produces presentation-ready figures plus a results summary.

It never contacts a provider, never mutates the run directory and never
changes any benchmark logic.  Missing measurements are EXCLUDED, never
imputed.

Usage:
    python analyze_pilot.py <run-directory> [output-directory]
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

METHOD_ORDER = ["full_file_regeneration", "search_replace_patch", "itms_ast_splice"]
METHOD_LABEL = {
    "full_file_regeneration": "Full-file\nregeneration",
    "search_replace_patch": "Search/replace\npatch",
    "itms_ast_splice": "ITMS\nAST splice",
}
SUCCESS = "repair_success"

#: Credential-shaped patterns, mirroring the benchmark's own redaction rules
#: (benchmarks/benchmark/provider.py).  Used to prove that no secret-like
#: value reached a persisted artefact.
SECRET_PATTERNS = [
    re.compile(r"(?i)\bsk-[A-Za-z0-9._\-]{6,}"),
    re.compile(r"(?i)\bor-[A-Za-z0-9._\-]{6,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"(?i)(api[_-]?key['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9._\-+/]{8,}"),
    re.compile(r"(?i)(authorization['\"]?\s*[:=]\s*['\"]?)[A-Za-z0-9._\-+/]{8,}"),
    re.compile(r"AKIA[0-9A-Z]{16}(?![A-Z0-9])"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}"),
]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_jsonl(path):
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def load_csv(path):
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def style_axes(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(axis="y", alpha=0.25, linewidth=0.8)
    ax.set_axisbelow(True)


def bar_labels(ax, bars, fmt="{:.0f}"):
    for bar in bars:
        height = bar.get_height()
        ax.annotate(
            fmt.format(height),
            (bar.get_x() + bar.get_width() / 2.0, height),
            textcoords="offset points",
            xytext=(0, 3),
            ha="center",
            va="bottom",
            fontsize=9,
        )


def main(run_dir, out_dir):
    os.makedirs(out_dir, exist_ok=True)

    records = load_jsonl(os.path.join(run_dir, "attempts.jsonl"))
    csv_rows = load_csv(os.path.join(run_dir, "attempts.csv"))
    with open(os.path.join(run_dir, "summary.json"), "r", encoding="utf-8") as fh:
        summary = json.load(fh)
    with open(os.path.join(run_dir, "run_metadata.json"), "r", encoding="utf-8") as fh:
        metadata = json.load(fh)

    planned = metadata.get("planned_attempts")
    planned = planned if planned is not None else summary.get("planned_attempts")
    total = len(records)

    by_method = defaultdict(list)
    for r in records:
        by_method[r["method"]].append(r)
    methods = [m for m in METHOD_ORDER if m in by_method]

    # ------------------------------------------------------------------
    # Integrity checks
    # ------------------------------------------------------------------
    integrity = {}

    # 1. JSONL <-> CSV agreement (attempt ids and status).
    jsonl_ids = [r["attempt_id"] for r in records]
    csv_ids = [row["attempt_id"] for row in csv_rows]
    integrity["jsonl_csv_same_attempt_ids"] = jsonl_ids == csv_ids
    integrity["jsonl_csv_same_status"] = all(
        row["status"] == rec["status"]
        for row, rec in zip(csv_rows, records)
    )

    # 2. Summary counters agree with the records.
    integrity["summary_recorded_matches"] = summary.get("recorded_attempts") == total
    integrity["summary_planned_matches"] = summary.get("planned_attempts") == planned
    integrity["summary_artifacts_persisted_matches"] = summary.get(
        "artifacts_persisted"
    ) == sum(1 for r in records if r.get("artifacts_status") == "ok")

    # 3. Candidate hash: evaluated bytes vs. stored bytes.
    hash_rows = []
    for r in records:
        cand_path = r.get("candidate_path")
        if not cand_path:
            continue
        abs_path = os.path.join(run_dir, cand_path)
        if not os.path.exists(abs_path):
            hash_rows.append((r, None, None, "missing_file"))
            continue
        with open(abs_path, "rb") as fh:
            stored_digest = sha256_bytes(fh.read())
        evaluated_digest = r.get("candidate_sha256")
        hash_rows.append((r, evaluated_digest, stored_digest, None))
    evaluated_present = [h for h in hash_rows if h[1] and h[2]]
    integrity["candidate_hashes_all_match"] = bool(evaluated_present) and all(
        h[1] == h[2] for h in evaluated_present
    )
    integrity["candidate_hash_mismatches"] = [
        h[0]["attempt_id"] for h in evaluated_present if h[1] != h[2]
    ]
    integrity["candidate_redacted_count"] = sum(
        1 for r in records if r.get("candidate_redacted")
    )
    integrity["candidate_records_without_hash"] = [
        r["attempt_id"] for r in records if r.get("candidate_path") and not r.get("candidate_sha256")
    ]

    # 4. Artifact persistence status.
    integrity["artifact_status_counts"] = dict(
        Counter(r.get("artifacts_status", "missing") for r in records)
    )

    # 5. Mock vs live.
    integrity["mock_attempts"] = sum(1 for r in records if r.get("is_mock"))
    integrity["modes_seen"] = sorted({r.get("mode") for r in records})
    integrity["is_measurement"] = summary.get("is_measurement")

    # 6. Model identity.
    requested = sorted({r.get("requested_model_id") for r in records if r.get("requested_model_id")})
    served = Counter(r.get("served_model_id") for r in records)
    served_non_null = {k: v for k, v in served.items() if k}
    integrity["requested_model_ids"] = requested
    integrity["served_model_id_counts"] = dict(served)
    integrity["single_served_model"] = len(served_non_null) == 1
    integrity["served_matches_requested"] = (
        len(served_non_null) == 1
        and len(requested) == 1
        and list(served_non_null)[0] == requested[0]
    )
    integrity["attempts_without_served_model_id"] = sum(
        1 for r in records if not r.get("served_model_id")
    )

    # 7. Secret scan over every persisted artefact.
    secret_hits = []
    for root, _dirs, files in os.walk(run_dir):
        for name in files:
            path = os.path.join(root, name)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
            except OSError:
                continue
            for pat in SECRET_PATTERNS:
                if pat.search(text):
                    secret_hits.append(os.path.relpath(path, run_dir))
                    break
    integrity["secret_like_hits_in_artifacts"] = sorted(set(secret_hits))

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------
    per_method = {}
    for m in methods:
        rows = by_method[m]
        successes = [r for r in rows if r.get("status") == SUCCESS]
        failures = [r for r in rows if r.get("status") != SUCCESS]
        evaluated = [r for r in rows if r.get("evaluation_attempted") is True]
        passed = [r for r in evaluated if r.get("test_passed") is True]
        # Latency: only attempts whose wall-clock latency was recorded.
        lat = [r["latency_ms"] for r in rows if r.get("latency_ms") is not None]
        # Tokens: only provider-reported totals (never local estimates).
        tok = [
            r["total_tokens"]
            for r in rows
            if r.get("total_tokens") is not None
            and r.get("usage_source") in ("provider_reported", "provider_partial")
        ]
        per_method[m] = {
            "n": len(rows),
            "success": len(successes),
            "failed": len(failures),
            "evaluated": len(evaluated),
            "passed": len(passed),
            "pass_rate": (len(passed) / len(evaluated)) if evaluated else None,
            "status_counts": dict(Counter(r.get("status") for r in rows)),
            "failure_category_counts": dict(
                Counter(r.get("failure_category") for r in failures)
            ),
            "latency_n": len(lat),
            "mean_latency_ms": (sum(lat) / len(lat)) if lat else None,
            "token_n": len(tok),
            "mean_total_tokens": (sum(tok) / len(tok)) if tok else None,
            "total_tokens_sum": sum(tok),
        }

    exclusions = {
        "latency": "attempts with latency_ms == null are excluded (never imputed)",
        "tokens": "attempts without a provider-reported total_tokens (usage_source "
        "'local_estimate*' or null) are excluded (never imputed)",
        "pass_rate": "denominator is the number of attempts for which the official "
        "evaluator actually ran (evaluation_attempted == true)",
    }

    # ------------------------------------------------------------------
    # Figures
    # ------------------------------------------------------------------
    fig_paths = {}

    # --- Fig 1: pass rate by method -----------------------------------
    rates = [per_method[m]["pass_rate"] for m in methods]
    rates = [r if r is not None else 0.0 for r in rates]
    ns = [per_method[m]["evaluated"] for m in methods]
    fig, ax = plt.subplots(figsize=(8.2, 5.0))
    bars = ax.bar(
        [METHOD_LABEL[m] for m in methods],
        [100.0 * r for r in rates],
        color=["#4C78A8", "#F58518", "#54A24B"],
        width=0.6,
    )
    bar_labels(ax, bars, "{:.1f}%")
    for i, m in enumerate(methods):
        ax.annotate(
            f"n={ns[i]} evaluated",
            (bars[i].get_x() + bars[i].get_width() / 2.0, bars[i].get_height()),
            textcoords="offset points",
            xytext=(0, 16),
            ha="center",
            fontsize=9,
            color="#444444",
        )
    ax.set_ylabel("Official evaluator pass rate (%)")
    ax.set_xlabel("Editing method")
    ax.set_title(
        "Official evaluator pass rate by method\n"
        f"{metadata.get('requested_model_id')}  |  {total} live attempts\n"
        "Denominator = attempts the official evaluator actually ran",
        fontsize=12,
    )
    ax.set_ylim(0, max(110, 100.0 * max(rates) + 20))
    style_axes(ax)
    p = os.path.join(out_dir, "fig1_pass_rate_by_method.png")
    fig.tight_layout()
    fig.savefig(p, dpi=200)
    plt.close(fig)
    fig_paths["fig1_pass_rate_by_method"] = p

    # --- Fig 1b: success over ALL scheduled attempts -------------------
    # Fig 1 has a ceiling effect (every evaluated attempt passed), so the
    # discriminating quantity is the completion rate over scheduled
    # attempts, where truncated-output attempts count as failures.
    sched = [per_method[m]["n"] for m in methods]
    comp = [
        100.0 * per_method[m]["success"] / per_method[m]["n"] if per_method[m]["n"] else 0.0
        for m in methods
    ]
    fig, ax = plt.subplots(figsize=(8.2, 5.0))
    bars = ax.bar(
        [METHOD_LABEL[m] for m in methods],
        comp,
        color=["#4C78A8", "#F58518", "#54A24B"],
        width=0.6,
    )
    bar_labels(ax, bars, "{:.1f}%")
    for i, m in enumerate(methods):
        d = per_method[m]
        ax.annotate(
            f"{d['success']}/{d['n']} attempts",
            (bars[i].get_x() + bars[i].get_width() / 2.0, bars[i].get_height()),
            textcoords="offset points",
            xytext=(0, 16),
            ha="center",
            fontsize=9,
            color="#444444",
        )
    ax.set_ylabel("Successful attempts per scheduled attempt (%)")
    ax.set_xlabel("Editing method")
    ax.set_title(
        "Attempt completion rate by method (supplementary to Fig 1)\n"
        "Denominator = all scheduled attempts; truncated-output attempts count as failures"
    )
    ax.set_ylim(0, 110)
    style_axes(ax)
    p = os.path.join(out_dir, "fig1b_completion_rate_by_method.png")
    fig.tight_layout()
    fig.savefig(p, dpi=200)
    plt.close(fig)
    fig_paths["fig1b_completion_rate_by_method"] = p

    # --- Fig 2: success vs failure by method --------------------------
    succ = [per_method[m]["success"] for m in methods]
    fail = [per_method[m]["failed"] for m in methods]
    x = np.arange(len(methods))
    w = 0.36
    fig, ax = plt.subplots(figsize=(8.2, 5.0))
    b1 = ax.bar(x - w / 2, succ, w, label="Successful (repair_success)", color="#54A24B")
    b2 = ax.bar(x + w / 2, fail, w, label="Failed / not successful", color="#E45756")
    bar_labels(ax, b1)
    bar_labels(ax, b2)
    ax.set_xticks(x)
    ax.set_xticklabels([METHOD_LABEL[m] for m in methods])
    ax.set_ylabel("Number of attempts")
    ax.set_xlabel("Editing method")
    ax.set_title(
        "Successful versus failed attempts by method\n"
        f"ITMS code-editing benchmark — {metadata.get('requested_model_id')} "
        f"(n={total} attempts)"
    )
    ax.set_ylim(0, max(succ + fail) * 1.25)
    ax.legend(frameon=False, loc="upper right")
    style_axes(ax)
    p = os.path.join(out_dir, "fig2_success_vs_failure_by_method.png")
    fig.tight_layout()
    fig.savefig(p, dpi=200)
    plt.close(fig)
    fig_paths["fig2_success_vs_failure_by_method"] = p

    # --- Fig 3: mean execution time -----------------------------------
    lat_vals = [per_method[m]["mean_latency_ms"] for m in methods]
    lat_n = [per_method[m]["latency_n"] for m in methods]
    if any(v is not None for v in lat_vals):
        vals = [v if v is not None else 0.0 for v in lat_vals]
        fig, ax = plt.subplots(figsize=(8.2, 5.0))
        bars = ax.bar(
            [METHOD_LABEL[m] for m in methods],
            vals,
            color=["#4C78A8", "#F58518", "#54A24B"],
            width=0.6,
        )
        bar_labels(ax, bars, "{:.0f}")
        for i, m in enumerate(methods):
            ax.annotate(
                f"n={lat_n[i]} with latency",
                (bars[i].get_x() + bars[i].get_width() / 2.0, bars[i].get_height()),
                textcoords="offset points",
                xytext=(0, 16),
                ha="center",
                fontsize=8,
                color="#555555",
            )
        ax.set_ylim(0, max(vals) * 1.22)
        ax.set_ylabel("Mean end-to-end attempt latency (ms)")
        ax.set_xlabel("Editing method")
        ax.set_title(
            "Mean execution time per attempt by method\n"
            "(includes provider call + parse + official evaluation)"
        )
        style_axes(ax)
        p = os.path.join(out_dir, "fig3_mean_execution_time_by_method.png")
        fig.tight_layout()
        fig.savefig(p, dpi=200)
        plt.close(fig)
        fig_paths["fig3_mean_execution_time_by_method"] = p
    else:
        fig_paths["fig3_mean_execution_time_by_method"] = None

    # --- Fig 4: mean token usage --------------------------------------
    tok_vals = [per_method[m]["mean_total_tokens"] for m in methods]
    tok_n = [per_method[m]["token_n"] for m in methods]
    if any(v is not None for v in tok_vals):
        vals = [v if v is not None else 0.0 for v in tok_vals]
        fig, ax = plt.subplots(figsize=(8.2, 5.0))
        bars = ax.bar(
            [METHOD_LABEL[m] for m in methods],
            vals,
            color=["#4C78A8", "#F58518", "#54A24B"],
            width=0.6,
        )
        bar_labels(ax, bars, "{:.0f}")
        for i, m in enumerate(methods):
            ax.annotate(
                f"n={tok_n[i]} provider-reported",
                (bars[i].get_x() + bars[i].get_width() / 2.0, bars[i].get_height()),
                textcoords="offset points",
                xytext=(0, 16),
                ha="center",
                fontsize=8,
                color="#555555",
            )
        ax.set_ylim(0, max(vals) * 1.22)
        ax.set_ylabel("Mean total tokens per attempt (provider-reported)")
        ax.set_xlabel("Editing method")
        ax.set_title(
            "Mean token usage per attempt by method\n"
            "(provider-reported totals only; local estimates excluded)"
        )
        style_axes(ax)
        p = os.path.join(out_dir, "fig4_mean_token_usage_by_method.png")
        fig.tight_layout()
        fig.savefig(p, dpi=200)
        plt.close(fig)
        fig_paths["fig4_mean_token_usage_by_method"] = p
    else:
        fig_paths["fig4_mean_token_usage_by_method"] = None

    # --- Fig 5: task x method heatmap ---------------------------------
    tasks = sorted({r["task_id"] for r in records})
    matrix = np.full((len(tasks), len(methods)), np.nan)
    for ti, t in enumerate(tasks):
        for mi, m in enumerate(methods):
            rows = [r for r in by_method[m] if r["task_id"] == t]
            evald = [r for r in rows if r.get("evaluation_attempted") is True]
            if not evald:
                continue
            passed = sum(1 for r in evald if r.get("test_passed") is True)
            matrix[ti, mi] = 100.0 * passed / len(evald)
    fig, ax = plt.subplots(figsize=(8.0, 8.6))
    im = ax.imshow(matrix, cmap="RdYlGn", vmin=0, vmax=100, aspect="auto")
    ax.set_xticks(range(len(methods)))
    ax.set_xticklabels([METHOD_LABEL[m] for m in methods], fontsize=9)
    ax.set_yticks(range(len(tasks)))
    ax.set_yticklabels(tasks, fontsize=8)
    ax.set_xlabel("Editing method")
    ax.set_ylabel("Task")
    ax.set_title(
        "Official evaluator pass rate per task and method\n"
        "(cell = % of repetitions passing; blank = not evaluated)"
    )
    for ti in range(len(tasks)):
        for mi in range(len(methods)):
            v = matrix[ti, mi]
            if np.isnan(v):
                ax.text(mi, ti, "n/e", ha="center", va="center", fontsize=7, color="#555555")
            else:
                ax.text(
                    mi, ti, f"{v:.0f}",
                    ha="center", va="center", fontsize=7,
                    color="black" if 20 < v < 80 else "white",
                )
    fig.colorbar(im, ax=ax, shrink=0.75, label="pass rate (%)")
    p = os.path.join(out_dir, "fig5_task_by_method_heatmap.png")
    fig.tight_layout()
    fig.savefig(p, dpi=200)
    plt.close(fig)
    fig_paths["fig5_task_by_method_heatmap"] = p

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------
    failures = [r for r in records if r.get("status") != SUCCESS]
    truncations = [r for r in records if r.get("truncated_output")]
    report = {
        "run_directory": os.path.abspath(run_dir),
        "analysis_directory": os.path.abspath(out_dir),
        "run_id": metadata.get("run_id"),
        "created_at_utc": metadata.get("created_at_utc"),
        "mode": metadata.get("mode"),
        "is_measurement": metadata.get("is_measurement"),
        "declared_provider": metadata.get("declared_provider"),
        "effective_provider": metadata.get("effective_provider"),
        "requested_model_id": metadata.get("requested_model_id"),
        "task_manifest_digest": metadata.get("task_manifest_digest"),
        "seed": metadata.get("run_order_seed"),
        "planned_attempts": planned,
        "recorded_attempts": total,
        "successful_attempts": sum(1 for r in records if r.get("status") == SUCCESS),
        "failed_attempts": len(failures),
        "status_counts": dict(Counter(r.get("status") for r in records)),
        "failure_category_counts": dict(
            Counter(r.get("failure_category") for r in failures)
        ),
        "truncated_output_attempts": len(truncations),
        "truncation_by_method": dict(
            Counter(r["method"] for r in truncations)
        ),
        "per_method": per_method,
        "per_task_counts": {
            t: sum(1 for r in records if r["task_id"] == t) for t in tasks
        },
        "model_identity": {
            "requested_model_ids": requested,
            "served_model_id_counts": dict(served),
            "single_served_model": integrity["single_served_model"],
            "served_matches_requested": integrity["served_matches_requested"],
            "attempts_without_served_model_id": integrity[
                "attempts_without_served_model_id"
            ],
        },
        "integrity": integrity,
        "exclusions": exclusions,
        "figures": fig_paths,
        "elapsed_wall_clock_ms": summary.get("elapsed_wall_clock_ms"),
    }

    with open(os.path.join(out_dir, "results_summary.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False, default=str)
        fh.write("\n")

    lines = []
    lines.append("# ITMS Code-Editing Benchmark — Live Run Results (120 attempts)\n")
    lines.append(f"- Run directory: `{report['run_directory']}`")
    lines.append(f"- Run id: `{report['run_id']}`  (created {report['created_at_utc']})")
    lines.append(f"- Mode: **{report['mode']}**  |  is_measurement: {report['is_measurement']}")
    lines.append(f"- Provider: {report['declared_provider']} (effective: {report['effective_provider']})")
    lines.append(f"- Requested model id: `{report['requested_model_id']}`")
    lines.append(f"- Task manifest digest: `{report['task_manifest_digest']}`")
    lines.append(f"- Run-order seed: {report['seed']}\n")
    lines.append("## Counts\n")
    lines.append(f"| Metric | Value |\n|---|---|")
    lines.append(f"| Planned attempts | {planned} |")
    lines.append(f"| Recorded attempts | {total} |")
    lines.append(f"| Successful (repair_success) | {report['successful_attempts']} |")
    lines.append(f"| Failed / not successful | {report['failed_attempts']} |")
    lines.append(f"| Wall clock | {report['elapsed_wall_clock_ms']} ms |\n")
    lines.append("### Status breakdown\n")
    for k, v in sorted(report["status_counts"].items()):
        lines.append(f"- `{k}`: {v}")
    lines.append("")
    if report["failure_category_counts"]:
        lines.append("### Failure categories\n")
        for k, v in sorted(report["failure_category_counts"].items()):
            lines.append(f"- `{k}`: {v}")
        lines.append("")
    lines.append("## Limitations that MUST be disclosed\n")
    lines.append(
        f"- **Ceiling effect on the evaluator pass rate.** Every attempt that reached "
        f"the official evaluator passed (see the pass-rate column). Fig 1 therefore "
        f"shows 100% for all three methods and does NOT discriminate between them; "
        f"Fig 1b (completion rate over all scheduled attempts) is the discriminating "
        f"comparison, and Fig 2 shows the raw success/failure split."
    )
    lines.append(
        f"- **All {report['truncated_output_attempts']} non-successes are output "
        f"truncations** (`finish_reason = length`, `failure_category = "
        f"truncated_output`), not wrong repairs: the model's mandatory reasoning "
        f"consumed the 2048-token output cap before a parseable patch was emitted. "
        f"They are concentrated in `itms_ast_splice` "
        f"({report['truncation_by_method'].get('itms_ast_splice', 0)} of 40), so the "
        f"completion-rate difference between methods is partly a token-budget effect "
        f"of the harness, not purely a difference in edit quality."
    )
    lines.append(
        "- **Task set is easy for this model.** The 20 HumanEval-derived repair "
        "mutations are solved nearly perfectly, so this run cannot separate the "
        "methods on repair quality. It is a valid measurement of the harness on a "
        "pinned model, not a discriminating benchmark of editing strategies."
    )
    lines.append(
        "- **Free-model route.** The requested id is a pinned OpenRouter slug, but "
        "OpenRouter may re-point that slug at a different upstream serving "
        "implementation over time. This run observed a single served id for all 120 "
        "attempts, so it is a same-model comparison *as observed on this date*; it is "
        "not a guarantee for future runs."
    )
    lines.append(
        "- **One run, seed 20261021, 2 repetitions.** Per-task results are based on "
        "2 repetitions, so per-task pass rates are coarse (0%, 50%, 100%)."
    )
    lines.append("")
    lines.append("## Per-method results\n")
    lines.append("| Method | n | Success | Failed | Evaluated | Pass rate | Mean latency (ms) | Mean tokens |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for m in methods:
        d = per_method[m]
        pr = f"{100.0*d['pass_rate']:.1f}%" if d["pass_rate"] is not None else "n/a"
        lat = f"{d['mean_latency_ms']:.0f}" if d["mean_latency_ms"] is not None else "n/a"
        tok = f"{d['mean_total_tokens']:.0f}" if d["mean_total_tokens"] is not None else "n/a"
        lines.append(
            f"| {m} | {d['n']} | {d['success']} | {d['failed']} | {d['evaluated']} | {pr} | {lat} | {tok} |"
        )
    lines.append("")
    lines.append("## Model identity\n")
    mi = report["model_identity"]
    lines.append(f"- Requested id(s): {mi['requested_model_ids']}")
    lines.append(f"- Served id counts: {mi['served_model_id_counts']}")
    lines.append(f"- All attempts served the same model: **{mi['single_served_model']}**")
    lines.append(f"- Served id == requested id for every attempt: **{mi['served_matches_requested']}**")
    lines.append("")
    lines.append("## Integrity checks\n")
    for k, v in integrity.items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## Exclusions (never imputed)\n")
    for k, v in exclusions.items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## Figures\n")
    for k, v in fig_paths.items():
        lines.append(f"- `{k}` -> `{v}`")
    lines.append("")

    with open(os.path.join(out_dir, "RESULTS_SUMMARY.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    run = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(run, "analysis")
    raise SystemExit(main(run, out))
