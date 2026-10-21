# Phase 2A — Independent Implementation Audit

**Project:** Hermes / ITMS code-editing benchmark
**Audited artefact:** Phase 1 implementation (runner, adapters, provider layer, metrics, tests)
**Audit date:** 2026-10-20
**Audit type:** independent source-level verification + offline execution
**Auditor constraint:** no code, configuration, credential or Phase 0 artefact was modified during this audit

---

## 0. Executive verdict

# PASS WITH LIMITATIONS

**The benchmark harness is functionally sound and honest about what it measures.**
All eight audit areas were checked against the source, and the central claims
of the Phase 1 implementation report hold up:

* the three editing methods are genuinely distinct and each is wired to the
  **real** project implementation (`TextDiffPatcher`, `skeletonize_code`,
  `ASTNodeLocator`, `ASTSurgicalSplicer`);
* all three were proven to independently reproduce the reference repair for
  **all 20 tasks**, and every candidate is graded by the **same** official
  test in the same isolated sandbox;
* the model-facing payload was scanned for every task × method combination:
  **no gold solution, no mutation metadata, no official test, no evaluation
  metadata and no cross-task identifiers** reach a prompt;
* the Phase 0 task set, manifest and official tests are intact and
  independently re-verified (gold passes, mutated fails, 88-file manifest
  verifies);
* live mode is fail-closed and cannot fall back to a mock;
* the record schema, denominators, incremental persistence and bootstrap
  analysis are consistent with the metrics document;
* **264 tests pass (23 subtests) with sockets blocked at the interpreter
  level.**

**However, five Major issues must be closed before any live pilot.** Three of
them sit in the provider layer and directly affect the honesty of the
recorded measurements:

| ID | Major issue |
|---|---|
| **M1** | A locally estimated token count can be stored as `usage_source="provider_reported"` |
| **M2** | Every attempt silently issues an **extra, uncounted HTTP request** to the provider's `/models` catalog (up to 600 requests for the advertised "300 calls") |
| **M3** | The pinned model id is **not honoured** for the `gemini`, `anthropic`, `openai` and `deepseek` provider paths, and the declared `--provider` may differ from the provider actually used |
| **M4** | The run metadata does **not** record `temperature`, `max_output_tokens`, request timeout, retry policy or the bootstrap seed — the fixed variables of the design are not captured in the artefacts |
| **M5** | A live attempt that fails after retries records `retry_count=0` — the retry accounting is lost on failure |

**No Critical issue was found.** The issues are, with one exception (M2),
confined to the provider/recording layer and do not affect the correctness of
the task set, the isolation of the evaluation, or the validity of the
paired task-level analysis.

**Is Phase 3 safe to begin?** **Not as a full live experiment.** M1–M4 should
be fixed first (each is a small, localised change). A *small* live pilot
(≤3 attempts) could be run today to validate the transport, but its token
metrics would not be trustworthy until M1 is fixed, and it would issue twice
as many HTTP requests as advertised until M2 is fixed.

---

## 1. Method integrity — **VERIFIED**

### 1.1 The three adapters are distinct treatments

| Adapter | Prompt contract | Application | Lines |
|---|---|---|---|
| `full_file_regeneration` | whole mutated file; exactly one fenced block | the block becomes the candidate module verbatim | `benchmarks/benchmark/adapters/full_file.py:64-114` |
| `search_replace_patch` | whole mutated file; exactly one `<<<<<<< SEARCH … >>>>>>> REPLACE` block | `surgical_splicer.TextDiffPatcher.apply_search_replace` | `benchmarks/benchmark/adapters/search_replace.py:96-161` |
| `itms_ast_splice` | `skeletonizer.skeletonize_code` context skeleton + target node | `surgical_splicer.ASTNodeLocator` + `ASTSurgicalSplicer.splice` | `benchmarks/benchmark/adapters/itms_splice.py:75-142` |

The ITMS adapter really does use the project code, not a re-implementation —
confirmed both by reading the imports
(`itms_splice.py:88-90`, `:100-111`) and by spy-based unit tests
(`benchmarks/tests/test_phase1_adapters.py:250-289`).

### 1.2 All three methods can solve the same task and are graded identically

Executed check (not a unit-test assertion): for **each of the 20 tasks**, the
gold repair was injected into each method's own output format and pushed
through the real adapter → evaluation path.

```
tasks where some method could not reproduce+pass the gold : none
tasks where the candidate differs (ast.dump) from gold     : none
```

That is: for all 60 method×task combinations, the adapter produced a candidate
that is **AST-identical to the reference solution** and **passes the official
test**. No method is handicapped by the harness, and the three treatments
differ only in how the edit is requested/applied — which is the intended
treatment.

