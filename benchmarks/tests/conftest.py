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
    def __init__(self, *args, **kwargs):
        raise OSError("network access is blocked in the Phase 1 test suite")


def _blocked(*args, **kwargs):
    raise OSError("network access is blocked in the Phase 1 test suite")


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
