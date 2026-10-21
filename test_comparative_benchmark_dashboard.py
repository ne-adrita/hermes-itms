"""
End-to-end check that the Streamlit dashboard renders the ITMS vs Aider vs
Cline comparative benchmark tab.

This test only renders the dashboard in-process. It never runs a benchmark,
never calls a model provider and never writes a file. Any NON-loopback network
connection attempted while the dashboard renders is refused and recorded, so a
provider call could not slip through unnoticed.

Like the completed-benchmark dashboard test, this one lives at the repository
root because the package ``conftest.py`` installs a session-wide socket guard
that blocks the loopback sockets Streamlit's test runner needs. It is skipped
automatically when an offline guard is active.
"""

from __future__ import annotations

import os
import socket

import pytest

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
APP_PATH = os.path.join(PROJECT_ROOT, "app.py")

#: Every tab that must survive the addition of the comparative tab.
PRESERVED_TABS = (
    "Completed Benchmark Results",
    "Comparative Benchmark Results",
    "Live Relay Runner (Milestones 2 & 3)",
    "Single-Query Prompt Telemetry",
    "Full Cascade Lifecycle Economics",
    "Model Catalog & Tier Analysis",
    "Surgical Code Modification (Phase 2)",
    "Macro Benchmark Suite (5-Task)",
)


def _guard_is_active() -> bool:
    """True when an offline guard has replaced the stdlib socket helpers."""
    return getattr(socket.create_connection, "__module__", "socket") != "socket"


def _is_loopback(address) -> bool:
    if isinstance(address, tuple) and address:
        host = str(address[0])
        return host in ("127.0.0.1", "::1", "localhost", "0.0.0.0", "")
    return True  # AF_UNIX socket paths


@pytest.mark.skipif(_guard_is_active(), reason="offline socket guard is active")
def test_dashboard_renders_the_comparative_benchmark_tab():
    if not os.path.isfile(APP_PATH):
        pytest.skip("app.py not present")

    from streamlit.testing.v1 import AppTest

    external_attempts = []
    real_socket = socket._socket.socket

    class _GuardedSocket(real_socket):
        def connect(self, address):
            if not _is_loopback(address):
                external_attempts.append(address)
                raise OSError("test: outbound connection blocked")
            return real_socket.connect(self, address)

    previous_socket = socket.socket
    previous_getaddrinfo = socket.getaddrinfo
    socket.socket = _GuardedSocket
    socket.getaddrinfo = socket._socket.getaddrinfo
    try:
        at = AppTest.from_file(APP_PATH, default_timeout=240)
        at.run()
    finally:
        socket.socket = previous_socket
        socket.getaddrinfo = previous_getaddrinfo

    assert not at.exception, "the dashboard raised an exception while rendering"
    assert external_attempts == [], (
        f"the dashboard attempted an external connection: {external_attempts}"
    )

    labels = [tab.label for tab in at.tabs]
    for preserved in PRESERVED_TABS:
        assert preserved in labels, f"tab lost: {preserved}"

    # The comparative tab must not break the existing completed-benchmark tab.
    completed_tab = at.tabs[labels.index("Completed Benchmark Results")]
    elements = list(completed_tab)
    assert len([e for e in elements if type(e).__name__ == "Image"]) == 6
    joined = "\n".join(
        getattr(e, "value", "") for e in elements if type(e).__name__ == "Markdown"
    )
    assert "97.5% (39/40)" in joined
    assert "100.0% (40/40)" in joined
    assert "87.5% (35/40)" in joined

    # The comparative tab renders (or reports that no dataset exists yet) and
    # never claims a provider call.
    comparative = at.tabs[labels.index("Comparative Benchmark Results")]
    comparative_text = "\n".join(
        getattr(e, "value", "")
        for e in list(comparative)
        if type(e).__name__
        in ("Markdown", "Subheader", "Title", "Header", "Caption", "Warning", "Info", "Error")
    )
    assert "ITMS vs Aider vs Cline" in comparative_text
    assert "never contacts" in comparative_text or "never launches" in comparative_text

    # When the Phase 2 dataset is present, the tab must show it, with the
    # completion-status metrics and every figure.
    import comparative_viewer

    run_dir = comparative_viewer.find_latest_live_run()
    if run_dir:
        loaded = comparative_viewer.load_comparative_run(run_dir)
        records = loaded["records"]
        assert records, "the discovered run directory has no records"
        assert not any(r.get("is_mock") for r in records), (
            "a mock record leaked into the live comparative dataset"
        )
        assert len(loaded["errors"]) == 0 or all(
            "analysis" in error for error in loaded["errors"]
        ), loaded["errors"]

        images = [e for e in list(comparative) if type(e).__name__ == "Image"]
        assert len(images) == len(comparative_viewer.GRAPH_FILENAMES), (
            f"expected {len(comparative_viewer.GRAPH_FILENAMES)} figures, "
            f"got {len(images)}"
        )

        # The integrity / completion status metrics must be shown.
        metric_values = {(m.label, m.value) for m in at.metric}
        planned = loaded["metadata"].get("planned_attempts")
        recorded = len(records)
        assert ("Recorded attempts", str(recorded)) in metric_values
        if planned:
            assert ("Planned attempts", str(planned)) in metric_values
        assert ("Mock records", "0") in metric_values

        # The seed limitation for Cline must be disclosed.
        assert "no request-level seed is available" in comparative_text
        # And the non-HumanEval caveat must be present.
        assert "not HumanEval" in comparative_text

    # The comparative viewer module must not import a network client.
    viewer_path = os.path.join(PROJECT_ROOT, "comparative_viewer.py")
    with open(viewer_path, "r", encoding="utf-8") as fh:
        viewer_source = fh.read()
    for forbidden in ("httpx", "requests", "urllib.request", "socket."):
        assert forbidden not in viewer_source, forbidden
