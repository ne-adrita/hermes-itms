"""Tests for multi-language surgical splicing (GAP-5).

Covers the LanguageAdapter abstraction: PythonAdapter (existing ast path must
still work) and TreeSitterAdapter for JavaScript/TypeScript.
"""

from __future__ import annotations

import pytest

from surgical_splicer import (
    LanguageAdapter,
    PythonAdapter,
    SurgicalTarget,
    TreeSitterAdapter,
    find_matches_for_language,
    get_adapter,
    locate_target_for_language,
)

# ---------------------------------------------------------------------------
# PythonAdapter: existing behaviour must be unchanged
# ---------------------------------------------------------------------------

PY_SOURCE = """import math

class AccountLedger:
    def reconcile_balance(self, target_date: str) -> float:
        total = 0
        for tx in self.transactions:
            total += tx.amount
        return total

class Other:
    def reconcile_balance(self, target_date: str) -> float:
        return 0.0
"""


def test_python_adapter_locate_class_method():
    adapter = PythonAdapter()
    target = adapter.locate_target(PY_SOURCE, "AccountLedger.reconcile_balance")
    assert isinstance(target, SurgicalTarget)
    assert target.qualified_name == "AccountLedger.reconcile_balance"
    assert target.parent_class == "AccountLedger"


def test_python_adapter_ambiguous_rejected():
    adapter = PythonAdapter()
    # reconcile_balance exists in two classes -> ambiguous -> None
    assert adapter.locate_target(PY_SOURCE, "reconcile_balance") is None


def test_python_adapter_missing_rejected():
    adapter = PythonAdapter()
    assert adapter.locate_target(PY_SOURCE, "does_not_exist") is None


# ---------------------------------------------------------------------------
# TreeSitterAdapter: JavaScript
# ---------------------------------------------------------------------------

JS_SOURCE = """const helper = () => 1;

class IngestionPipeline {
    constructor(channel) {
        this.channel = channel;
        this.queue = [];
    }

    dispatchBatch(expectedHashPrefix) {
        let dispatched = 0;
        for (const p of this.queue) {
            if (p.computeSha256().startsWith(expectedHashPrefix)) {
                dispatched += 1;
            }
        }
        return dispatched;
    }
}

function standaloneFunction(x) {
    return x + 1;
}
"""


def test_tree_sitter_adapter_js_is_language_javascript():
    adapter = TreeSitterAdapter("javascript")
    assert adapter.language == "javascript"


def test_tree_sitter_adapter_js_locate_method():
    adapter = TreeSitterAdapter("javascript")
    target = adapter.locate_target(JS_SOURCE, "IngestionPipeline.dispatchBatch")
    assert target is not None
    assert target.qualified_name == "IngestionPipeline.dispatchBatch"
    assert target.parent_class == "IngestionPipeline"
    assert target.start_line >= 1
    assert target.end_line >= target.start_line
    assert target.base_indentation == "    "


def test_tree_sitter_adapter_js_locate_standalone_function():
    adapter = TreeSitterAdapter("javascript")
    target = adapter.locate_target(JS_SOURCE, "standaloneFunction")
    assert target is not None
    assert target.qualified_name == "standaloneFunction"
    assert target.parent_class is None


def test_tree_sitter_adapter_js_ambiguous_rejected():
    adapter = TreeSitterAdapter("javascript")
    # dispatchBatch exists only once, so this should resolve; use a name that
    # does not exist for the missing case instead.
    assert adapter.locate_target(JS_SOURCE, "noSuchMethod") is None


# ---------------------------------------------------------------------------
# TreeSitterAdapter: TypeScript
# ---------------------------------------------------------------------------

TS_SOURCE = """export class TokenBucket {
    constructor(capacity, rate) {
        this.capacity = capacity;
        this.rate = rate;
        this.tokens = capacity;
        this.lastCheck = Date.now();
    }

    acquire(tokens: number): boolean {
        const now = Date.now();
        const elapsed = now - this.lastCheck;
        this.lastCheck = now;
        this.tokens = Math.min(this.capacity, this.tokens + elapsed * this.rate);
        if (this.tokens >= tokens) {
            this.tokens -= tokens;
            return true;
        }
        return false;
    }
}

export function rateLimit(key: string): void {
    // noop
}
"""


def test_tree_sitter_adapter_ts_locate_method():
    adapter = TreeSitterAdapter("typescript")
    target = adapter.locate_target(TS_SOURCE, "TokenBucket.acquire")
    assert target is not None
    assert target.qualified_name == "TokenBucket.acquire"
    assert target.parent_class == "TokenBucket"


def test_tree_sitter_adapter_ts_locate_function():
    adapter = TreeSitterAdapter("typescript")
    target = adapter.locate_target(TS_SOURCE, "rateLimit")
    assert target is not None
    assert target.qualified_name == "rateLimit"
    assert target.parent_class is None


# ---------------------------------------------------------------------------
# Dispatch helpers
# ---------------------------------------------------------------------------

def test_get_adapter_python_default():
    adapter = get_adapter("python")
    assert isinstance(adapter, PythonAdapter)


def test_get_adapter_javascript():
    adapter = get_adapter("javascript")
    assert isinstance(adapter, TreeSitterAdapter)
    assert adapter.language == "javascript"


def test_get_adapter_typescript():
    adapter = get_adapter("typescript")
    assert isinstance(adapter, TreeSitterAdapter)
    assert adapter.language == "typescript"


def test_get_adapter_unsupported_raises():
    with pytest.raises(ValueError):
        get_adapter("rust")


def test_locate_target_for_language_js():
    target = locate_target_for_language(JS_SOURCE, "IngestionPipeline.dispatchBatch", "javascript")
    assert target is not None
    assert target.qualified_name == "IngestionPipeline.dispatchBatch"


def test_find_matches_for_language_js():
    matches = find_matches_for_language(JS_SOURCE, "dispatchBatch", "javascript")
    assert len(matches) == 1
    assert matches[0].qualified_name == "IngestionPipeline.dispatchBatch"


def test_target_has_line_range_and_indentation():
    target = locate_target_for_language(JS_SOURCE, "IngestionPipeline.dispatchBatch", "javascript")
    assert isinstance(target.start_line, int) and target.start_line >= 1
    assert isinstance(target.end_line, int) and target.end_line >= target.start_line
    assert isinstance(target.base_indentation, str)
    assert target.original_text.strip() != ""
