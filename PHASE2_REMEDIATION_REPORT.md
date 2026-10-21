# Phase 2 — Audit Remediation and Offline End-to-End Validation Report

**Project:** Hermes / ITMS code-editing benchmark
**Repository:** `/Users/noshinebnatadrita/Documents/499B`
**Date:** 2026-10-20
**Scope:** remediation of the five Major findings of `PHASE2_AUDIT_REPORT.md`,
the high-value Minor findings, and a full offline end-to-end validation.
**Phase 3 was not started. No real provider request was made. No credits were
consumed.**

---

## 0. Executive verdict

# PASS

All five Major findings (M1–M5) are fixed and covered by automated tests. The
high-value Minor findings are addressed. The full test suite passes with the
network blocked, the Phase 0 manifest verifies without mismatches, and the
300-attempt mock pipeline is internally consistent and explicitly labelled as
a non-measurement.

| Acceptance criterion | Result | Evidence |
|---|---|---|
| M1–M5 fixed and covered by tests | **yes** | 40 new tests in `benchmarks/tests/test_phase2_remediation.py`; suite 304 passed |
| Full test suite passes (network blocked) | **yes** | `304 passed, 23 subtests passed` |
| Phase 0 manifest verifies, no mismatches | **yes** | `manifest: OK (88 files)`, exit 0 |
| 300-attempt mock pipeline consistent, non-measurement | **yes** | 300 scheduled → 300 recorded; `is_measurement=false`; 300/300 `is_mock=true` |
| Remaining limitations documented honestly | **yes** | §7 and §9 |

**Important qualifier (unchanged from the audit):** the mock run is a
*pipeline and recording validation only*. It produced **zero** successful
repairs — by design, because the mock engine never produces a correct repair.
**No statement about method quality, model quality or comparative performance
can be derived from any artefact produced in this phase.**

---

## 1. The five Major findings and their exact fixes

### M1 — Token provenance: estimates are no longer labelled provider-reported

**Audit finding.** `model_dispatcher.py` decided `usage_source` from the mere
*presence* of a `usage` object, while the individual token fields fell back to
a local tiktoken estimate. A provider that returns `usage` without
`prompt_tokens`/`completion_tokens` therefore produced a record with
`usage_source="provider_reported"` and estimated numbers.

**Fix.** Provenance is now decided **per field**:

* `model_dispatcher.py` — `ModelResponse` gained
  `provider_prompt_tokens`, `provider_completion_tokens`,
  `provider_total_tokens`, `estimated_prompt_tokens` and
  `estimated_completion_tokens` (all optional, defaults `None`). The three
  call paths (`_call_openai_compatible`, `_call_gemini`, `_call_anthropic`)
  now populate them separately and set
  `usage_source` to `provider_reported` / `provider_partial` /
  `local_estimate`.
  `raw_tokens_in` / `output_tokens` keep their previous "best available int"
  semantics so the **application** behaviour is unchanged.
* `benchmarks/benchmark/provider.py` — `ProviderResult` maps only
  `provider_*` fields into `prompt_tokens` / `completion_tokens` /
  `total_tokens`; a missing provider field becomes `null`, and the local
  estimate travels in `estimated_prompt_tokens` / `estimated_completion_tokens`.
* `benchmarks/benchmark/records.py` — the attempt record gained
  `estimated_prompt_tokens` and `estimated_completion_tokens` as **separate,
  explicitly named columns**; `usage_source` is stored per attempt.
* `benchmarks/benchmark/analysis.py` — provider totals are summed **only** from
  provider-reported values, and the metric block now separates
  `provider_reported_attempts`, `provider_partial_attempts`,
  `local_estimate_only_attempts` and `missing_attempts`
  (missing = the provider reported nothing at all).
* Cost provenance is unchanged and remains honest: `cost_usd` is filled only
  when the provider response itself contains a numeric `cost`; otherwise it
  stays `null` with `cost_source=None`.

**Verified behaviour** (stubbed HTTP, no real request):

