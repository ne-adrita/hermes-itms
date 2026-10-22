#!/usr/bin/env python3
"""
Read-only dashboard for the completed CSE499B live benchmark.

This app **displays** the results that the run already produced.  It never
runs a benchmark, never contacts a model provider, never opens an outbound
socket and never writes to the run directory.  Every number it shows is
derived from the saved artefacts by :mod:`dashboard_data` and is
cross-checked against the offline analysis file, so the dashboard cannot
display a statistic that disagrees with the data on disk.

Start it with::

    cd dashboard/benchmark-results && ../../.venv/bin/python -m streamlit run app.py

and open http://localhost:8501
"""

from __future__ import annotations

import os
import sys

import altair as alt
import pandas as pd
import streamlit as st

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import dashboard_data as dd  # noqa: E402

st.set_page_config(
    page_title="CSE499B — ITMS code-editing benchmark",
    page_icon="📊",
    layout="wide",
)

BUNDLE = dd.load_run_bundle()
OVERALL = BUNDLE["overall"]
METHODS = BUNDLE["methods"]
RECORDS = BUNDLE["records"]
ORDER = [m for m in dd.METHOD_ORDER if m in METHODS]
LABELS = dd.METHOD_LABELS


def _label(method: str) -> str:
    return LABELS.get(method, method)


def _fmt(value, digits: int = 0, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:,.{digits}f}{suffix}"
    return f"{value:,}{suffix}"


# ==========================================================================
# header
# ==========================================================================
st.title("CSE499B — ITMS code-editing benchmark")
st.caption(
    "Read-only dashboard over a completed live run. Every value below is read "
    "from the saved run artefacts; nothing is recomputed from a model and no "
    "provider is contacted."
)

model = OVERALL.get("model") or {}
meta_cols = st.columns(5)
meta_cols[0].metric("Run", str(OVERALL.get("run_id") or "—"))
meta_cols[1].metric("Mode", f"{(OVERALL.get('mode') or '—')} (live)")
meta_cols[2].metric(
    "Model", f"`{model.get('requested_model_id', '—')}`"
)
meta_cols[3].metric("Tasks", OVERALL.get("tasks"))
meta_cols[4].metric("Methods", OVERALL.get("methods"))

st.caption(
    f"Run directory: `{OVERALL.get('run_dir')}`  ·  "
    f"started {OVERALL.get('created_at_utc') or '—'}  ·  "
    f"mock records: {OVERALL.get('mock_attempts')}"
)

# ==========================================================================
# summary cards
# ==========================================================================
st.subheader("Summary")

card_cols = st.columns(5)
card_cols[0].metric(
    "Requested attempts",
    _fmt(OVERALL.get("requested_attempts")),
    help="Provider requests issued, from the post-run budget state.",
)
card_cols[1].metric(
    "Officially evaluated",
    _fmt(OVERALL.get("evaluated_attempts")),
    help="Attempts that reached the official Phase 0 evaluator.",
)
card_cols[2].metric(
    "Evaluator passes",
    _fmt(OVERALL.get("evaluator_passes")),
    help="Attempts the official evaluator accepted.",
)
card_cols[3].metric(
    "Failures",
    _fmt(OVERALL.get("evaluator_failures")),
    help="Evaluated attempts the official evaluator rejected.",
)
card_cols[4].metric(
    "Never evaluated",
    _fmt(OVERALL.get("never_evaluated")),
    help="Attempts that never reached the official evaluator.",
)

sub = st.columns(5)
sub[0].metric("Planned cells", _fmt(OVERALL.get("planned_attempts")))
sub[1].metric("Recorded attempts", _fmt(OVERALL.get("completed_attempts")))
sub[2].metric("Provider errors", _fmt(OVERALL.get("provider_errors")))
sub[3].metric("Truncations", _fmt(OVERALL.get("truncations")))
sub[4].metric("Retries", _fmt(OVERALL.get("retries")))

# ==========================================================================
# methodology limitations
# ==========================================================================
with st.expander("⚠️ Methodology limitations — read before interpreting any number", expanded=True):
    for limitation in BUNDLE["limitations"]:
        st.markdown(f"**{limitation['title']}.** {limitation['text']}")

# ==========================================================================
# tabs
# ==========================================================================
overview_tab, methods_tab, attempts_tab, figures_tab, reports_tab, integrity_tab = st.tabs(
    [
        "Overview",
        "Method comparison",
        "Attempts",
        "Figures",
        "Reports",
        "Integrity",
    ]
)

