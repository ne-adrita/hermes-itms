"""System adapters for the ITMS vs Aider vs Cline comparison."""

from .base import (
    FailureCategory,
    SystemAdapter,
    SystemOutcome,
    TaskBundle,
    UsageSource,
)
from .aider import AiderSystemAdapter
from .cline import ClineSystemAdapter
from .itms import ItmsSystemAdapter
from .mock import MockSystemAdapter

__all__ = [
    "AiderSystemAdapter",
    "ClineSystemAdapter",
    "FailureCategory",
    "ItmsSystemAdapter",
    "MockSystemAdapter",
    "SystemAdapter",
    "SystemOutcome",
    "TaskBundle",
    "UsageSource",
]