```
usage_source: provider_partial | prompt_tokens: None | completion_tokens: None | total_tokens: 42
local estimate kept separately: True True
cost: None None
```

**Tests** (`test_phase2_remediation.py::TestM1TokenProvenance`, 10 tests):
absent usage, empty usage object, `total_tokens`-only usage, partial token
fields, complete usage, missing cost, provider-reported cost, mock provider
reports nothing, analysis excludes estimates from provider totals, and the
record keeps the estimate in a separate field.

### M2 — The uncounted model-catalog request no longer repeats per attempt

**Audit finding.** `LiveProvider.complete` built a **new** `ModelDispatcher`
for every attempt. The OpenRouter model-support check
(`_assert_openrouter_model_supported` → `GET /models`) is cached per
dispatcher *instance*, so the cache never hit: up to 600 HTTP requests for an
advertised 300-call experiment.

**Fix.**

* `benchmarks/benchmark/provider.py` — the dispatcher is created **once** per
  provider (`_dispatcher_instance()`) and reused for every attempt, so the
  per-instance catalog cache is effective: the catalog is fetched **once per
  run**.
* Strict-live behaviour, the model-support validation, the bounded retry
  policy, the request timeout and the error classification are all unchanged.
* `benchmarks/benchmark_runner.py` — the live warning now states the honest
  request count: *"N live chat-completion requests"* plus *"the provider may
  additionally be asked once for its model catalog (one request per run, not
  per attempt)"*.

**Verified behaviour** (stubbed HTTP, 5 attempts on one provider):

```
M2 GET requests: 1 | POST requests: 5
```

**Tests** (`::TestM2SingleCatalogRequest`, 3 tests): the catalog is fetched
once for many attempts; the dispatcher instance is reused; the planned call
count stays equal to the number of chat requests.

### M3 — Provider and model identity are enforced (fail closed)

**Audit finding.** The declared `--provider` and the provider the dispatcher
actually resolved could differ, and the pinned model id was silently remapped
(`gpt-4o-mini` → `openai/gpt-4o-mini`) or hardcoded (gemini, anthropic).

**Fix** — `benchmarks/benchmark/provider.py::LiveProvider.preflight()` now
refuses to start a run unless:

1. the declared provider equals the provider the model id resolves to
   (`ModelDispatcher._resolve_provider`);
2. the model id that would actually be put on the wire
   (`ModelDispatcher._resolve_remote_model_id`) is **identical** to the
   requested id.

Both checks fail closed with a `configuration_error` and an actionable
message. `served_model_id` is only ever populated from the provider's own
response field (`data["model"]`); it is never invented, and it stays `null`
when the provider does not report it. `requested_model_id` and
`served_model_id` remain separate fields, so a mismatch is visible.

**Verified behaviour** (real CLI, no request issued):

```
--provider openai --model gpt-4o-mini
  → live preflight: FAILED — Provider 'openai' would send model id
    'openai/gpt-4o-mini' instead of the requested 'gpt-4o-mini'. Refusing to run…
  exit=2

--provider openrouter --model gemini-2.0-flash
  → live preflight: FAILED — Declared provider 'openrouter' does not match the
    provider that the model identifier 'gemini-2.0-flash' resolves to ('gemini').
  exit=2

--provider openrouter --model vendor/pinned-model
  → live preflight: OK (credentials and model id resolved)   exit=0
```

**Consequence (intended, and now a documented constraint):** a controlled
experiment on this harness can only use an **OpenRouter-style model id**
(`vendor/model`) on the `openrouter` provider. Other provider paths are
refused rather than silently changing the model.

**Tests** (`::TestM3ProviderAndModelIdentity`, 9 tests): provider mismatch,
OpenRouter-style id accepted and forwarded verbatim, gemini path refused,
anthropic path refused, unsupported provider/model combination refused,
dynamic alias still refused, served model id never invented, served model id
recorded when reported, requested-vs-served mismatch visible.

### M4 — The run metadata now describes the run completely

**Audit finding.** `run_metadata.json` omitted the generation parameters, so
a live run's artefacts did not document the configuration that produced them.