### 1.3 Consistent evaluation

Every method ends in the same call:
`pipeline.py:155` → `pipeline.py:198-212` → `benchmarks/phase0/runner.py:118`
(`run_official_test`), which evaluates the **candidate** source with the
task's official `check(candidate)`. The reference solution is never passed to
the evaluator (`pipeline.py:155` passes `adapter_result.candidate_source`).

### 1.4 The known splicer limitation is untouched

`benchmarks/tests/test_phase1_adapters.py:291-320` re-derives the
documented mis-indentation and asserts it is still present.
`surgical_splicer.py` was not modified (mtime unchanged since Phase 0).

---

## 2. Prompt leakage — **VERIFIED (no leakage found)**

### 2.1 What the model actually receives

`benchmarks/benchmark/adapters/*.py` build the prompt from exactly three
fields of the model-facing task:

* `task.problem_prompt` (the original HumanEval prompt, i.e. imports +
  signature + docstring),
* `task.repair_instruction`,
* `task.mutated_source` (for ITMS: the skeletonised version of it).

No other task attribute is referenced by any adapter.

### 2.2 Leak scan over all 60 prompts (20 tasks × 3 methods)

| Probe | Result |
|---|---|
| full gold source file present | **0 hits** |
| the *repaired* form of the defective line present | **0 hits** |
| mutation rule name (e.g. `comparison_operator_swap`) | **0 hits** |
| mutation parameters (e.g. `{'from_op': '!=', …}`) | **0 hits** |
| official test source (`def check(candidate)`) | **0 hits** |
| `METADATA` block from the official test | **0 hits** |
| evaluation tokens (`checksums`, `sha256`, `mutation`, `gold_solutions`, `humaneval_edit_tasks`, `repair_success`, `HE-EDIT`) | **0 hits** |
| identifiers of other tasks | **0 hits** |

### 2.3 The package cannot reach the gold even by accident

`benchmarks/benchmark/tasks.py` opens exactly three files:
`model_facing_tasks.json` (`:98`), `tasks/<id>/mutated_source.py` (`:114`)
and `tasks/<id>/official_test.py` (`:116`). A grep of the whole benchmark
package for `gold`, `original_source`, `canonical_solution` and `mutation`
returns only a docstring and two unrelated keyword arguments
(`original_source=task.mutated_source`). The gold file is **not reachable**
from the runner.

### 2.4 Note (Informational)

`benchmarks/data/model_facing_tasks.json` also carries the `source` provenance
block (repository, revision, checksums). It is *not* put into any prompt
(verified above) — it is provenance metadata for the payload itself.

---

## 3. Task integrity — **VERIFIED**

### 3.1 Independent re-verification (not a re-run of the Phase 0 validator)

For all 20 tasks, the audit re-executed the official test from scratch:

```
integrity failures: []
all 20 gold pass & mutated fail: True
ids sorted & unique: True
```

and re-checked the SHA-256 of every official test and every mutated source
against the Phase 0 record (`humaneval_edit_tasks.json`) — no mismatches.

### 3.2 Manifest

```
python benchmarks/phase0/validate_tasks.py --verify-manifest
→ tasks: 20, passed: 20, failed: 0
→ manifest: OK (88 files)
```

No Phase 0 file was modified during this audit (see §8.3).

### 3.3 Equivalent task requirements across methods

All three methods receive the identical `problem_prompt` and
`repair_instruction` (verified per task). The only asymmetry is the ITMS
context skeleton (documented in the design, §4).

### 3.4 Clean workspace per attempt

`pipeline.py:92-94` creates a fresh `tempfile.mkdtemp()` **per attempt** and
`pipeline.py:177` removes it in a `finally` block. Verified by test
(`test_phase1_pipeline.py:196-217`) and by inspection.

*Informational:* the candidate is written into that workspace
(`pipeline.py:141-143`) but the executed copy is created by the Phase 0
runner in **its own** fresh temp directory (`runner.py:132-147`). Both are
per-attempt and disposable; the executed copy is the one that matters for
isolation.

---

## 4. Provider correctness — **PASS WITH MAJOR FINDINGS (M1, M2, M3, M5)**

### 4.1 What was traced

`benchmarks/benchmark/provider.py:296-311` →
`model_dispatcher.ModelDispatcher.generate_live` (`model_dispatcher.py:330-363`)
→ `ModelDispatcher.generate` (`model_dispatcher.py:366-425`) →
`_call_openai_compatible` (`model_dispatcher.py:626-720`).

