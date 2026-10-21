"""
Phase 1 benchmark tests.

These tests never contact an inference API.  They use fake providers and
dependency injection, and the whole module is executed with sockets blocked by
``benchmarks/tests/conftest.py``.
"""

from __future__ import annotations

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