**Fix** — `benchmarks/benchmark/experiment.py::_write_metadata` now records:

`mode`, `declared_provider`, `effective_provider`, `is_measurement`,
`requested_model_id`, `served_model_id` (**null** — never fabricated before a
response exists), `temperature`, `max_output_tokens`,
`request_timeout_seconds`, `max_retries`, `retry_policy`,
`evaluation_timeout_seconds`, `reps`, `tasks`, `task_count`, `methods`,
`run_order_seed`, `bootstrap_seed`, `planned_attempts`,
`task_manifest_digest`, `python_version`, `platform`, `software_versions`,
`artifact_files` and explanatory `notes`.

* The values are read **from the provider object**, not hardcoded, so they
  cannot drift from what was actually used.
* The bootstrap seed is a single exported constant
  (`analysis.BOOTSTRAP_SEED = 20260101`) that is both used and recorded.
* The observed served model ids are reported in the summary
  (`observed_served_model_ids`) once responses exist.
* In mock mode the sampling parameters are recorded as `null` with an
  explicit note — the mock performs no inference, and no value is invented.

**Tests** (`::TestM4RunMetadata`, 5 tests): every required field present;
values correct; the metadata reflects the *actual* provider configuration
(temperature 0.35, 1234 tokens, 17 s, 1 retry) rather than a separate
hardcoded value; the full schedule is 300 attempts; observed served model ids
are reported in the summary.

### M5 — Retry counts survive a failure

**Audit finding.** `retry_count` was copied from the response only on success;
a request that retried twice and then failed was recorded as `retry_count=0`.

**Fix.**

* `model_dispatcher.py` — every raised failure now carries the number of
  retries already performed: `LiveModeError` gained a `retry_count`
  attribute; `QuotaExceededError` inherits it; the 402 / 429 / HTTP-status /
  transport paths set it explicitly; `_classify_live_error` copies it from
  the original exception.
* `benchmarks/benchmark/provider.py` — `ProviderError` gained `retry_count`,
  and the three error handlers copy it from the dispatcher exception.
* `benchmarks/benchmark/pipeline.py` — the provider-error path now writes
  `record.retry_count` instead of leaving the default `0`.
* The retry policy itself is unchanged and bounded: retries apply **only** to
  a transient HTTP 429; HTTP 402 (out of credits) and daily/per-minute quota
  caps are never retried. The count is never inferred from an error message.

**Tests** (`::TestM5RetryAccounting`, 7 tests): success without retries,
success after one retry (2 POSTs, `retry_count=1`), failure after two retries
(`retry_count=2`, 3 POSTs), daily quota not retried (1 POST, `retry_count=0`),
HTTP 402 not retried (1 POST, `retry_count=0`), timeout categorised as
`timeout`, and the pipeline recording `retry_count=2` for a failed attempt.

---

## 2. Minor findings addressed

| ID | Fix | Where | Test |
|---|---|---|---|
| m1 | A missing credential is now a `configuration_error`, not a generic `provider_error` | `model_dispatcher.py` raises `ModelConfigurationError` (a `RuntimeError` subclass, so existing callers are unaffected); `provider.py` maps it to `configuration_error` | `::TestMinorFindings::test_missing_credential_is_a_configuration_error`, `::test_dispatcher_configuration_error_maps_to_configuration_error` |
| m2 | The evaluation sandbox is documented as **best-effort isolation, not a security boundary**; filesystem and subprocess containment are stated as absent and the socket-guard bypass is stated explicitly | `docs/PHASE1_BENCHMARK_DESIGN.md` §6 (enforced vs. best-effort table), `docs/PHASE1_METRICS_AND_ANALYSIS.md` | documentation (empirically re-verified in the Phase 2A audit) |
| m3 | Truncation is recorded distinctly: `finish_reason == "length"` with an unusable response → `failure_category="truncated_output"` and `truncated_output=true` | `benchmarks/benchmark/pipeline.py`, `records.py` (`FailureCategory.TRUNCATED_OUTPUT`) | `::TestMinorFindings::test_truncated_output_is_recorded_distinctly` |
| m4 | `--dry-run` with an unknown method now prints a clear error and exits 2 instead of raising a traceback | `benchmarks/benchmark_runner.py` | `::TestMinorFindings::test_dry_run_rejects_unknown_methods_cleanly` |
| m5 | Two runs can no longer share a directory: the artefact paths are resolved **once at run start** and are run-scoped (`attempts-<run_id>.jsonl`, …) when a previous run already wrote there | `benchmarks/benchmark/experiment.py` | `::TestMinorFindings::test_two_runs_in_one_directory_do_not_mix_records` |
| m6 | JSONL, CSV, summary and metadata are kept internally consistent | `records.py` (new columns), `experiment.py` (paths recorded in the summary) | `::TestMinorFindings::test_jsonl_csv_summary_and_metadata_agree` |