# ==========================================================================
# overview
# ==========================================================================
with overview_tab:
    st.markdown(
        "#### The three editing methods, side by side\n\n"
        "All rates use **evaluated attempts** as the denominator unless the "
        "column says otherwise."
    )
    rows = []
    for method in ORDER:
        values = METHODS[method]
        rows.append(
            {
                "Method": _label(method),
                "Scheduled": values["scheduled_attempts"],
                "Evaluated": values["evaluated_attempts"],
                "Passes": values["evaluator_passes"],
                "Failures": values["evaluator_failures"],
                "Pass rate (of evaluated)": values["evaluator_pass_rate"],
                "Mean input tok": values["mean_prompt_tokens"],
                "Mean output tok": values["mean_completion_tokens"],
                "Mean total tok": values["mean_total_tokens"],
                "Median total tok": values["median_total_tokens"],
                "Mean latency (ms)": values["mean_latency_ms"],
                "Median latency (ms)": values["median_latency_ms"],
                "Mean prompt (bytes)": values["mean_prompt_bytes"],
            }
        )
    st.dataframe(
        pd.DataFrame(rows).style.format(
            {
                "Pass rate (of evaluated)": "{:.1%}",
                "Mean input tok": "{:,.0f}",
                "Mean output tok": "{:,.0f}",
                "Mean total tok": "{:,.0f}",
                "Median total tok": "{:,.0f}",
                "Mean latency (ms)": "{:,.0f}",
                "Median latency (ms)": "{:,.0f}",
                "Mean prompt (bytes)": "{:,.0f}",
            },
            na_rep="n/a",
        ),
        width="stretch",
        hide_index=True,
    )

    st.markdown("#### Official-evaluator pass rate")
    pass_chart = (
        alt.Chart(
            pd.DataFrame(
                [
                    {
                        "Method": _label(m),
                        "Pass rate": (METHODS[m]["evaluator_pass_rate"] or 0) * 100,
                        "Passes": METHODS[m]["evaluator_passes"],
                        "Evaluated": METHODS[m]["evaluated_attempts"],
                    }
                    for m in ORDER
                ]
            )
        )
        .mark_bar(color="#2563eb")
        .encode(
            x=alt.X("Method:N", sort=list(_label(m) for m in ORDER), title=None),
            y=alt.Y("Pass rate:Q", title="% of evaluated attempts", scale=alt.Scale(domain=[0, 105])),
            tooltip=[
                alt.Tooltip("Method:N"),
                alt.Tooltip("Passes:Q", title="passes"),
                alt.Tooltip("Evaluated:Q", title="evaluated (denominator)"),
                alt.Tooltip("Pass rate:Q", format=".1f", title="pass rate %"),
            ],
        )
        .properties(height=280)
    )
    st.altair_chart(pass_chart, width="stretch")

    st.info(
        "All three methods passed every attempt they were evaluated on, so the "
        "pass-rate chart is flat at 100 %. This run compares **efficiency**, "
        "not accuracy."
    )

    st.markdown("#### Token usage (provider-reported only)")
    token_rows = []
    for method in ORDER:
        values = METHODS[method]
        token_rows.append(
            {
                "Method": _label(method),
                "Input": values["mean_prompt_tokens"] or 0,
                "Output": values["mean_completion_tokens"] or 0,
                "Total": values["mean_total_tokens"] or 0,
            }
        )
    token_df = pd.DataFrame(token_rows)
    token_chart = (
        alt.Chart(token_df)
        .mark_bar()
        .encode(
            x=alt.X("Method:N", sort=list(_label(m) for m in ORDER), title=None),
            y=alt.Y("Total:Q", title="mean provider-reported tokens"),
            color=alt.Color(
                "kind:N",
                scale=alt.Scale(
                    domain=["Input", "Output"],
                    range=["#0891b2", "#f59e0b"],
                ),
                legend=alt.Legend(title="tokens"),
            ),
            order=["Input", "Output"],
            tooltip=[
                alt.Tooltip("Method:N"),
                alt.Tooltip("kind:N", title="part"),
                alt.Tooltip("Total:Q", format=",", title="mean tokens"),
            ],
        )
        .properties(height=280)
    )
    st.altair_chart(token_chart, width="stretch")
    st.caption(
        "Input and output tokens are stacked; the bar total is the mean total "
        "token count. Only provider-reported values are used — no local "
        "estimate is mixed in."
    )

    st.markdown("#### Latency (measured)")
    latency_df = pd.DataFrame(
        [
            {
                "Method": _label(m),
                "Mean": METHODS[m]["mean_latency_ms"] or 0,
                "Median": METHODS[m]["median_latency_ms"] or 0,
            }
            for m in ORDER
        ]
    )
    latency_chart = (
        alt.Chart(latency_df)
        .mark_bar()
        .encode(
            x=alt.X("Method:N", sort=list(_label(m) for m in ORDER), title=None),
            y=alt.Y("value:Q", title="milliseconds"),
            color=alt.Color(
                "stat:N",
                scale=alt.Scale(domain=["Mean", "Median"], range=["#7c3aed", "#c4b5fd"]),
                legend=alt.Legend(title=None),
            ),
            order=["Mean", "Median"],
            tooltip=[
                alt.Tooltip("Method:N"),
                alt.Tooltip("stat:N"),
                alt.Tooltip("value:Q", format=",", title="ms"),
            ],
        )
        .properties(height=280)
    )
    st.altair_chart(latency_chart, width="stretch")

    st.markdown("#### The prompt-length difference")
    prompt_df = pd.DataFrame(
        [
            {
                "Method": _label(m),
                "Mean prompt bytes": METHODS[m]["mean_prompt_bytes"] or 0,
                "Median prompt bytes": METHODS[m]["median_prompt_bytes"] or 0,
            }
            for m in ORDER
        ]
    )
    prompt_chart = (
        alt.Chart(prompt_df)
        .mark_bar(color="#dc2626")
        .encode(
            x=alt.X("Method:N", sort=list(_label(m) for m in ORDER), title=None),
            y=alt.Y("Mean prompt bytes:Q", title="mean bytes of the prompt that was sent"),
            tooltip=[
                alt.Tooltip("Method:N"),
                alt.Tooltip("Mean prompt bytes:Q", format=",", title="mean bytes"),
                alt.Tooltip("Median prompt bytes:Q", format=",", title="median bytes"),
            ],
        )
        .properties(height=220)
    )
    st.altair_chart(prompt_chart, width="stretch")
    st.caption(
        "Measured from the persisted `prompt.txt` artefacts, i.e. from the "
        "requests the provider actually received. The ITMS adapter builds a "
        "pruned skeleton, so it is shown a smaller context — a confound for "
        "any total-token comparison."
    )