| Property | Implementation | Verdict |
|---|---|---|
| strict live | `generate_live` refuses `force_mock=True` (`model_dispatcher.py:338-342`) and forces `strict_live=True` (`:351-363`) | **enforced** |
| no mock fallback | `generate()` raises before the mock branch in strict mode (`model_dispatcher.py:389-392`, `:419-423`); `LiveModeError`/`QuotaExceededError` are re-raised, never masked (`:406-417`) | **enforced** |
| fail-closed configuration | `provider.py:267-293` requires provider, pinned model id, credential; dynamic aliases refused | **enforced** |
| HTTP status handling | 402 → quota, 429 → quota (with daily-cap detection), `raise_for_status` → classified by `_classify_live_error` (401/403 → auth, ≥500 → provider, timeout → timeout, transport → network) (`model_dispatcher.py:84-117`, `:648-676`) | **enforced** |
| retry policy | bounded (`--max-retries`, default 2), only on transient 429, never on daily caps or 402 (`model_dispatcher.py:653-672`) | **enforced**, but see **M5** |
| timeouts | `httpx.Client(timeout=self.request_timeout or 35.0)` (`model_dispatcher.py:648`); CLI default 60 s | **enforced** |
| finish reason | `data["choices"][0].get("finish_reason")` (`model_dispatcher.py:678`) → `ModelResponse.finish_reason` → record | **enforced** |
| served model id | `data.get("model")` (`model_dispatcher.py:679`) → record `served_model_id` | **enforced** |
| usage | `data.get("usage")` (`:680`), mapped to the record | **defective — M1** |
| cost | only from `data.get("cost")` when numeric (`model_dispatcher.py:692-698`), otherwise `None` with `cost_source=None` | **honest** |
| request payload | exactly `model`, `messages`, `max_tokens`, `temperature` (verified by stub capture) — no seed, no invented parameters | **correct** |
| secrets | error text passes through `redact_secrets` (`provider.py:57-73`, applied at `provider.py:314-333` and `pipeline.py:103,125,151`); only the `Authorization` header name is ever constructed | **enforced** (no value printed in this audit) |

### 4.2 M1 (Major) — estimated tokens stored as provider-reported

`model_dispatcher.py:684-688`:

```python
raw_in   = usage.get("prompt_tokens", get_token_count(prompt))     # local tiktoken estimate as fallback
out_tokens = usage.get("completion_tokens", get_token_count(content))
usage_source = "provider_reported" if usage else "local_estimate"  # decided by the PRESENCE of usage
```

If a provider returns a `usage` object that lacks `prompt_tokens` /
`completion_tokens` (e.g. only `total_tokens`, or a nested cost object), the
**local estimate** is used while `usage_source` still says
`provider_reported`. Demonstrated with a stubbed HTTP response:

```
usage_source : provider_reported
raw_tokens_in : 151     (local tiktoken estimate — the provider never sent it)
output_tokens : 4       (local estimate)
benchmark record would store -> prompt_tokens: 151, usage_source: provider_reported
```

This contradicts the Phase 1 requirement *"Never represent an estimate as
provider-reported usage or cost"* and the metrics document §2.7.

### 4.3 M2 (Major) — one uncounted extra HTTP request per attempt

`model_dispatcher.py:400-402` calls `_assert_openrouter_model_supported(...)`
whenever the base URL is OpenRouter (which is the case for **every** provider
in this environment, because `OPENAI_BASE_URL=https://openrouter.ai/api/v1`).
That helper fetches the model catalog over the network
(`model_dispatcher.py:514-529` → `fetch_openrouter_models`, `:199-207`).

The cache is **per dispatcher instance** (`model_dispatcher.py:319`), and
`LiveProvider.complete` constructs a **new dispatcher on every attempt**
(`provider.py:298-303`). The cache therefore never hits:

```
cache is per-instance: True
LiveProvider.complete creates a new dispatcher per call: True
```

Consequence: a "300-call" experiment issues up to **600 HTTP requests**
(300 × `GET /models` + 300 × `POST /chat/completions`). The CLI prints
*"WARNING: this will make 300 live provider calls"*
(`benchmark_runner.py:218`), which understates the real request count and
understates quota/rate-limit consumption.

(Side effect, verified by reading the code: a failing catalog fetch is caught
and swallowed — `model_dispatcher.py:216-221` — so it does **not** fail the
attempt, but it can add up to 30 s of latency per attempt.)

### 4.4 M3 (Major) — the pinned model id is not always honoured

`LiveProvider` records `requested_model_id` from `--model`
(`provider.py:257`, `benchmark_runner.py:118`), but the dispatcher decides
the actual model:

| Declared provider / model | Model actually sent | Source |
|---|---|---|
| `openrouter` / `vendor/model` | `vendor/model` (verbatim ✓) | `model_dispatcher.py:391-394` |
| `openrouter` / `gpt-4o-mini` | `openai/gpt-4o-mini` | `model_dispatcher.py:396-408` |
| `openrouter` / `openrouter/free` | `openrouter/free` | `model_dispatcher.py:391-394` |
| `openai` / `my-custom-model` | **raises** `ModelConfigurationError` | `model_dispatcher.py:407-412` |
| `gemini` / anything | **hardcoded** `gemini-2.0-flash` / `gemini-1.5-pro` | `model_dispatcher.py:548` |
| `anthropic` / anything | **hardcoded** `claude-3-5-sonnet-20241022` | `model_dispatcher.py:737` |

So for the `gemini` and `anthropic` provider paths the recorded
`requested_model_id` is **not** the model that was called, and for
`openai`/`deepseek` the label is remapped. The truth is still recoverable from
`served_model_id`, but the artefact set is misleading and the design's
"same pinned model ID for every method" contract is not enforced by the code.

A second, related defect: the **declared** provider and the **effective**
provider can differ. `LiveProvider.preflight` validates the credential of the
declared provider (`provider.py:288`), while the dispatcher resolves the
provider from the *model id* (`model_dispatcher.py:381`). Example:
`--provider openrouter --model gpt-4o-mini` → preflight checks the OpenRouter
credential, the dispatcher uses provider `openai`. In this environment the
base URL is OpenRouter for both, so the request still succeeds — but the
preflight guarantee is about a different provider than the one that is called.

### 4.5 M5 (Major) — retry accounting lost on failure

`retry_count` is copied from the provider result only on the success path
(`pipeline.py:113`). On a provider error the pipeline returns at
`pipeline.py:100-106` and `retry_count` keeps its dataclass default of `0`
(`records.py:120`). An attempt that was retried twice and then failed for
quota is recorded as `retry_count=0`, so the effective request count is
under-reported in exactly the case where it matters.

### 4.6 m1 (Minor) — wrong category for a missing credential

`model_dispatcher.py:389-392` raises a bare `RuntimeError` when the
credential is missing. `provider.py:329-333` maps any non-`LiveModeError`
exception to `provider_error`, so this configuration problem is recorded as
a provider failure rather than a configuration error. (The `LiveProvider`
preflight catches the common case first, so this only triggers when the
*dispatcher-resolved* provider differs from the declared one — see M3.)

---

## 5. Execution safety — enforced vs. best-effort

Measured empirically by running a probe candidate through the real evaluation
sandbox (`benchmarks/phase0/runner.py`):

```
env_keys     : []                                   <- no credential-like variable present
env_all      : LANG, PATH, PYTHONDONTWRITEBYTECODE, PYTHONHASHSEED,
               PYTHONNOUSERSITE, PYTHONPATH, TMPDIR
cwd          : /private/var/.../itms_phase0_*/work  <- isolated working directory
project_import: ModuleNotFoundError                 <- project modules NOT importable
socket       : OSError                              <- sockets blocked
outside_read : ALLOWED                             <- filesystem read NOT restricted
outside_write: ALLOWED                             <- filesystem write NOT restricted
subprocess   : 'child ok'                          <- child processes allowed
```

### Enforced controls

| Control | Where | Evidence |
|---|---|---|
| scrubbed environment (no API keys/tokens reach the candidate) | `runner.py:85-94`, `:150` | probe: `env_keys == []` |
| isolated working directory per evaluation | `runner.py:132-147`, `:156` | probe: `cwd` is a fresh temp dir |
| no accidental imports from the project | `PYTHONPATH` replaced, `-s` (no user site) | probe: `ModuleNotFoundError` |
| wall-clock timeout, child killed | `runner.py:154-173` | `subprocess.run(timeout=…)` |
| deterministic hashing for the candidate | `runner.py:92` (`PYTHONHASHSEED=0`) | — |
| no injected convenience imports | `runner.py:134-149` writes only candidate + official test + `check(entry)` | probe: a candidate using `List` without importing it fails with `NameError` (test `test_phase1_pipeline.py:171-188`) |
| sockets blocked (direct) | `runner.py:22-55` (`sitecustomize`) | probe: `OSError` |

### Best-effort controls (must be described as such)

| Control | Reality |
|---|---|
| "network access is blocked" | **bypassable**: a candidate that spawns a child interpreter with `PYTHONPATH` removed can open a socket — **demonstrated**: `VERDICT: NETWORK-BYPASS` |
| filesystem containment | none: the candidate can read any file the process can read (including the repository and `.env`) and write anywhere writable |
| process containment | none: the candidate may spawn arbitrary children |
| secret protection in artefacts | the sandbox environment contains no credentials, and the candidate's stdout/stderr are **not** written into the attempt records — but the candidate could still *read* `.env` from the filesystem |