### Minor findings deliberately **not** changed

| ID | Why |
|---|---|
| m7 (rewording the repair instruction to "The code below") | The instruction lives in `benchmarks/data/model_facing_tasks.json`, a **Phase 0 artefact covered by the manifest**. Rewording it would change a task definition and break the manifest checksum. Constraint 4 of this phase forbids that. The wording mismatch is therefore documented instead (see §7). |
| m8 (`validation_report.json` outside the manifest) | Same reason: `benchmarks/data/manifest.json` and `PROVENANCE.json` are Phase 0 artefacts. The provenance and manifest coverage of that file are documented in §7 of this report instead of by editing Phase 0 files. |
| m2 (sandbox hardening) | Out of scope by instruction: *"Do not expand this task into a redesign of the sandbox. Document its limitations accurately."* |

---

## 3. Files changed

| File | Change |
|---|---|
| `model_dispatcher.py` | **M1** per-field token provenance on `ModelResponse` and in all three call paths; **M5** `retry_count` carried on every raised failure and propagated by `_classify_live_error`; **m1** missing credential → `ModelConfigurationError`; helper `_as_int`. Backward compatible: every new field has a default, `raw_tokens_in`/`output_tokens` keep their previous semantics, and the app's own 144 tests still pass unchanged. |
| `benchmarks/benchmark/provider.py` | **M1** per-field usage mapping and separate estimate fields; **M2** one shared dispatcher per provider; **M3** provider/model identity enforcement in `preflight()`; **M5** `ProviderError.retry_count`; `ProviderResult` gained `estimated_*` and `effective_provider`. |
| `benchmarks/benchmark/pipeline.py` | **M5** records the retry count on provider failures; **M1** copies the separate estimate fields into the record; **m3** `truncated_output` classification. |
| `benchmarks/benchmark/records.py` | **M1** new record fields `estimated_prompt_tokens` / `estimated_completion_tokens` and `truncated_output`; **m3** new failure category `truncated_output`; new CSV columns. |
| `benchmarks/benchmark/analysis.py` | **M1** provider totals exclude estimates; `provider_partial_attempts`, `local_estimate_only_attempts`, corrected `missing_attempts`; summary counters for local estimates and truncations and observed served model ids; **M4** exported `BOOTSTRAP_SEED`. |
| `benchmarks/benchmark/experiment.py` | **M4** complete run metadata; **m5** run-scoped artefact paths resolved once per run; summary carries the artefact file list. |
| `benchmarks/benchmark_runner.py` | **m4** clean rejection of unknown methods; **M2** honest request-count wording; dry-run preflight now runs *before* the summary line. |
| `benchmarks/tests/test_phase2_remediation.py` | **new** — 40 tests covering M1–M5 and every Minor fix implemented. |
| `benchmarks/tests/test_phase1_provider.py` | **one fixture updated** (not weakened): the fake `ModelResponse` now sets the new `provider_*` token fields, so the test still asserts that provider-reported usage is kept. The assertion itself is unchanged in intent. |
| `docs/PHASE1_BENCHMARK_DESIGN.md` | **m2** enforced-vs-best-effort isolation table; live request-count wording. |
| `docs/PHASE1_METRICS_AND_ANALYSIS.md` | **M1** per-field token provenance; **m3** `truncated_output` category. |
| `PHASE2_REMEDIATION_REPORT.md` | **new** — this report. |