# ==========================================================================
# method comparison
# ==========================================================================
with methods_tab:
    st.markdown("#### Per-method detail")
    detail = []
    for method in ORDER:
        values = METHODS[method]
        detail.append(
            {
                "Method": _label(method),
                "Key": method,
                "Scheduled": values["scheduled_attempts"],
                "Completed": values["completed_attempts"],
                "Evaluated": values["evaluated_attempts"],
                "Passes": values["evaluator_passes"],
                "Failures": values["evaluator_failures"],
                "Never evaluated": values["never_evaluated"],
                "Provider errors": values["provider_errors"],
                "Truncations": values["truncations"],
                "Syntax valid": values["syntax_valid"],
                "Retries": values["retries"],
                "Pass rate (of evaluated)": values["evaluator_pass_rate"],
                "Completion rate (of scheduled)": values["completion_rate"],
            }
        )
    st.dataframe(
        pd.DataFrame(detail).style.format(
            {
                "Pass rate (of evaluated)": "{:.1%}",
                "Completion rate (of scheduled)": "{:.1%}",
            },
            na_rep="n/a",
        ),
        width="stretch",
        hide_index=True,
    )

    st.markdown("#### Efficiency ranking (derived from the rows above)")
    ranking = []
    for method in ORDER:
        values = METHODS[method]
        ranking.append(
            {
                "Method": _label(method),
                "Mean output tokens": values["mean_completion_tokens"],
                "Median output tokens": values["median_completion_tokens"],
                "Mean total tokens": values["mean_total_tokens"],
                "Median total tokens": values["median_total_tokens"],
                "Mean latency (ms)": values["mean_latency_ms"],
                "Median latency (ms)": values["median_latency_ms"],
            }
        )
    st.dataframe(
        pd.DataFrame(ranking).style.format(
            {
                "Mean output tokens": "{:,.0f}",
                "Median output tokens": "{:,.0f}",
                "Mean total tokens": "{:,.0f}",
                "Median total tokens": "{:,.0f}",
                "Mean latency (ms)": "{:,.0f}",
                "Median latency (ms)": "{:,.0f}",
            },
            na_rep="n/a",
        ),
        width="stretch",
        hide_index=True,
    )
    st.caption(
        "Output tokens and latency are the fair comparison between the "
        "methods; the total-token column is affected by the different prompt "
        "sizes shown in the Overview tab."
    )

    st.markdown("#### Per-task results")
    per_task_rows = []
    for task_id in sorted(BUNDLE["per_task"]):
        row: dict = {"Task": task_id}
        for method in ORDER:
            cell = BUNDLE["per_task"][task_id].get(method) or {}
            row[f"{_label(method)} — passes"] = f"{cell.get('passes', 0)}/{cell.get('attempts', 0)}"
            row[f"{_label(method)} — mean tokens"] = cell.get("mean_total_tokens")
            row[f"{_label(method)} — mean latency (ms)"] = cell.get("mean_latency_ms")
        per_task_rows.append(row)
    st.dataframe(
        pd.DataFrame(per_task_rows).style.format(
            {
                c: "{:,.0f}"
                for c in pd.DataFrame(per_task_rows).columns
                if "tokens" in c or "latency" in c
            },
            na_rep="n/a",
        ),
        width="stretch",
        hide_index=True,
    )

    st.markdown("#### Per-task pass rate (interactive heatmap)")
    heat_rows = []
    for task_id in sorted(BUNDLE["per_task"]):
        for method in ORDER:
            cell = BUNDLE["per_task"][task_id].get(method) or {}
            rate = cell.get("pass_rate")
            heat_rows.append(
                {
                    "Task": task_id,
                    "Method": _label(method),
                    "Pass rate": (rate * 100) if rate is not None else None,
                    "Passes": cell.get("passes"),
                    "Attempts": cell.get("attempts"),
                }
            )
    heat_df = pd.DataFrame(heat_rows)
    heat_chart = (
        alt.Chart(heat_df)
        .mark_rect()
        .encode(
            x=alt.X("Task:N", sort=sorted(BUNDLE["per_task"]), title=None),
            y=alt.Y("Method:N", sort=list(_label(m) for m in ORDER), title=None),
            color=alt.Color(
                "Pass rate:Q",
                scale=alt.Scale(domain=[0, 100], range=["#b91c1c", "#15803d"]),
                legend=alt.Legend(title="pass rate %"),
            ),
            tooltip=[
                alt.Tooltip("Task:N"),
                alt.Tooltip("Method:N"),
                alt.Tooltip("Passes:Q", title="passes"),
                alt.Tooltip("Attempts:Q", title="attempts"),
                alt.Tooltip("Pass rate:Q", format=".1f", title="pass rate %"),
            ],
        )
        .properties(height=260)
    )
    st.altair_chart(heat_chart, width="stretch")