The Phase 1 implementation report §6 already states that the isolation is
"a best-effort sandbox … not a security boundary". The **design** document
§6 and the **metrics** document describe the socket block more strongly and
should be qualified (see §7, D3).

---

## 6. Metrics and records — **VERIFIED**

### 6.1 Denominators

`benchmarks/benchmark/analysis.py:96-135` computes and *reports* five
distinct denominators (`scheduled_attempts`, `recorded_attempts`,
`attempted_attempts`, `candidate_produced_attempts`, `tasks`). Recomputed by
hand on a 60-attempt mock run and compared with `summary.json`:

```
manual status counts : {'repair_failed': 60}
summary status counts: {'repair_failed': 60}
denominators         : scheduled=20, recorded=20, attempted=20, candidates=20, tasks=20, reps=1
```

Failed attempts are **not** dropped: 60 scheduled → 60 recorded.

### 6.2 Missing usage / cost

```
token_usage: provider_reported_attempts=0, missing_attempts=20, totals=None
cost       : provider_reported_attempts=0, missing_attempts=20, total_cost_usd=None
```

`None` is stored, never `0` (`analysis.py:145-186`; `records.py:120-135`).
For mock runs `usage_source="local_estimate_not_reported"` and every token
field is `null` — confirmed over the whole 60-record mock run.

### 6.3 JSONL / CSV consistency

```
jsonl records: 60   csv rows: 60
csv/jsonl field mismatches: 0   (over 16 compared fields)
distinct attempt_ids: 60 of 60
```

### 6.4 Malformed responses, provider failures, test failures, timeouts

All four are distinct, recorded outcomes:
`output_parse_error` / `provider_error` / `repair_failed`+`test_failed` /
`repair_failed`+`timed_out` (`records.py:12-33`, `pipeline.py:100-172`).
The provider-failure taxonomy is preserved end-to-end
(`auth_error`, `quota_exceeded`, `timeout`, `network_error`,
`configuration_error`, `provider_error`) — verified by
`test_phase1_provider.py:139-198`.

### 6.5 Bootstrap analysis

`analysis.py:64-88` — percentile bootstrap over **tasks** (not attempts),
2000 iterations, deterministic seed `20260101`. Verified deterministic
(`test_phase1_metrics.py:171-178`) and confirmed to resample tasks
(`paired_task_comparison`, `analysis.py:258-311`, uses per-task pass *rates*).

### 6.6 Mock contamination detection

`summarise_attempts` (`analysis.py:314-331`) sets `contains_mock_results`,
`is_measurement=false` and a `mock_warning`; every attempt record carries
`is_mock`. Verified on the 60-record mock run: `is_measurement=False`,
`mock_warning` present, all records `is_mock=true`, all usage/cost `null`.

---

## 7. Test validation — **VERIFIED**

Command actually run (network blocked at the interpreter level via an
external `sitecustomize`; the Phase 1 suite additionally blocks sockets in
`benchmarks/tests/conftest.py:36-56`):

```
PYTHONPATH=/tmp/offline_guard .venv/bin/python -m pytest -q
→ 264 passed, 23 subtests passed in 26.96s
```

Per-suite:

```
test_itms.py                        → 17 passed
test_milestones_2_3.py              → 19 passed
test_openrouter.py                  → 53 passed, 23 subtests
test_surgical_splicer.py            → 16 passed
test_benchmark_safety.py            → 22 passed
test_tdsc.py                         → 17 passed
benchmarks/phase0/test_phase0_tasks.py → 29 passed
benchmarks/tests/test_phase1_provider.py → 25 passed
benchmarks/tests/test_phase1_adapters.py → 27 passed
benchmarks/tests/test_phase1_pipeline.py → 24 passed
benchmarks/tests/test_phase1_metrics.py  → 15 passed
```

**No test weakened, skipped or modified.** The Phase 0 suite is byte-identical
and still green.

**Mock success is explicitly *not* treated as evidence of benchmark
effectiveness.** The mock engine deliberately never produces a correct repair
(`provider.py:236-260`), so a mock run can only ever produce
`repair_failed` / `output_parse_error`. The 60-attempt mock run produced
**0 repairs** and that is the expected, honest result. No method-comparison
claim can be derived from any artefact produced so far.

---

## 8. Reproducibility — **PASS WITH MAJOR FINDING (M4)**

### 8.1 Verified reproducible

