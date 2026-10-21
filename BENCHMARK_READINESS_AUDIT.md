# Benchmark Readiness Audit — Hermes/ITMS

**Scope:** ITMS vs Aider vs Cline, 20 HumanEval-derived function-editing tasks,
5 repetitions per task per system (target: 20 x 3 x 5 = 300 system-task runs).

**Audit date:** 2026-10-20
**Live API calls made during this audit:** 0 (verified with a socket-level network block)

---

## 1. Current state vs. required state

| Requirement | Current | Status |
|---|---|---|
| 20 task definitions | 5 (`BM-01`..`BM-05`) in `benchmark_harness.py` | **BLOCKER** |
| HumanEval-derived tasks | None. 5 hand-written algorithmic tasks | **BLOCKER** |
| Aider integrated | Not integrated. Only a *simulated* "Aider style" text-diff patcher with hardcoded latencies | **BLOCKER** |
| Cline integrated | Not present anywhere in the codebase | **BLOCKER** |
| Repetitions (`rep`) | Not supported | **BLOCKER** |
| `system` field per run | Not recorded | **BLOCKER** |
| CSV/JSON durable export | Did not exist | **FIXED** |
| Unknown tokens/cost as `null` | Not possible (everything was `0.0`/`0`) | **FIXED** |
| Mock results never reported as live | Possible: mock run reported 100% pass rate | **FIXED** |
| Quota/auth/timeout/provider errors distinguished | All collapsed to one `RuntimeError` | **FIXED** |
| Official tests in isolated workspace | Yes — `sandbox.py` uses a temp file + subprocess + scrubbed env | OK |
| Regression detection | Python asserts per task, run in the sandbox | OK |

## 2. Critical finding: mock results were publishable as live results

With the current `.env`, `DEFAULT_TIER1_MODEL=Gemini 3.5 Flash` and
`DEFAULT_TIER2_MODEL=Claude Sonnet 5.5 / Fable` resolve to `gemini` and
`anthropic`, **neither of which has an API key configured**. The harness
therefore ran entirely on the deterministic mock engine and reported:

```
total_tasks: 5   passed: 5   pass_rate: 100.0%   savings vs frontier: 72.44%
```

Every one of those "passes" was fabricated. `strict_live` was auto-disabled
because no selected tier model had a key.

**Fix applied:** `benchmark_harness.assert_live_measurement()` now raises
`MockContaminationError` if any recorded run was mock-served or if any live
request failed. A mock-derived report cannot be published as a measurement.

**Required operator action:** point the tier defaults at a provider that has a
key, e.g. `DEFAULT_TIER1_MODEL=openrouter/free`.

## 3. Known limitation (documented, deliberately NOT fixed)

**Nested-function surgical splice mis-indentation.**
`ASTSurgicalSplicer.splice()` mishandles a *body-only* replacement (no `def`
header) that declares a nested function: the nested `def` is hoisted to module
level and the original method body is overwritten. The spliced file is
syntactically valid but semantically wrong, and `splice()` still reports
`success=True`.

Characterized by `test_benchmark_safety.py::TestKnownSplicerLimitation` so the
baseline is measured against the current implementation. Full-node replacements
(with a `def` header) are unaffected. **Not fixed, per audit constraint 8.**

## 4. What "Aider" and "Cline" actually are here

- **Aider**: `surgical_splicer.TextDiffPatcher` implements a *simulated*
  search/replace patcher. `edit_orchestrator.py` feeds it **pre-written**
  replacement blocks and assigns hardcoded latencies (`1650.0` / `520.0` /
  `185.0` ms). It never calls a model. This measures patch-application
  mechanics, not Aider the agent.
- **Cline**: absent from the codebase entirely.

Neither is a real agentic system, so a 3-way comparison against them is not
currently possible. A genuine comparison needs either the upstream tools
installed and driven, or a documented re-implementation of their edit
strategies with **real model calls** (the current hardcoded-latency
simulation must not be reported as measured performance).

## 5. Result recording (now available)

`BenchmarkRunRecord` captures, per run:
`system`, `task_id`, `task_name`, `category`, `rep`, `model`, `passed`,
`syntax_valid`, `timed_out`, `error_type`, `api_error`, `api_error_category`,
`http_status`, `duration_ms`, `input_tokens`, `output_tokens`, `total_tokens`,
`cost_usd`, `is_mock`, `escalated_to_tier2`, `tier1_surgical_repairs`, `notes`.

Unmeasured values are `None` (never `0`). Export via
`MacroBenchmarkSuite.run_suite(..., output_dir="results")` writes
`results/<name>.json` and `results/<name>.csv`.

## 6. Error classification (strict-live)

| Condition | Category | HTTP |
|---|---|---|
| Bad/missing credentials | `auth_error` | 401 / 403 |
| Free-tier daily cap | `quota_exceeded` | 429 |
| Out of credits | `quota_exceeded` | 402 |
| Upstream failure | `provider_error` | 5xx |
| Timeout | `timeout` | — |
| Connection failure | `network_error` | — |

All are raised as `LiveModeError` (quota errors as `QuotaExceededError`) and
recorded as **failed** runs. They are never converted into mock results.
