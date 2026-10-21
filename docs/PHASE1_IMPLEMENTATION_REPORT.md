# Phase 1 Implementation Report — ITMS Code-Editing Benchmark

**Date:** 2026-10-20
**Scope:** Phase 1 — implementation, testing and audit of the benchmark runner.
**Status:** implementation complete and audited. **The 300-attempt live
benchmark has NOT been executed. No live benchmark result exists yet.**

---

## 1. Files added

### Benchmark package (new)

| File | Purpose |
|---|---|
| `benchmarks/benchmark_runner.py` | CLI entry point (`--mode`, `--reps`, `--methods`, `--tasks`, `--model`, `--provider`, `--output-dir`, `--seed`, `--dry-run`, `--yes`, `--allow-overwrite`) |
| `benchmarks/benchmark/__init__.py` | package marker |
| `benchmarks/benchmark/provider.py` | provider interface, `MockProvider`, `LiveProvider`, secret redaction |
| `benchmarks/benchmark/tasks.py` | `TaskRepository`: the strict task → mutated-source mapping (no globbing) |
| `benchmarks/benchmark/pipeline.py` | the single-attempt pipeline |
| `benchmarks/benchmark/records.py` | `AttemptRecord` schema + incremental JSONL/CSV writers |
| `benchmarks/benchmark/experiment.py` | scheduling, output directory management, run metadata, summary |
| `benchmarks/benchmark/analysis.py` | metrics with explicit denominators, paired task-level comparison, bootstrap CI |
| `benchmarks/benchmark/adapters/__init__.py` | adapter registry (`METHODS`, `get_adapter`) |
| `benchmarks/benchmark/adapters/base.py` | `EditingAdapter` interface, `AdapterResult`, failure-reason vocabulary |
| `benchmarks/benchmark/adapters/full_file.py` | treatment A |
| `benchmarks/benchmark/adapters/search_replace.py` | treatment B |
| `benchmarks/benchmark/adapters/itms_splice.py` | treatment C |

### Tests (new)

| File | Purpose |
|---|---|
| `benchmarks/tests/__init__.py` | package marker |
| `benchmarks/tests/conftest.py` | session-wide socket guard (proves the tests run offline) + fixtures |
| `benchmarks/tests/test_phase1_provider.py` | provider configuration, mock-fallback prohibition, error taxonomy, usage/cost honesty, secret redaction, truncation |
| `benchmarks/tests/test_phase1_adapters.py` | output contracts, prompt hygiene (no gold, no mutation metadata), proof that the project patcher/splicer/skeletonizer are the ones used, preserved splicer limitation |
| `benchmarks/tests/test_phase1_pipeline.py` | single-attempt pipeline, syntax/test/timeout/provider failures, workspace cleanup, scheduling (300 attempts), incremental persistence, output-directory protection, Phase 0 suite still green |
| `benchmarks/tests/test_phase1_metrics.py` | metric denominators, mock-contamination detection, paired comparison + bootstrap, CLI behaviour, mock end-to-end smoke test |

### Documentation (new)

`docs/PHASE1_BENCHMARK_DESIGN.md`, `docs/PHASE1_METRICS_AND_ANALYSIS.md`,
`docs/PHASE1_IMPLEMENTATION_REPORT.md` (this file).

### Root configuration (new)

`pytest.ini` — pytest discovery configuration. **Why it was needed:** the
Phase 0 task data contains `benchmarks/data/tasks/<id>/official_test.py`,
whose names match pytest's default `*_test.py` pattern. A bare `pytest` run
from the repository root therefore tried to *collect the benchmark data as
test modules* and aborted with 19 collection errors. `pytest.ini` excludes
`benchmarks/data` from collection and lists the real test modules explicitly.
No existing test was changed or weakened.

## 2. Files modified

**One existing file was modified: `model_dispatcher.py`.** The change is
additive and backward compatible:

*   `ModelResponse` gained `finish_reason`, `served_model_id`, `retry_count`,
    `usage_source`, `cost_usd`, `cost_source` — all with defaults, so every
    existing construction site keeps working;
*   `ModelDispatcher.__init__` gained `max_retries` (default `2`, the previous
    behaviour) and `request_timeout` (default `None`, the previous per-provider
    timeouts);
*   `_call_openai_compatible`, `_call_gemini` and `_call_anthropic` now record
    the finish reason, the served model id, the number of retries actually
    performed, and whether the usage numbers were provider-reported;
*   a new `ModelDispatcher.generate_live()` performs a strictly live call: it
    refuses `force_mock=True` and fails closed with `ModelConfigurationError`
    when no credential is configured.