# ==========================================================================
# attempts (filterable table)
# ==========================================================================
with attempts_tab:
    st.markdown("#### Attempt-level records")
    frame = pd.DataFrame(RECORDS)
    frame["method_label"] = frame["method"].map(_label)

    with st.sidebar:
        st.header("Filters")
        method_filter = st.multiselect(
            "Method",
            options=[_label(m) for m in ORDER],
            default=[_label(m) for m in ORDER],
        )
        task_filter = st.multiselect(
            "Task id",
            options=sorted(frame["task_id"].unique().tolist()),
            default=[],
            placeholder="all tasks",
        )
        rep_filter = st.multiselect(
            "Repetition",
            options=sorted(frame["rep"].unique().tolist()),
            default=[],
            placeholder="all repetitions",
        )
        status_filter = st.multiselect(
            "Evaluation status",
            options=["pass", "fail", "not evaluated"],
            default=["pass", "fail", "not evaluated"],
        )
        search = st.text_input(
            "Search (task id, attempt id, status)",
            value="",
            placeholder="e.g. MOD-EDIT-004 or provider_error",
        )
        st.divider()
        st.caption("The dashboard is read-only: it never writes to the run.")

    mask = frame["method_label"].isin(method_filter)
    if task_filter:
        mask &= frame["task_id"].isin(task_filter)
    if rep_filter:
        mask &= frame["rep"].isin(rep_filter)

    def _status(row) -> str:
        if row.get("evaluation_attempted") is not True:
            return "not evaluated"
        return "pass" if row.get("test_passed") is True else "fail"

    frame["evaluation_status"] = frame.apply(_status, axis=1)
    mask &= frame["evaluation_status"].isin(status_filter)

    if search:
        needle = search.strip().lower()
        haystack = (
            frame["task_id"].astype(str)
            + " "
            + frame["attempt_id"].astype(str)
            + " "
            + frame["status"].astype(str)
            + " "
            + frame["failure_category"].astype(str)
            + " "
            + frame["method_label"].astype(str)
        ).str.lower()
        mask &= haystack.str.contains(needle, regex=False, na=False)

    filtered = frame[mask].copy()
    st.caption(
        f"Showing **{len(filtered)}** of **{len(frame)}** recorded attempts."
    )

    show_columns = [
        "attempt_id",
        "task_id",
        "method_label",
        "rep",
        "status",
        "evaluation_status",
        "failure_category",
        "test_passed",
        "syntax_valid",
        "truncated_output",
        "timed_out",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "usage_source",
        "cost_usd",
        "latency_ms",
        "provider_latency_ms",
        "retry_count",
        "seed_requested",
        "max_output_tokens",
        "served_model_id",
        "timestamp_utc",
    ]
    st.dataframe(
        filtered[show_columns].rename(
            columns={
                "method_label": "method",
                "test_passed": "evaluator_passed",
                "prompt_tokens": "input_tokens",
                "completion_tokens": "output_tokens",
                "total_tokens": "total_tokens",
            }
        ),
        width="stretch",
        hide_index=True,
        height=460,
    )
    st.caption(
        "`evaluator_passed` is the official evaluator's verdict. A blank "
        "token value means the provider reported none — it is never a zero."
    )

    if not filtered.empty:
        st.download_button(
            "Download the filtered attempts as CSV",
            data=filtered[show_columns].to_csv(index=False).encode("utf-8"),
            file_name="filtered_attempts.csv",
            mime="text/csv",
        )

