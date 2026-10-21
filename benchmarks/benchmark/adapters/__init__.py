"""Editing-method adapters for the Phase 1 benchmark."""

from benchmarks.benchmark.adapters.base import AdapterResult, EditingAdapter
from benchmarks.benchmark.adapters.full_file import FullFileRegenerationAdapter
from benchmarks.benchmark.adapters.itms_splice import ItmsAstSpliceAdapter
from benchmarks.benchmark.adapters.search_replace import SearchReplacePatchAdapter

__all__ = [
    "AdapterResult",
    "EditingAdapter",
    "FullFileRegenerationAdapter",
    "SearchReplacePatchAdapter",
    "ItmsAstSpliceAdapter",
    "METHODS",
    "get_adapter",
]

METHODS = (
    "full_file_regeneration",
    "search_replace_patch",
    "itms_ast_splice",
)


def get_adapter(method: str, **kwargs) -> EditingAdapter:
    """Return the adapter implementing ``method``."""
    if method == "full_file_regeneration":
        return FullFileRegenerationAdapter(**kwargs)
    if method == "search_replace_patch":
        return SearchReplacePatchAdapter(**kwargs)
    if method == "itms_ast_splice":
        return ItmsAstSpliceAdapter(**kwargs)
    raise ValueError(f"unknown editing method: {method}")