The existing 144-test suite still passes unchanged after this modification.

No other existing file was touched: `sandbox.py`, `skeletonizer.py`,
`surgical_splicer.py`, `prompt_manager.py`, `config.py`, `benchmark_harness.py`,
`edit_orchestrator.py`, `relay_orchestrator.py`, `app.py`, `telemetry.py`,
`.env`, `env.example` and all existing test files are byte-identical to their
Phase 0 state (verified by modification timestamps and by re-running the
suite).

## 3. Interfaces actually integrated

| Concern | Implementation used | Notes |
|---|---|---|
| Search/replace application | `surgical_splicer.TextDiffPatcher.apply_search_replace` | the project's existing patcher, not a re-implementation; its ambiguity/whitespace failure modes are therefore part of the measurement |
| ITMS context skeleton | `skeletonizer.skeletonize_code` | existing implementation, called with the same flags the legacy orchestrator used |
| ITMS target location | `surgical_splicer.ASTNodeLocator.locate_target` | existing implementation |
| ITMS splice | `surgical_splicer.ASTSurgicalSplicer.splice` | existing implementation, **unmodified**; the known nested-function limitation is preserved and covered by a test |
| Response cleanup | `surgical_splicer.ASTSurgicalSplicer.clean_replacement_code` | existing implementation |
| Official test execution | `benchmarks/phase0/runner.py:run_official_test` | isolated temp directory, scrubbed env, user site disabled, **no injected convenience imports**, sockets blocked via `sitecustomize` |
| Provider credentials / base URL | `config.AppConfig.resolve_api_key`, `resolve_base_url`, `get_openrouter_headers` | existing configuration, no credential is logged |
| Provider error taxonomy | `model_dispatcher.LiveModeError`, `QuotaExceededError`, `ModelConfigurationError` | existing categories reused |
| Live transport | `model_dispatcher.ModelDispatcher.generate_live` | minimal extension of the existing dispatcher (see §2) |

`benchmarks/phase0/runner.py` was used instead of the legacy `sandbox.py`
because `sandbox.py` injects convenience imports into the evaluated script
and splices the candidate into the context in a cascade-specific way; using it
would both violate the "no convenience imports" rule and introduce a
difference between the three methods.

## 4. Tests run and results

Command (with an interpreter-level socket block installed via `PYTHONPATH`
for the whole session):

```
PYTHONPATH=/tmp/offline_guard .venv/bin/python -m pytest -q
```

Result:

```
264 passed, 23 subtests passed in 29.17s
```

Breakdown:

| Suite | Tests | Result |
|---|---|---|
| `test_itms.py`, `test_milestones_2_3.py`, `test_openrouter.py`, `test_surgical_splicer.py`, `test_benchmark_safety.py`, `test_tdsc.py` (pre-existing) | 144 (+23 subtests) | all pass, unchanged |
| `benchmarks/phase0/test_phase0_tasks.py` (Phase 0, unchanged) | 29 | all pass, unchanged |
| `benchmarks/tests/test_phase1_provider.py` | 25 | all pass |
| `benchmarks/tests/test_phase1_adapters.py` | 27 | all pass |
| `benchmarks/tests/test_phase1_pipeline.py` | 24 | all pass |
| `benchmarks/tests/test_phase1_metrics.py` | 15 | all pass |

The Phase 0 artefacts are untouched: `python benchmarks/phase0/validate_tasks.py
--verify-manifest` reports `tasks: 20, passed: 20, failed: 0` and
`manifest: OK (88 files)`.

### Mock-mode end-to-end verification

```
.venv/bin/python benchmarks/benchmark_runner.py --mode mock --reps 1 --output-dir /tmp/mock_e2e
```

60 attempts (20 tasks × 3 methods × 1 rep) executed, all recorded, all
persisted incrementally; every attempt failed the official test by design
(the mock engine deliberately leaves the defect in place). The summary
reported `mode=mock`, `is_measurement=false` and the mock warning; no usage
or cost was recorded as provider-reported.

## 5. Network access, inference calls, credits, packages

*   **Network access during Phase 1: none.** No HTTP request was issued by
    any Phase 1 code path during implementation or testing. The test suite
    additionally blocks sockets at the interpreter level
    (`benchmarks/tests/conftest.py`) and the whole suite was additionally run
    with an external `sitecustomize` socket block.
*   **No inference API calls were made.** No request was sent to OpenRouter,
    OpenAI, Anthropic, Gemini or DeepSeek. The live path was exercised only
    through injected fake dispatchers.