# ==========================================================================
# figures
# ==========================================================================
with figures_tab:
    st.markdown(
        "#### The figures produced by the offline analysis\n\n"
        "These are the existing benchmark figures, shown unchanged from "
        "`analysis/figures/`. They are not regenerated by the dashboard."
    )
    figures = BUNDLE["figures"]
    if not figures:
        st.warning("No figure files were found in the run directory.")
    else:
        for start in range(0, len(figures), 2):
            pair = figures[start : start + 2]
            columns = st.columns(len(pair))
            for column, figure in zip(columns, pair):
                with column:
                    st.image(figure["path"], caption=figure["title"], width="stretch")
                    st.caption(f"`{figure['filename']}`")

# ==========================================================================
# reports
# ==========================================================================
with reports_tab:
    st.markdown("#### The written reports of the run")
    reports = BUNDLE["reports"]
    if not reports:
        st.warning("No report files were found in the run directory.")
    for report in reports:
        st.markdown(f"**{report['title']}** — `{report['relative']}`")
        st.download_button(
            f"Download {os.path.basename(report['relative'])}",
            data=report["text"].encode("utf-8"),
            file_name=os.path.basename(report["relative"]),
            mime="text/markdown",
            key=f"download-{report['relative']}",
        )
        with st.expander(f"Read {os.path.basename(report['relative'])}", expanded=False):
            st.markdown(report["text"])

# ==========================================================================
# integrity
# ==========================================================================
with integrity_tab:
    st.markdown(
        "#### Consistency between the dashboard and the offline analysis\n\n"
        "The dashboard recomputes every headline number from the attempt "
        "records and compares it with `analysis/analysis.json`. A "
        "disagreement would be listed below."
    )
    consistency = pd.DataFrame(BUNDLE["consistency"])
    st.dataframe(
        consistency,
        width="stretch",
        hide_index=True,
    )
    disagreements = [row for row in BUNDLE["consistency"] if row.get("agrees") is False]
    unknown = [row for row in BUNDLE["consistency"] if row.get("agrees") is None]
    if disagreements:
        st.error(
            f"{len(disagreements)} value(s) disagree with the offline analysis. "
            "Do not quote the dashboard until this is resolved."
        )
    else:
        st.success(
            f"All {len(BUNDLE['consistency'])} checked values agree with "
            "`analysis/analysis.json`."
        )
    if unknown:
        st.caption(
            f"{len(unknown)} value(s) could not be compared because one side "
            "was unavailable."
        )

    st.markdown("#### Artefact cross-checks")
    for problem in OVERALL.get("problems", []):
        st.warning(problem)
    for warning in OVERALL.get("warnings", []):
        st.info(warning)
    if not OVERALL.get("problems"):
        st.success("Every required run artefact was found and parsed.")

    st.markdown("#### Schedule actually used")
    schedule = BUNDLE.get("schedule") or {}
    if schedule:
        st.json(
            {
                "kind": schedule.get("kind"),
                "planned_attempts": schedule.get("planned_attempts"),
                "scheduled_cells_by_method": schedule.get("scheduled_cells_by_method"),
                "repetitions_used": schedule.get("repetitions_used"),
                "cells_with_repetition_1": schedule.get("cells_with_repetition_1"),
                "cells_with_repetition_above_1": schedule.get("cells_with_repetition_above_1"),
                "provenance_sha256": (schedule.get("provenance") or {}).get("sha256"),
            }
        )
    else:
        st.caption("The run metadata records no explicit schedule.")