**Not modified (verified by modification time):** `surgical_splicer.py`,
`skeletonizer.py`, `sandbox.py`, `prompt_manager.py`, `config.py`,
`benchmark_harness.py`, `edit_orchestrator.py`, `relay_orchestrator.py`,
`app.py`, `telemetry.py`, `.env`, `env.example`, every existing test file, and
every file under `benchmarks/data/` and `benchmarks/phase0/`.

---

## 4. Commands executed and their actual exit statuses

| # | Command | Exit | Observed output |
|---|---|---|---|
| 1 | `PYTHONPATH=/tmp/offline_guard .venv/bin/python -m pytest -q` | **0** | `304 passed, 23 subtests passed in 33.29s` |
| 2 | `.venv/bin/python benchmarks/phase0/validate_tasks.py --verify-manifest` | **0** | `tasks: 20 / passed: 20 / failed: 0 / manifest: OK (88 files)` |
| 3 | `.venv/bin/python benchmarks/benchmark_runner.py --mode mock --reps 5 --seed 20260101 --output-dir /tmp/phase2_mock_300` | **0** | `recorded attempts : 300`, `mode : mock`, `WARNING : MOCK RUN — pipeline test only, not a measurement of model quality.` |
| 4 | `.venv/bin/python benchmarks/benchmark_runner.py --mode mock --reps 5 --seed 20260101 --output-dir /tmp/phase2_mock_300_b` | **0** | second run used to verify the deterministic order |
| 5 | `.venv/bin/python benchmarks/benchmark_runner.py --dry-run --mode live --provider openrouter --model vendor/pinned-model` | **0** | `live preflight: OK (credentials and model id resolved)` / `dry run: 300 attempts scheduled, no provider contacted.` |
| 6 | `.venv/bin/python benchmarks/benchmark_runner.py --dry-run --mode live --provider openai --model gpt-4o-mini` | **2** | `live preflight: FAILED — Provider 'openai' would send model id 'openai/gpt-4o-mini' instead of the requested 'gpt-4o-mini'.` |
| 7 | `.venv/bin/python benchmarks/benchmark_runner.py --dry-run --mode live --provider openrouter --model gemini-2.0-flash` | **2** | `live preflight: FAILED — Declared provider 'openrouter' does not match the provider that the model identifier 'gemini-2.0-flash' resolves to ('gemini').` |
| 8 | `.venv/bin/python benchmarks/benchmark_runner.py --dry-run --methods not_a_method` | **2** | `error: unknown editing method(s): not_a_method. Available methods: …` (no traceback) |

Per-suite results (each run individually, network blocked):

```
test_itms.py                              -> 17 passed
test_milestones_2_3.py                    -> 19 passed
test_openrouter.py                        -> 53 passed, 23 subtests passed
test_surgical_splicer.py                  -> 16 passed
test_benchmark_safety.py                  -> 22 passed
test_tdsc.py                               -> 17 passed
benchmarks/phase0/test_phase0_tasks.py    -> 29 passed
benchmarks/tests/test_phase1_provider.py  -> 25 passed
benchmarks/tests/test_phase1_adapters.py  -> 27 passed
benchmarks/tests/test_phase1_pipeline.py  -> 24 passed
benchmarks/tests/test_phase1_metrics.py   -> 15 passed
benchmarks/tests/test_phase2_remediation.py -> 40 passed
```

No existing test was weakened, skipped or deleted. The only edit to an
existing test is the fixture update described in §3.

---

## 5. Offline end-to-end validation (Part C)

Run: `--mode mock --reps 5 --seed 20260101 --output-dir /tmp/phase2_mock_300`
(fresh directory; nothing was overwritten).