*   **No credits were purchased** and no quota or account setting was
    touched.
*   **No new package was installed.** The implementation uses only
    `httpx`, `pytest`, `python-dotenv` and `tiktoken`, all of which were
    already present in `.venv` and listed in `requirements.txt`.
*   `.env`, `env.example` and the credential values were **not** modified,
    printed or copied into any artefact. Error messages are passed through a
    secret-redaction filter before being stored.

## 6. Known bugs and limitations

1. **The known ITMS nested-function splice bug is preserved, not fixed**
   (required by the brief). A body-only replacement that declares a nested
   function is hoisted to module level by the existing splicer and still
   reports success. It is characterised by
   `test_phase1_adapters.py::TestItmsSpliceAdapter::test_known_nested_function_limitation_is_preserved`.
2. **No live benchmark has been run**, so no model has been shown to solve any
   task through any of the three methods. The end-to-end path is verified
   with fake providers and with the deterministic mock engine only.
3. **Provider capability claims are requests, not facts.** `temperature` and
   `max_output_tokens` are sent on every request, but the provider may ignore
   them and there is no provider-side acknowledgement. The served model id is
   recorded when the provider returns one.
4. **Cost is `null` unless the provider itself returns a cost.** No price
   table is applied; an estimate would have to be stored in separate
   `*_estimate` fields.
5. **Resumption is refused.** The runner cannot prove which attempts already
   reached the provider, so it refuses to continue an incomplete experiment
   and asks for a new output directory. This means a crashed live run has to
   be restarted from scratch (the completed attempts are still preserved in
   `attempts.jsonl` for analysis).
6. **The mock engine never produces a correct repair.** That is deliberate:
   a mock run must not be able to suggest that any method is better than
   another. Success-shaped responses are only used inside the unit tests.
7. **The legacy `edit_orchestrator.py` still contains hardcoded latencies
   (1650/520/185 ms) and pre-written "simulated" model outputs.** It is not
   used by the Phase 1 runner and was not modified; it remains a legacy
   simulation and must not be reported as a measurement.
8. **`benchmark_harness.py` still describes the 5-task legacy suite**, whose
   `test_benchmark_safety.py` explicitly asserts that five tasks exist. The
   Phase 1 task set is separate and lives under `benchmarks/`.
9. **Statistics are weak by construction.** 20 tasks, 5 repetitions, one
   model, one provider. The bootstrap interval over tasks is reported to make
   that visible; no significance claim is made.
10. **The isolation is a best-effort sandbox**, not a security boundary
    against hostile code.

## 7. Commands

```bash
# 1. Offline validation of the Phase 0 task set (no provider call).
python benchmarks/phase0/validate_tasks.py --verify-manifest \
    --json benchmarks/data/validation_report.json

# 2. The full test suite (offline; sockets are blocked by the test conftest).
python -m pytest -q

# 3. Mock-mode smoke test (pipeline verification only, never a measurement).
python benchmarks/benchmark_runner.py --mode mock \
    --tasks HE-EDIT-001,HE-EDIT-006 --reps 1 --output-dir results/mock_smoke

# 4. Dry run: validate the configuration and print the schedule (no provider call).
python benchmarks/benchmark_runner.py --dry-run --mode live \
    --provider openrouter --model <pinned-model-id>

# 5. The real experiment — REQUIRES an explicit live command and a confirmation.
#    NOT executed during Phase 1.
python benchmarks/benchmark_runner.py --mode live \
    --provider openrouter --model <pinned-model-id> \
    --reps 5 --output-dir results/<experiment-name>
```

## 8. Explicit statements

*   **No live benchmark results exist.** Every summary produced so far comes
  from mock mode and is labelled `mode=mock`, `is_measurement=false`,
  `contains_mock_results=true`.
*   **No HumanEval score is reported.** The task set is synthetic and
  HumanEval-derived; the benchmark must never be presented as a HumanEval
  result.
*   **No Aider or Cline integration is claimed.** The three methods are this
    project's own editing strategies.
*   **Live benchmarking is not yet demonstrated to work against a real
    provider.** The provider configuration, the quota for the chosen model and
    the model's availability are still unverified, because verifying them
    would require a live request. The live path is implemented, fails closed
    and is unit-tested with fakes, but it has never contacted a gateway.
    Before the live run the operator should confirm: a credential for the
    chosen provider, a pinned model id that the gateway lists, and sufficient
    quota for 300 requests (plus the bounded retries).
