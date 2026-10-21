"""
Phase 1 benchmark package.

A reproducible runner for the ITMS code-editing benchmark: three editing
methods (full-file regeneration, search/replace patch, ITMS AST splice) are
evaluated on the 20 Phase 0 HumanEval-derived repair tasks.

The package never performs an inference call on import.  Live calls happen only
through :class:`benchmarks.benchmark.provider.LiveProvider`, which is
constructed explicitly by the runner in ``--mode live``.
"""

__all__ = [
    "provider",
    "tasks",
    "adapters",
    "pipeline",
    "records",
    "experiment",
    "analysis",
]
