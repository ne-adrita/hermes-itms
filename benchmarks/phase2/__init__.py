"""
Phase 2 experiment support.

This package holds the tooling that a Phase 2 (AST-guided surgical editing)
benchmark needs and that the Phase 0/1 package does not provide:

*   :mod:`benchmarks.phase2.task_selection` - the pre-registered, outcome
    independent task-selection rule and the property report of the current
    task set.

Nothing in this package contacts a provider.
"""

__all__ = ["task_selection"]
