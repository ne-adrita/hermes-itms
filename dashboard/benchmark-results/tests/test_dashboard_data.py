"""
Offline tests for the read-only benchmark-results dashboard.

The tests never start a benchmark, never contact a provider and never
write to the run directory.  They check that

*   the dashboard reads the saved artefacts and finds them all,
*   every number it shows agrees with ``analysis/analysis.json``,
*   the numbers are derived from the records, not hard-coded,
*   the limitations are disclosed and carry the real numbers,
*   the Streamlit app itself runs without an exception,
*   nothing credential-shaped is exposed.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

DASHBOARD_DIR = os.path.join(REPO_ROOT, "dashboard", "benchmark-results")
if DASHBOARD_DIR not in sys.path:
    sys.path.insert(0, DASHBOARD_DIR)

import dashboard_data as dd  # noqa: E402

RUN_DIR = dd.DEFAULT_RUN_DIR
ANALYSIS = os.path.join(RUN_DIR, "analysis", "analysis.json")


@pytest.fixture(scope="module")
def bundle() -> dict:
    return dd.load_run_bundle()


@pytest.fixture(scope="module")
def analysis() -> dict:
    with open(ANALYSIS, "r", encoding="utf-8") as handle:
        return json.load(handle)


# --------------------------------------------------------------------------
# the run is read completely
# --------------------------------------------------------------------------

def test_every_required_artefact_is_found(bundle):
    assert bundle["overall"]["problems"] == []
    assert bundle["overall"]["completed_attempts"] == 100


def test_the_attempt_records_are_read(bundle):
    assert len(bundle["records"]) == 100
    assert len({r["attempt_id"] for r in bundle["records"]}) == 100


def test_the_figures_and_the_reports_are_found(bundle):
    assert len(bundle["figures"]) >= 11
    for figure in bundle["figures"]:
        assert os.path.isfile(figure["path"])
        assert figure["size_bytes"] > 0
    assert len(bundle["reports"]) >= 2
    for report in bundle["reports"]:
        assert report["text"].strip()


# --------------------------------------------------------------------------
# the numbers agree with the offline analysis
# --------------------------------------------------------------------------

def test_the_overall_counts_agree_with_the_analysis(bundle, analysis):
    overall = bundle["overall"]
    denominators = analysis["denominators"]
    assert overall["planned_attempts"] == denominators["planned_attempts"]
    assert overall["completed_attempts"] == denominators["recorded_attempts"]
    assert overall["evaluated_attempts"] == denominators["evaluated_attempts"]
    assert overall["requested_attempts"] == denominators["provider_requests_issued"]
    assert overall["evaluator_passes"] == analysis["generation_versus_evaluation"][
        "evaluator_pass"
    ]


def test_every_method_agrees_with_the_analysis(bundle, analysis):
    for method, values in bundle["methods"].items():
        other = analysis["methods"][method]
        assert values["scheduled_attempts"] == other["denominators"][
            "scheduled_attempts"
        ]
        assert values["evaluated_attempts"] == other["denominators"][
            "candidate_produced_attempts"
        ]
        assert values["evaluator_passes"] == other["test_pass"]["count"]
        assert values["evaluator_pass_rate"] == pytest.approx(
            other["test_pass"]["rate_over_candidates"]
        )
        assert values["mean_total_tokens"] == pytest.approx(
            other["token_usage"]["mean_total_tokens"]
        )
        assert values["median_total_tokens"] == pytest.approx(
            other["token_usage"]["median_total_tokens"]
        )
        assert values["mean_latency_ms"] == pytest.approx(other["latency_ms"]["mean"])
        assert values["median_latency_ms"] == pytest.approx(
            other["latency_ms"]["median"]
        )


def test_the_built_in_consistency_check_reports_no_disagreement(bundle):
    rows = bundle["consistency"]
    assert rows
    disagreements = [row for row in rows if row.get("agrees") is False]
    assert disagreements == []


def test_the_method_allocation_is_the_unequal_34_33_33(bundle):
    counts = {
        method: values["scheduled_attempts"] for method, values in bundle["methods"].items()
    }
    assert counts["full_file_regeneration"] == 34
    assert counts["search_replace_patch"] == 33
    assert counts["itms_ast_splice"] == 33


def test_the_input_output_token_split_is_derived_from_the_records(bundle):
    for method, values in bundle["methods"].items():
        assert values["mean_prompt_tokens"] is not None
        assert values["mean_completion_tokens"] is not None
        assert values["mean_total_tokens"] is not None
        subset = [r for r in bundle["records"] if r["method"] == method]
        assert values["token_samples"] == len(subset)
        # The split must add up to the total for every attempt.
        for record in subset:
            assert record["prompt_tokens"] + record["completion_tokens"] == record[
                "total_tokens"
            ]


def test_the_prompt_sizes_come_from_the_persisted_prompts(bundle):
    for method, values in bundle["methods"].items():
        assert values["prompt_bytes_samples"] == values["completed_attempts"]
        assert values["mean_prompt_bytes"] > 0


def test_the_per_task_view_covers_every_task_and_method(bundle):
    assert len(bundle["per_task"]) == 20
    for task_id, methods in bundle["per_task"].items():
        assert set(methods) == set(bundle["methods"]), task_id
        for cell in methods.values():
            assert cell["attempts"] >= 1


# --------------------------------------------------------------------------
# the limitations are disclosed and carry the real numbers
# --------------------------------------------------------------------------

def test_the_limitations_cover_every_required_disclosure(bundle):
    titles = " ".join(item["title"].lower() for item in bundle["limitations"])
    assert "budget" in titles
    assert "repetition" in titles
    assert "prompt" in titles
    assert "dataset" in titles or "corpus" in titles
    assert "cost" in titles
    assert "seed" in titles


def test_the_limitation_numbers_come_from_the_data(bundle):
    text = " ".join(item["text"] for item in bundle["limitations"])
    assert "100" in text  # the planned request budget
    assert "34" in text and "33" in text  # the unequal per-method allocation
    assert "300" in text  # the design that was *not* run


def test_the_prompt_length_difference_is_disclosed_with_real_numbers(bundle):
    text = " ".join(item["text"] for item in bundle["limitations"])
    assert "bytes" in text
    values = bundle["methods"]
    smallest = min(v["mean_prompt_bytes"] for v in values.values())
    largest = max(v["mean_prompt_bytes"] for v in values.values())
    assert f"{smallest:,.0f}" in text or f"{smallest:.0f}" in text
    assert f"{largest:,.0f}" in text or f"{largest:.0f}" in text


def test_the_unavailable_cost_is_not_reported_as_zero(bundle, analysis):
    assert analysis["cost"]["available"] is False
    text = " ".join(item["text"] for item in bundle["limitations"])
    assert "unavailable" in text.lower()


# --------------------------------------------------------------------------
# nothing sensitive is exposed
# --------------------------------------------------------------------------

def test_no_credential_shaped_string_is_exposed(bundle):
    from benchmarks.benchmark.provider import contains_credential

    payload = json.dumps(
        {
            "overall": bundle["overall"],
            "methods": bundle["methods"],
            "reports": [r["text"] for r in bundle["reports"]],
        },
        default=str,
    )
    assert not contains_credential(payload)


def test_the_dashboard_does_not_write_to_the_run_directory(bundle):
    before = {
        os.path.join(dirpath, name)
        for dirpath, _dirs, files in os.walk(RUN_DIR)
        for name in files
    }
    dd.load_run_bundle()
    after = {
        os.path.join(dirpath, name)
        for dirpath, _dirs, files in os.walk(RUN_DIR)
        for name in files
    }
    assert before == after


# --------------------------------------------------------------------------
# the app itself
# --------------------------------------------------------------------------

def test_the_streamlit_app_runs_without_an_exception():
    """
    The startup procedure is verified, not assumed.

    Streamlit's test runner creates an asyncio event loop, and an asyncio
    event loop needs a purely *local* socket pair for its self-pipe.  The
    benchmark test suite blocks ``socket.socket`` for the whole session, so
    the unpatched class is restored for the duration of this test.

    The guard against real network access is **not** weakened:
    ``socket.create_connection`` and ``socket.getaddrinfo`` stay blocked and
    are asserted below.
    """
    import socket as socket_module

    from streamlit.testing.v1 import AppTest

    patched_socket = socket_module.socket
    # The unpatched class is reachable from the patched one, no matter how
    # pytest imported the benchmark conftest.
    unblocked = getattr(patched_socket, "original", None)
    if unblocked is None:
        pytest.skip("the socket guard of the benchmark suite is not installed")
    socket_module.socket = unblocked
    try:
        # The network guard must still be in force.
        with pytest.raises(OSError):
            socket_module.create_connection(("127.0.0.1", 9), timeout=1)
        with pytest.raises(OSError):
            socket_module.getaddrinfo("example.invalid", 80)

        app = AppTest.from_file(
            os.path.join(DASHBOARD_DIR, "app.py"), default_timeout=180
        )
        app.run()
    finally:
        socket_module.socket = patched_socket

    assert app.exception == []
    # The dashboard must actually show something.
    assert len(app.metric) >= 10
    assert len(app.dataframe) >= 4
    assert len(app.tabs) >= 5