| # | Check | Result |
|---|---|---|
| 1 | Scheduled attempts | **300** (20 tasks × 3 methods × 5 reps) |
| 2 | Recorded attempts | **300** — every scheduled attempt has a record, including failures |
| 3 | Run explicitly labelled mock | `summary.mode == "mock"`, `summary.is_measurement == false`, `summary.mock_warning` present |
| 4 | Every record `is_mock=true` | **300 / 300** |
| 5 | No mock usage/cost represented as provider-reported | `usage_source` values = `{"local_estimate_not_reported"}` for all 300; all `prompt_tokens`, `completion_tokens`, `total_tokens`, `cost_usd` are `null` |
| 6 | JSONL / CSV counts | 300 / 300 |
| 7 | Unique attempt ids | 300 / 300 (600 unique across two runs) |
| 8 | JSONL ↔ CSV field consistency (19 fields compared, null-aware) | **0 mismatches** |
| 9 | Denominators, recomputed independently | per method: scheduled 100, recorded 100, candidates 100; total 300 |
| 10 | Summary counts vs. raw records | `status_counts` = `{"repair_failed": 300}` — matches a manual recount |
| 11 | Task order deterministic for a fixed seed | two runs with `--seed 20260101` produced **identical** orderings |
| 12 | Coverage | 300 distinct (task, method, rep) triples — no duplicate, no omission |
| 13 | Paired comparisons | 3 method pairs, 20 paired tasks each, bootstrap over 20 tasks |
| 14 | Mock cannot fall back to a real provider | enforced by test: a mock run with `httpx.Client` replaced by a function that raises still completes normally, and live mode without a credential raises `configuration_error` with no request |

**Result of the mock experiment: 0 successful repairs out of 300.** This is
the expected and honest outcome: the mock engine deliberately returns a
response that leaves the defect in place, so it can only ever demonstrate
that the pipeline executes, records and analyses. It is **not** evidence that
any method is ineffective, and it is **not** a measurement of anything.

---

## 6. Verification of the specific remediation claims

| Claim | How verified | Result |
|---|---|---|
| Token provenance | stubbed responses with absent / empty / total-only / partial / complete usage | `provider_reported` only when the provider sent both fields; partial and estimate-only cases store `null` for the missing field and keep the estimate in a separate field |
| Cost provenance | stubbed response without `cost`, then with `cost` | `null` / `None` without cost; `0.0025` / `provider_reported` with cost |
| Catalog request not repeated | stubbed HTTP client counting GET/POST over 5 attempts | 1 GET, 5 POST |
| Requested vs served model identity | stubbed response with and without `model` | `requested_model_id="vendor/model"`, `served_model_id="vendor/model-20260101"`; `served_model_id=None` when the provider reports none; a differing served id stays visible |
| Provider/model identity enforcement | real CLI dry runs | exit 2 with an actionable message for `openai`/`gpt-4o-mini` and for `openrouter`/`gemini-2.0-flash` |
| Retry accounting | stubbed 429/402/timeout sequences | success→0, 1 retry→1, 2 retries then failure→2, daily quota→0 with 1 request, 402→0 with 1 request |
| Run metadata completeness | real mock run metadata file | all 22 required fields present, values read from the provider object |
| Phase 0 preservation | manifest verification + modification times | `manifest: OK (88 files)`; no Phase 0 file touched |

---

## 7. Remaining limitations and known issues

1. **The evaluation sandbox is best-effort, not a security boundary.**
   Enforced: scrubbed environment (no credentials), isolated working
   directory, no accidental project imports, wall-clock timeout, no injected
   convenience imports. **Not** enforced: filesystem containment (the
   candidate can read/write anywhere the process can, including the
   repository and `.env`), process containment (the candidate may spawn
   children), and the socket guard (a child interpreter with `PYTHONPATH`
   removed can open a socket — verified empirically in the Phase 2A audit).
   The benchmark assumes non-hostile model output.
2. **Live mode is now restricted to OpenRouter-style model ids on the
   `openrouter` provider.** That is the deliberate consequence of M3: a
   controlled experiment must not silently run a different model than the one
   recorded. Other provider paths fail closed with a clear message.