| Element | Where recorded | Verified |
|---|---|---|
| pinned model id | `run_metadata.json:requested_model_id`, per-attempt `requested_model_id` + `served_model_id` | ✓ (but see M3) |
| temperature | sent on every request (payload verified by stub capture) | ✓ sent |
| output-token limit | `max_tokens=2048` in the payload | ✓ sent |
| repetition count | `run_metadata.json:reps` | ✓ |
| task list + count | `run_metadata.json:tasks`, `task_count` | ✓ |
| task-set digest | `run_metadata.json:task_manifest_digest` = `92077bdc…d08f1` | ✓ matches `TaskRepository.manifest_digest()` |
| run order | deterministic from `--seed` | ✓ two seeded mock runs produced **identical** orderings |
| random seed | `run_metadata.json:seed` | ✓ |
| software versions | `run_metadata.json:software_versions`, `python_version`, `platform` | ✓ |
| output directory | one directory per experiment; non-empty directory refused | ✓ |
| duplicate-run protection | fresh `run_id` per run (`experiment.py:150`), non-empty dir refused (`experiment.py:157-166`) | ✓ |

### 8.2 M4 (Major) — the fixed variables are not all in the artefacts

`experiment.py:208-237` writes the run metadata. It records the mode,
provider, requested model id, tasks, methods, reps, seed, planned attempts,
task digest, evaluation timeout and software versions — but **not**:

* `temperature` (0.2),
* `max_output_tokens` (2048),
* the provider request timeout (60 s),
* the retry policy (`max_retries` = 2),
* the bootstrap seed used for the confidence intervals (hardcoded at
  `analysis.py:72` and `analysis.py:259`),
* the resolved *effective* model id that the dispatcher would send.

The design document lists these as fixed experiment variables, so a live run's
artefacts do not fully document the configuration that produced them.

### 8.3 Provenance of the audit itself

* No code, documentation, configuration or Phase 0 artefact was modified
  during this audit (verified with a 25-minute modification-time window:
  empty).
* The Phase 0 manifest still verifies (88 files, 0 mismatches).
* `benchmarks/data/validation_report.json` (a *derived* Phase 0 output, not
  covered by the manifest) was regenerated during the **Phase 1**
  implementation at 23:09 — not during this audit.

---

## 9. Discrepancies between the implementation report and the actual code

| ID | Report claim | Actual code | Severity |
|---|---|---|---|
| D1 | "prints the planned number of provider calls" | prints 300; the real HTTP request count is up to 600 because of the per-attempt `/models` fetch (`model_dispatcher.py:400-402`, `provider.py:298-303`) | **Major (M2)** |
| D2 | "Never represent an estimate as provider-reported usage or cost" (report §"usage") | a local tiktoken estimate can be stored as `usage_source="provider_reported"` (`model_dispatcher.py:684-688`) | **Major (M1)** |
| D3 | "sockets blocked at the interpreter level via `sitecustomize`" (design §6) | true for the candidate process, **bypassable** through a child interpreter with `PYTHONPATH` removed (demonstrated) | Minor (m2) |
| D4 | "bounded retry policy … the number of retries actually performed is recorded per attempt" (design §2.2) | recorded only for attempts that *succeeded*; failed attempts report `retry_count=0` (`pipeline.py:100-113`) | **Major (M5)** |
| D5 | "Same pinned model ID for every method" (design §2) | honoured for OpenRouter-style ids only; `gemini`/`anthropic`/`openai`/`deepseek` remap or hardcode the model (`model_dispatcher.py:548`, `:737`, `:391-412`) | **Major (M3)** |
| D6 | "run metadata JSON recording configuration, task manifest digest, model identifier, and software version information" | accurate but incomplete: temperature, max-token limit, request timeout, retry policy and bootstrap seed are missing (`experiment.py:208-237`) | **Major (M4)** |
| D7 | "The evaluation sandbox … isolates the candidate" | true for env/cwd/imports/timeouts; **not** for filesystem or process containment (§5) | Minor (m2) |
| D8 | "each task starts from a clean isolated workspace" | true — a fresh temp dir per attempt (`pipeline.py:92-94`); the *executed* copy is created by the Phase 0 runner in its own fresh dir | Informational |

No discrepancy was found in the areas the report emphasises most: the task
set, the method wiring, the mock labelling, the fail-closed live mode, or the
test results.

---

## 10. Issue register

### Critical (0)
None.

### Major (5)

