"""
Test fixtures for the Phase 1 benchmark tests.

The conftest installs a socket guard before any test module is imported, so
every test in this package provably runs offline: any attempt to open a
network connection raises immediately.
"""

from __future__ import annotations

import os
import socket
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


class _BlockedSocket(socket.socket):
    #: The unpatched socket class.  It is kept on the class itself so that a
    #: test can reach it no matter how pytest happened to import this
    #: module.
    original = socket.socket

    def __init__(self, *args, **kwargs):
        raise OSError("network access is blocked in the Phase 1 test suite")


def _blocked(*args, **kwargs):
    raise OSError("network access is blocked in the Phase 1 test suite")


#: The unpatched socket class, captured before the guard is installed.
#:
#: The guard replaces the whole ``socket.socket`` class, which also blocks
#: purely *local* uses such as ``socket.socketpair()`` — the self-pipe that
#: an asyncio event loop creates.  A test that needs such a local socket
#: pair (the Streamlit dashboard's own test runner does) can restore this
#: class for the duration of that test.  The guard against real network
#: access is unaffected: ``socket.create_connection`` and
#: ``socket.getaddrinfo`` stay blocked.
UNBLOCKED_SOCKET = socket.socket


@pytest.fixture(autouse=True, scope="session")
def _no_network():
    """Block outbound sockets for the whole test session."""
    original_socket = socket.socket
    original_create_connection = socket.create_connection
    original_getaddrinfo = socket.getaddrinfo
    socket.socket = _BlockedSocket
    socket.create_connection = _blocked
    socket.getaddrinfo = _blocked
    try:
        yield
    finally:
        socket.socket = original_socket
        socket.create_connection = original_create_connection
        socket.getaddrinfo = original_getaddrinfo


@pytest.fixture(scope="session")
def repository():
    from benchmarks.benchmark.tasks import TaskRepository

    return TaskRepository()


@pytest.fixture(scope="session")
def task(repository):
    """A single task from the approved Phase 0 set."""
    return repository.get("HE-EDIT-001")


@pytest.fixture(scope="session")
def simple_task(repository):
    """A small task whose reference implementation is trivial to reproduce."""
    return repository.get("HE-EDIT-006")