3. **The provider's own compliance with `temperature=0.2` and
   `max_tokens=2048` cannot be verified from the client side.** The values
   are sent on every request and recorded as *planned configuration*; the
   provider's acknowledgement is not available. This is documented in the
   design.
4. **Resumption of an interrupted live run is still refused.** The runner
   cannot prove which attempts already reached the provider. Completed
   attempts remain in `attempts.jsonl` for analysis; a new run needs a new
   directory (or `--allow-overwrite`, which now writes run-scoped files).
5. **The shared repair instruction still says "The Python module below"**,
   which is slightly inaccurate for the ITMS prompt (it shows a context
   skeleton). The instruction is part of the Phase 0, manifest-covered
   model-facing payload, so it was **not** changed in this phase. The
   requirement text is nevertheless identical for all three methods, so the
   comparison is unaffected.
6. **`benchmarks/data/validation_report.json` is outside the Phase 0
   manifest.** It is a *derived* output of `validate_tasks.py` (it embeds the
   manifest itself, so including it in the manifest would be circular). It is
   regenerated on demand and is reproducible from the command in
   `docs/PHASE0_TASK_VALIDATION_REPORT.md` §11. The authoritative Phase 0
   artefacts (task data, gold solutions, model-facing payload, mutation
   catalogue, build log, provenance, licence, vendored subset) **are** all
   covered by the manifest and were not modified.
7. **Statistics remain weak by construction**: 20 tasks, 5 repetitions, one
   model, one provider. The bootstrap interval is reported over tasks to make
   that visible, and no significance claim is made.
8. **The legacy `edit_orchestrator.py` still contains hardcoded latencies
   (1650/520/185 ms) and pre-written "simulated" model outputs.** It is not
   used by the benchmark and was not modified. It must never be reported as a
   measurement.
9. **Live provider capability is still unverified** (quota, model
   availability, gateway behaviour), because verifying it would require a
   real request. The live path is implemented, fail-closed and unit-tested
   with stubs, but it has never contacted a gateway.

---

## 8. Confirmations

* **No real provider request was made.** The only outbound traffic during
  this phase was the two GitHub dataset fetches performed during **Phase 0**.
  Every provider interaction in this phase used a stubbed `httpx.Client`
  (`benchmarks/tests/test_phase2_remediation.py::RecordingClient`). The
  session-wide socket guard in `benchmarks/tests/conftest.py` and the
  external `sitecustomize` block make any real connection fail loudly.
* **No credits were consumed or purchased**, and no quota or account setting
  was touched.
* **No package was installed.** The implementation uses only `httpx`,
  `pytest`, `python-dotenv` and `tiktoken`, all already present in `.venv`.
* **`.env` and the credentials were not modified, printed or copied into any
  artefact.** Only the *names* of environment variables were inspected, and
  error text passes through the secret-redaction filter before being stored.
* **Phase 0 was preserved**: task source files, gold solutions, official
  tests, the manifest, the mutation catalogue, the provenance file and the
  vendored subset are byte-identical (`manifest: OK (88 files)`, no
  mismatches). `surgical_splicer.py` and the documented ITMS nested-function
  limitation were not touched.
* **The three methods and the 300-attempt plan are unchanged**:
  `full_file_regeneration`, `search_replace_patch`, `itms_ast_splice`; 20
  tasks × 3 methods × 5 reps; temperature 0.2; `max_tokens` 2048.
* **Phase 3 was not started.** No live experiment was launched and no live
  confirmation was requested.

---

## 9. Next step

The remaining blockers for a live pilot are **operational, not code**:

1. choose a **pinned OpenRouter-style model id** that the gateway lists and
   that the account can afford;
2. confirm the credential and the quota for that model;
3. run a **3-attempt pilot** (one task × three methods) into a fresh output
   directory and inspect `attempts.jsonl` for
   `requested_model_id` vs `served_model_id`, `finish_reason`,
   `usage_source`, `cost_usd` and `is_mock=false`;
4. only then start the 300-attempt experiment.

No code change is required for those steps. A live pilot would still be the
first contact with a real gateway, so its first result must be treated as a
transport validation rather than a measurement.