| ID | Issue | Location | Impact |
|---|---|---|---|
| **M1** | Local token estimate stored as `provider_reported` | `model_dispatcher.py:684-688` → `provider.py:315-330` → `pipeline.py:115-118` | corrupts the token-usage metric |
| **M2** | One uncounted `/models` HTTP request per attempt | `model_dispatcher.py:400-402`, `:514-529`; `provider.py:298-303` | doubles real API traffic; misstates the planned-call count and the quota impact |
| **M3** | Pinned model id not honoured on the gemini/anthropic/openai/deepseek paths; declared ≠ effective provider | `model_dispatcher.py:391-412`, `:548`, `:737`; `provider.py:288` | breaks the "same pinned model" contract; misleading `requested_model_id` |
| **M4** | Run metadata omits temperature, max tokens, request timeout, retry policy, bootstrap seed | `experiment.py:208-237` | incomplete reproducibility of a live run |
| **M5** | `retry_count` lost on failed attempts | `pipeline.py:100-113`; `records.py:120` | under-reported API usage in exactly the failure case |

### Minor (8)

| ID | Issue | Location |
|---|---|---|
| m1 | Missing credential in the dispatcher's strict path raises `RuntimeError` → categorised `provider_error` instead of `configuration_error` | `model_dispatcher.py:389-392`; `provider.py:329-333` |
| m2 | Socket block bypassable; filesystem and process containment absent | `runner.py:22-55` |
| m3 | No distinct failure category for truncated output (`finish_reason="length"` is recorded, but the attempt is reported as a parse error) | `records.py:12-33`; `full_file.py:82-88` |
| m4 | `--dry-run` with an unknown method raises an uncaught traceback (exit 1) instead of a clean error (exit 2) | `benchmark_runner.py:190` |
| m5 | A failed run leaves an empty output directory behind | `benchmark_runner.py:246-249` |
| m6 | With `--allow-overwrite`, `attempts.jsonl` / `attempts.csv` are appended to while `summary.json` is overwritten → one directory can hold two runs | `records.py:163-205`; `experiment.py:157-166` |
| m7 | The repair instruction says "The Python module below", which does not match the ITMS prompt (context skeleton) | `benchmarks/data/model_facing_tasks.json` → `repair_instruction` |
| m8 | `benchmarks/data/validation_report.json` is a derived Phase 0 output outside the manifest; it was regenerated during Phase 1 | `benchmarks/data/manifest.json:files` |

### Informational (6)

| ID | Note |
|---|---|
| I1 | The ITMS treatment sees an `ast.unparse`-reformatted skeleton, so the raw text differs from the file given to the other two methods (semantically identical) |
| I2 | The provider payload carries only `model`, `messages`, `max_tokens`, `temperature`; the seed affects ordering only (documented in the design) |
| I3 | The pipeline's workspace receives `candidate.py`, but the executed copy is created by the Phase 0 runner in its own temp dir |
| I4 | A single shared dispatcher instance would remove the M2 extra request |
| I5 | Live provider capability (quota, model availability, gateway behaviour) remains **unverified** — no request was made in Phase 0 or Phase 1 |
| I6 | `_call_gemini` passes the API key in the URL query string (`model_dispatcher.py:550`) — pre-existing, not exercised, but it is a credential-handling smell worth noting |

---

## 11. Checklist before the live pilot

Ordered by blocking severity. Every item is a small, localised change.

**Must fix (blocking a live pilot)**

1. **[M1]** Make token provenance honest: set `usage_source` per *field*, not per response — e.g. only report `prompt_tokens`/`completion_tokens` when the provider actually sent those keys; otherwise store `null` and mark the estimate separately (`model_dispatcher.py:684-688`).
2. **[M2]** Eliminate the per-attempt catalog fetch: reuse one `ModelDispatcher` per run (cache the instance in `LiveProvider`, `provider.py:298-303`) **or** hoist the support check out of the per-request path (`model_dispatcher.py:400-402`).
3. **[M3]** Pin the effective model: (a) restrict live mode to provider paths that forward the model id verbatim (OpenRouter-style `vendor/model`), or (b) record the *resolved* model id as `requested_model_id` and reject the gemini/anthropic/openai/deepseek remapping paths for a controlled experiment (`model_dispatcher.py:391-412`, `:548`, `:737`); also make `LiveProvider.preflight` validate the credential of the **dispatcher-resolved** provider, not only the declared one (`provider.py:288`).
4. **[M4]** Extend `run_metadata.json` with `temperature`, `max_output_tokens`, `request_timeout`, `max_retries`, the bootstrap seed and the resolved model id (`experiment.py:208-237`).
5. **[M5]** Carry the retry count into the failure path so failed attempts report the retries actually performed (`pipeline.py:100-113`).

**Should fix before the pilot (low risk, high value)**

