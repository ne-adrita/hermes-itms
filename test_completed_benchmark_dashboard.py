"""
End-to-end check that the Streamlit dashboard renders the completed
120-attempt ITMS benchmark.

This test only renders the dashboard in-process. It never runs a benchmark,
never calls a model provider and never writes a file. Any NON-loopback network
connection attempted while the dashboard renders is refused and recorded, so a
provider call could not slip through unnoticed.

The check lives at the repository root (not under ``benchmarks/tests``) because
the package ``conftest.py`` installs a session-wide socket guard that blocks
the loopback sockets Streamlit's test runner needs. It is skipped automatically
when an offline guard is active.
"""

from __future__ import annotations

import os
import socket

import pytest

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
APP_PATH = os.path.join(PROJECT_ROOT, "app.py")


def _guard_is_active() -> bool:
    """True when an offline guard has replaced the stdlib socket helpers."""
    return getattr(socket.create_connection, "__module__", "socket") != "socket"


def _is_loopback(address) -> bool:
    if isinstance(address, tuple) and address:
        host = str(address[0])
        return host in ("127.0.0.1", "::1", "localhost", "0.0.0.0", "")
    return True  # AF_UNIX socket paths


@pytest.mark.skipif(_guard_is_active(), reason="offline socket guard is active")
def test_dashboard_renders_the_completed_benchmark():
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

    # The completed-results tab exists and no existing tab was removed.
    assert "Completed Benchmark Results" in labels
    for preserved in (
        "Live Relay Runner (Milestones 2 & 3)",
        "Single-Query Prompt Telemetry",
        "Full Cascade Lifecycle Economics",
        "Model Catalog & Tier Analysis",
        "Surgical Code Modification (Phase 2)",
        "Macro Benchmark Suite (5-Task)",
    ):
        assert preserved in labels, f"existing tab lost: {preserved}"

    # The summary is visible at the top of the page, on every tab.
    metrics = {m.label: m.value for m in at.metric}
    assert metrics.get("Scheduled attempts") == "120"
    assert metrics.get("Successful completions") == "114"
    assert metrics.get("Output failures") == "6"
    assert metrics.get("Completion rate") == "95.0% (114/120)"
    assert metrics.get("Evaluated attempts") == "114"
    assert metrics.get("Evaluator pass rate") == "100.0% (114/114)"

    # The dedicated tab carries the six figures and the method table.
    completed_tab = at.tabs[labels.index("Completed Benchmark Results")]
    elements = list(completed_tab)
    assert len([e for e in elements if type(e).__name__ == "Image"]) == 6
    joined = "\n".join(
        getattr(e, "value", "") for e in elements if type(e).__name__ == "Markdown"
    )
    assert "97.5% (39/40)" in joined
    assert "100.0% (40/40)" in joined
    assert "87.5% (35/40)" in joined

    # The limitation note must be present and must not claim superiority.
    notes = "\n".join(
        getattr(e, "value", "") for e in elements if type(e).__name__ == "Warning"
    )
    assert "Evaluator pass rate** is calculated **only from attempts that reached" in notes
    assert "Completion rate** is calculated from **all scheduled attempts" in notes
    assert "must **not** be presented as conclusive evidence" in notes

    # The top banner must not claim that ITMS beats the baselines either.
    banner_captions = "\n".join(
        getattr(e, "value", "") for e in list(at.main) if type(e).__name__ == "Caption"
    )
    assert "do **not** show that ITMS outperforms the baselines" in banner_captions
    assert "never reached the evaluator" in banner_captions