6. **[m1]** Map the dispatcher's missing-credential `RuntimeError` to `configuration_error` (`provider.py:329-333`).
7. **[m2]** Qualify the socket block as best-effort in `docs/PHASE1_BENCHMARK_DESIGN.md` §6 and `docs/PHASE1_METRICS_AND_ANALYSIS.md`; state explicitly that filesystem and process containment are absent.
8. **[m3]** Add a `truncated_output` failure category driven by `finish_reason == "length"`.
9. **[m4]** Handle an unknown `--methods` value cleanly in `--dry-run` (`benchmark_runner.py:190`).
10. **[m6]** Either refuse `--allow-overwrite` for an existing run directory or start a new file set per run, so a directory never mixes two runs.

**Optional**

11. **[m7]** Reword the shared repair instruction so it fits all three prompts (e.g. "the code below"), keeping the requirement text identical across methods.
12. **[m8]** Add `validation_report.json` to the Phase 0 manifest, or state that it is a derived artefact outside manifest coverage.

**Pre-pilot operational checklist (no code change)**

13. Confirm a credential exists for the chosen provider and that the **pinned** model id is listed by that gateway.
14. Confirm the quota covers the true request count — 300 chat requests **plus** the M2 catalog requests until item 2 is fixed.
15. Run a **3-attempt** live pilot (one task × 3 methods) into a fresh output directory and inspect `attempts.jsonl` for: `requested_model_id` vs `served_model_id`, `finish_reason`, `usage_source`, `cost_usd`, and the absence of `is_mock`.
16. Re-run the full suite and the Phase 0 validator after each code change.

---

## 12. Is Phase 3 safe to begin?

**No — not as the full live experiment.**

*   **Safe today:** the task set, the three editing treatments, the evaluation
    sandbox, the record schema, the metrics and the analysis are verified and
    need no rework. The Phase 0 data set is intact.
*   **Blocked:** M1–M4 must be closed first. M1 corrupts the token metric, M2
    doubles the real API traffic relative to what the operator is told, M3 can
    silently serve a different model than the one recorded, and M4 leaves the
    live artefacts without a complete configuration record.
*   **Conditionally acceptable:** a ≤3-attempt live pilot purely to validate
    the transport would be safe *today*, provided its token figures are treated
    as unverified (M1) and the extra catalog requests are expected (M2). It
    must not be reported as a benchmark result.
*   **The 300-attempt experiment must not start** until items 1–5 of §11 are
    implemented, the suite is re-run green, and the Phase 0 manifest still
    verifies.

---

## 13. Audit commands and observed results

```bash
# Phase 0 integrity (88 files, 0 mismatches)
python benchmarks/phase0/validate_tasks.py --verify-manifest
#   → tasks: 20 / passed: 20 / failed: 0 / manifest: OK (88 files)

# Full test suite with the network blocked at the interpreter level
PYTHONPATH=/tmp/offline_guard .venv/bin/python -m pytest -q
#   → 264 passed, 23 subtests passed in 26.96s

# Mock end-to-end (pipeline verification only; 60 attempts)
python benchmarks/benchmark_runner.py --mode mock --reps 1 --seed 42 --output-dir /tmp/audit_mock_a
#   → 60/60 recorded, all is_mock=true, all usage/cost null,
#     summary is_measurement=false, mock_warning present

# Live mode without confirmation must abort before any request
OPENROUTER_API_KEY=fake-test-key python benchmarks/benchmark_runner.py \
    --mode live --provider openrouter --model vendor/pinned-model \
    --reps 1 --tasks HE-EDIT-001 --output-dir /tmp/audit_live_should_not_exist < /dev/null
#   → "no interactive confirmation available; refusing to run." exit=2,
#     output directory not created

# Independent re-verification of the task set (gold passes, mutated fails)
#   → integrity failures: []   (all 20 tasks)

# Method-equivalence probe (gold injected into each method's output format)
#   → tasks where some method could not reproduce+pass the gold: none
#   → tasks where the candidate differs (ast.dump) from gold: none

# Prompt-leak scan over all 60 prompts
#   → LEAKS FOUND: none

# Sandbox capability probe (executed inside the real evaluation sandbox)
#   → env_keys: []   project_import: ModuleNotFoundError   socket: OSError
#   → outside_read: ALLOWED   outside_write: ALLOWED   subprocess: allowed
#   → child-interpreter socket bypass: VERDICT: NETWORK-BYPASS
```

**Audit network activity:** the only outbound traffic in this audit was the
two GitHub dataset fetches performed during **Phase 0** (already documented).
During this audit **no HTTP request was issued by any code under audit**;
the provider-layer probes used stubbed `httpx` clients, and the live-mode
probe aborted before the request stage. No credits were consumed and no
package was installed.
