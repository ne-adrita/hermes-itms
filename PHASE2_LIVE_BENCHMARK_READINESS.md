# Phase 2 Live Benchmark Readiness

**Project:** `/Users/noshinebnatadrita/Documents/499B` (Hermes / ITMS capstone)
**Audit date:** 2026-10-22
**Scope:** preparation of the **Phase 2 tri-way editing benchmark** (Full-File Rewrite vs Text-Based Search/Replace vs ITMS AST-Guided Surgical Splicing) for a real, provider-backed experiment.
**Audit constraint:** no paid API call, no live benchmark launch, no source change, no modification of historical results, no modification of `.env`, no secret printed. Everything below was verified by reading code and by running **safe, local, offline** commands.

---

## 0. Headline verdict

| Question | Answer |
| --- | --- |
| Does a real provider-backed tri-way execution path exist in the code? | **YES** — `benchmarks/benchmark_runner.py` + `benchmarks/benchmark/{provider,pipeline,experiment,adapters}` implement it end to end. |
| Has it ever been executed against a real provider? | **YES** — a **120-attempt live run** exists (`pilot_runs/2026-10-21_minimax-m3-1-flash-free_120_attempts/`), plus a 3-attempt live pilot (`results/pilot_HE-EDIT-006_openrouter-free_20261021T062934Z/`). |
| Is the Phase 2 dashboard tab wired to that real data? | **NO** — `app.py`'s *"Surgical Code Modification (Phase 2)"* tab is a **simulation** on user-supplied candidate code. A separate read-only tab (*"Completed Benchmark Results"*) does show the real 120-attempt dataset. |
| Is the experiment **ready to be launched and published as a Phase 2 result**? | **CONDITIONAL GO for a 9-attempt live pilot; NO-GO for the 300-attempt "claim" run** until the 6 blocking items in §2 are closed. The blockers are *scientific* (task set, output-cap confound, no request-level seed), not plumbing. |
| **Update after the B1–B6 closure pass (§13)** | **B1, B2, B3, B4, B5 are CLOSED with implementation and offline test evidence. B6 is METHOD-ONLY: the pre-registered selection rule is delivered and tested, but 0 of the current 20 tasks qualify, so the representative task family is still missing. The live path is therefore ready for the 9-request pilot; the 300-attempt claim run remains NO-GO.** |

**One-line summary:** the *mechanics* of a live tri-way Phase 2 benchmark are implemented, exercised and audited; the *experiment* is not yet publishable, because on the current 20-task set the three methods are statistically indistinguishable on correctness (100 % evaluator pass rate for all three) and the ITMS token advantage that the Phase 2 thesis rests on **does not exist on this dataset** (measured offline: the target function is ~96 % of every file).

---

## 1. What was inspected

| Area | Files read |
| --- | --- |
| Phase 2 simulation path | `edit_orchestrator.py`, `app.py` (tabs 1, 5, 6), `sandbox.py`, `surgical_splicer.py`, `skeletonizer.py` |
| Live tri-way harness | `benchmarks/benchmark_runner.py`, `benchmarks/benchmark/{__init__,provider,pipeline,experiment,records,artifacts,analysis,tasks}.py`, `benchmarks/benchmark/adapters/{__init__,base,full_file,search_replace,itms_splice}.py` |
| Evaluator / isolation | `benchmarks/phase0/runner.py` (`run_official_test`), `benchmarks/phase0/{validate_tasks,eligibility,build_tasks,write_report}.py` |
| Provider layer | `model_dispatcher.py` (`generate`, `generate_live`, `_resolve_remote_model_id`, `has_live_provider_key`), `config.py` (`AppConfig`) |
| Historical harnesses (read-only) | `experiments/itms_aider_cline_v1/{runner,integrity,analyze,config}.py`, `experiments/itms_aider_cline_v1/experiment_config.json` |
| Viewers / dashboard | `comparative_viewer.py`, `completed_benchmark_viewer.py`, `pilot_runs/analysis_2026-10-21_.../analyze_pilot.py` |
| Historical results (read-only) | `pilot_runs/**`, `results/pilot_HE-EDIT-006_.../**`, `experiments/itms_aider_cline_v1/runs/**` |
| Tests | `benchmarks/tests/*`, `benchmarks/phase0/test_phase0_tasks.py`, `test_itms_phase2_e2e.py`, `test_surgical_splicer.py` |

---

## 2. Trace of the three strategies (request → candidate → edit → evaluation → persistence)

All three strategies run through **one** pipeline, so the only differences are the prompt contract and the output contract:

```
benchmarks/benchmark_runner.py  (CLI: --mode mock|live, --tasks, --reps, --seed, --temperature,
                                 --max-output-tokens, --request-timeout, --evaluation-timeout,
                                 --max-retries, --dry-run, --yes)
      │
      ├─ TaskRepository()                    ← 20 approved tasks, checksum-verified, manifest digest pinned
      ├─ build_schedule(tasks, methods, reps, seed)   ← deterministic order (seeded shuffle)
      ├─ LiveProvider.preflight()            ← OFFLINE checks only: provider, pinned model id, credential
      └─ ExperimentRunner.run()
            └─ per attempt: AttemptPipeline.run()
                  1. adapter.build_prompt(task)         ← the ONLY method-specific step before the call
                  2. provider.complete(prompt)          ← exactly ONE chat-completions request
                  3. adapter.parse_and_apply(task, rep, response_text)   ← method-specific application
                  4. adapter._syntax_error(candidate)   ← AST parse of the candidate
                  5. run_official_test(candidate, official_test, target)  ← THE evaluator, identical for all 3
                  6. AttemptRecord (+ artefacts)         ← persisted to JSONL/CSV + artefacts/<id>/
```

### 2.1 Stage-by-stage: what is real, what is simulated

| Stage | Full-File Rewrite | Search/Replace | ITMS AST Splice | Real / simulated? |
| --- | --- | --- | --- | --- |
| Prompt build | `adapters/full_file.py::build_prompt` — full mutated source | `adapters/search_replace.py::build_prompt` — full mutated source | `adapters/itms_splice.py::build_prompt` — **skeleton** (`skeletonize_code`) + target name | **Real.** Different context by design (ITMS is the token-minimising treatment). Verified today: the target function's **AST is byte-identical** in the skeleton for **20/20** tasks, so the ITMS method sees exactly the same defect. |
| Provider call | `LiveProvider.complete()` → `ModelDispatcher.generate_live()` | same | same | **Real** in `--mode live`; `MockProvider` in `--mode mock`. `generate_live` cannot fall back to mock (`force_mock` refused, `strict_live=True`). |
| Response parsing | exactly one fenced block; >1 block → `multiple_code_blocks` | exactly one `<<<<<<< SEARCH … >>>>>>> REPLACE` block | one fenced block, extracted by `clean_replacement_code` | **Real**, strict output contracts. |
| Edit application | candidate **is** the file (verbatim) | `TextDiffPatcher.apply_search_replace` (exact-match, ambiguity-checked) | `ASTNodeLocator.locate_target` → `ASTSurgicalSplicer.splice` (line-boundary splice) | **Real.** The three application mechanisms are genuinely different. |
| Syntax validation | `ast.parse` | `ast.parse` | `ast.parse` (in `splice()` **and** in the pipeline) | **Real**, identical check. |
| Evaluation | `benchmarks/phase0/runner.py::run_official_test` — `check(target)` in a temp dir, scrubbed env, socket guard, timeout | same | same | **Real** and **identical** for all three; the official test is never in any prompt. |
| Persistence | `AttemptRecord` (46 fields) → `attempts.jsonl` + `attempts.csv`; per attempt: `artifacts/<id>/{attempt.json,candidate.py,raw_response.json}` | same | same | **Real.** Artifact persistence failure demotes the attempt to `incomplete` — a missing artefact can never be reported as a pass. |

### 2.2 Everything that is currently simulated, mocked, hard-coded or disconnected

| # | Finding | Where | Impact on a live Phase 2 run |
| --- | --- | --- | --- |
| S1 | The **Phase 2 dashboard tab is a simulation**: `SurgicalEditOrchestrator.run_benchmark()` takes caller-supplied candidate code and never calls a provider. | `edit_orchestrator.py` (whole file), `app.py:895-1010` | The dashboard's Phase 2 numbers are **not** benchmark results. The *real* 120-attempt dataset is shown in a different tab (*"Completed Benchmark Results"*). The two must be reconciled before publication. |
| S2 | The live runner has **no request-level seed**. `ModelDispatcher.generate_live()` accepts `seed=`, but `LiveProvider.complete()` never passes it; `AttemptRecord` has no `seed_*` fields (verified: the 120-run records contain **no** seed field). | `benchmarks/benchmark/provider.py:447-517`; `benchmarks/benchmark/records.py:110-171` | Repetitions are unseeded samples ⇒ **not reproducible** at the request level (only the run *order* is seeded, `run_order_seed`). The 300-attempt run inherits this. |
| S3 | The live runner has **no spending gate**. `benchmarks/benchmark/experiment.py` contains no budget/spend logic, and the CLI has no `--max-spend-usd`. (The *other* harness, `experiments/itms_aider_cline_v1/runner.py:192-233`, *does* implement `check_budget_preflight` + `max_spend_usd` + `require_free_model`.) | `benchmarks/benchmark_runner.py`, `benchmarks/benchmark/experiment.py` | An accidental `--tasks`/`--reps` typo, or a model change to a paid slug, has **no** automatic stop. **Blocking.** |
| S4 | **The prompt is never persisted.** `artifacts/<id>/raw_response.json` stores the response, but the *request* is not stored (no prompt text, no prompt hash, no prompt token count per attempt). | `benchmarks/benchmark/artifacts.py:159-240` | A reviewer cannot prove from the artefacts that the three methods received *equivalent* instructions, nor audit the exact context each method saw. **Blocking for auditability.** |
| S5 | **No analysis/figure generator inside the benchmark package.** `benchmarks/benchmark/analysis.py` computes metrics but has no entry point and no plotting; the figures for the 120-run were produced by `pilot_runs/analysis_2026-10-21_.../analyze_pilot.py` (a script living inside a *historical* results directory). | `benchmarks/benchmark/analysis.py`; `pilot_runs/analysis_.../analyze_pilot.py` | A new run has no supported, version-controlled path from `attempts.jsonl` → `analysis.json` + figures. **Blocking.** |
| S6 | The **output-token cap is a confound**. `max_output_tokens` defaults to **2048** and is shared by all three methods. In the 120-run, 6 attempts were `truncated_output` — **5 of them in `itms_ast_splice`** (which had the *highest* mean token use, 1412). | `benchmarks/benchmark_runner.py:100-103`; `pilot_runs/.../RESULTS_SUMMARY.md:32-33` | The completion-rate comparison against ITMS is partly an artefact of the harness's token budget, not of edit quality. **Blocking for a claim.** |
| S7 | **Ceiling effect.** Evaluator pass rate was **100 % for all three methods** (every attempt that reached the official test passed). | `pilot_runs/.../RESULTS_SUMMARY.md:32,40-44` | The run cannot discriminate repair *quality*; only completion rate, latency and tokens differ. **Blocking for a quality claim.** |
| S8 | **The task set cannot demonstrate the Phase 2 thesis.** Measured offline today with the local tokenizer: mean prompt tokens — full-file **428.6**, search/replace **445.6**, ITMS **450.4**; mean *output* the method must emit — whole file **139.9** tokens vs target-function-only **135.2** tokens. The target function is ~96 % of every file, so "surgical" editing has almost nothing to save. | offline measurement over all 20 tasks (commands in §7) | The report's "94 % output-token reduction / 8× faster" claim **cannot** be reproduced on this dataset. The live 120-run agrees: ITMS mean tokens **1412** > full-file **1092**. A different task set is required for a token claim. **Blocking for a token claim.** |
| S9 | The **dashboard harness is weaker than the benchmark harness**. `sandbox.py::ExecutionHarness` allows `PYTHONPATH` through into the child, does **not** block sockets, and injects convenience imports (which can mask a missing import). The benchmark evaluator (`benchmarks/phase0/runner.py`) does not have these weaknesses. | `sandbox.py:53-72, 84-260` vs `benchmarks/phase0/runner.py:29-96` | The benchmark path is the stronger one and is the one to use; the dashboard harness must never be used to grade a measured attempt. |
| S10 | `run_metadata.json` records `served_model_id: null` (it is only known after the first response) and no `max_spend_usd`. | `benchmarks/benchmark/experiment.py::_write_metadata` | Minor: served ids must be aggregated from the records (the summary already does this: `observed_served_model_ids`). |

### 2.3 What is genuinely real and already proven

| Evidence | Facts |
| --- | --- |
| 120-attempt live tri-way run (`pilot_runs/2026-10-21_minimax-m3-1-flash-free_120_attempts/`) | mode `live`; `is_measurement: true`; `contains_mock_results: false`; 120/120 attempts recorded; 114 `repair_success`, 6 `truncated_output`; **provider-reported usage on 120/120**; **cost reported on 0/120**; single served model `minimax/minimax-m3.1-flash:free` for **120/120** (served == requested); `artifacts_status = ok` for 120/120 with `candidate.py` + `raw_response.json` on disk and candidate hashes matching; `sum(retry_count) = 0`; wall clock 768.8 s. |
| 3-attempt live pilot (`results/pilot_HE-EDIT-006_openrouter-free_20261021T062934Z/`) | 4 HTTP requests (1 catalog + 3 chat), interactive confirmation used, `--yes` not used; 1/3 repaired and passed the official test; the two failures were correctly classified (`no_code_block`, `truncated_output`); the pilot's own report documents that the **dynamic alias served two different models**, so that pilot supports no method comparison. |
| Method-equivalence probe run **today** (offline) | Injecting the gold solution into **each method's own output format** → parse+apply+official test: **full-file 20/20, search/replace 20/20, ITMS splice 20/20**. With the *current* (post-fix) splicer, no method is disadvantaged by a parser/applier bug. |
| Mock-mode run executed **today** into a temp dir (no network) | 6 attempts (2 tasks × 3 methods) → all three strategies executed, and each attempt wrote `attempt.json`, `candidate.py`, `raw_response.json`. This **closes the pilot's auditability gap** (its §8.4: "the candidate source and the raw provider responses are not persisted"), which was a code gap at pilot time and is now fixed in `artifacts.py`. |
| Phase 0 integrity | `benchmarks/phase0/validate_tasks.py --verify-manifest` → 20/20 tasks, manifest OK (88 files). |
| Test suite (unchanged by this audit) | `pytest -q` → **471 passed, 23 subtests**; `pytest benchmarks/tests benchmarks/phase0 test_itms_phase2_e2e.py -q` → **254 passed**. |

---

## 3. Provider configuration (verified offline; no secret read, printed or transmitted)

Verified with `config.AppConfig` and `LiveProvider.preflight()` — **no network request was issued and no credential value was printed**.

| Check | Result |
| --- | --- |
| Credential for `openrouter` | **present** (73 chars). Value, prefix and suffix **not** printed. |
| Credential for `openai` / `deepseek` | **present, and the same value as the OpenRouter credential** (compared in memory; no value, length, hash or fingerprint is produced or stored). |
| Credential for `gemini` / `anthropic` | not configured. |
| Base URL for `openrouter` | `https://openrouter.ai/api/v1` (the default; no override in the environment). |
| `LiveProvider.preflight()` with `minimax/minimax-m3.1-flash:free` | **OK** — provider/model agreement verified, model id forwarded verbatim, credential present. `preflight()` performs **no** network call. |
| `LiveProvider.preflight()` with `openrouter/free` | **REFUSED** — dynamic alias, refused unless `--allow-dynamic-alias` is passed. This is the correct control (the earlier 3-attempt pilot was invalidated by it). |
| `.env` structure | 9 defined variables (`GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, `OPENROUTER_API_KEY`, `OPENAI_BASE_URL`, `EXECUTION_TIMEOUT_SECONDS`, `DEFAULT_TIER1_MODEL`, `DEFAULT_TIER2_MODEL`) **plus one bare line with no `KEY=` assignment** — a live secret that the loaders ignore. **Not modified by this audit** (flagged as an outstanding operator action: delete that line and rotate the credential with its issuer). |

> **Configuration risk to fix before a live run:** the *same* credential is configured for `openrouter`, `openai` and `deepseek`. The Phase 2 runner is safe as long as `--provider openrouter` is used with an explicit `vendor/model` id (the preflight enforces provider/model agreement), but a wrong `--provider` could send the OpenRouter key to a different vendor. Recommended: remove `OPENAI_API_KEY`/`DEEPSEEK_API_KEY` from `.env`, or set them to the correct vendor keys, **before** the live run. (Requires operator action on `.env`; not performed here.)

---

## 4. Missing implementation items, by file/function

### Blocking (must be closed before the 300-attempt claim run)

| # | File / function | Missing item | Suggested (minimal) change |
| --- | --- | --- | --- |
| B1 | `benchmarks/benchmark/provider.py::LiveProvider.complete` | request-level `seed` is never sent and never recorded | add a `seed` parameter, pass it to `generate_live(seed=…)`, and record `seed_requested` / `seed_supported` / `seed_applied` on the record (mirror `experiments/itms_aider_cline_v1/systems/base.py`) |
| B2 | `benchmarks/benchmark/experiment.py::ExperimentRunner.run` + `benchmarks/benchmark_runner.py` | no spending gate | port `check_budget_preflight()` / `max_spend_usd` / `require_free_model` from `experiments/itms_aider_cline_v1/runner.py:192-233`; add `--max-spend-usd` and `--require-free-model` flags; abort further calls once the cumulative *reported* cost reaches the cap, and record the cells that were not executed |
| B3 | `benchmarks/benchmark/artifacts.py::ArtifactStore.persist` | the request (prompt) is never stored | persist `prompt.txt` + `prompt_sha256` + `prompt_token_estimate` per attempt (redacted like the response) so the "equivalent instruction" claim is auditable |
| B4 | `benchmarks/benchmark/analysis.py` (+ a new CLI) | no version-controlled path from a run dir → `analysis.json` + figures | add `benchmarks/benchmark/figures.py` + `benchmarks/benchmark_runner.py --analyze <run-dir>` (or a sibling `analyze_runner.py`), migrating the logic of `pilot_runs/analysis_.../analyze_pilot.py` into the package; keep the historical script untouched |
| B5 | `benchmarks/benchmark_runner.py` (CLI) | the shared `max_output_tokens` confounds the comparison (6 truncations, 5 in ITMS) | run the matrix at a raised cap (e.g. 4096) **for all three methods**, record the cap per attempt, and treat "truncated_output" as a *harness* failure that is reported separately from repair quality; add a budget-sensitivity sub-experiment if a claim about truncation is desired |
| B6 | task set (`benchmarks/data/tasks/*`, `benchmarks/phase0/build_tasks.py`) | the 20 tasks are single-function modules: the target is ~96 % of the file, so ITMS cannot save tokens, and the model solves 100 % of them | build a **new** task family of multi-function modules (200–500 lines, several classes/helpers) with the defect in one function; keep the existing 20 for continuity. Do not modify the existing 20 (historical datasets are immutable) |

### Non-blocking but required before publication

| # | Item | Where |
| --- | --- | --- |
| N1 | The Phase 2 dashboard tab should read a *real* run (read-only) and label the simulation as a simulation; the two views must not be confusable | `app.py:895-1010`, `edit_orchestrator.py` |
| N2 | A standalone integrity validator for the Phase 2 record schema (matrix completeness, candidate hash ↔ artefact ↔ evaluated bytes, JSONL↔CSV↔summary agreement, no mock records, missing telemetry as `null`, seed consistency) | new module modelled on `experiments/itms_aider_cline_v1/integrity.py` |
| N3 | Permanent method-equivalence + prompt-leak probes (today they were ad-hoc offline commands) | `benchmarks/phase0/` or a new `benchmarks/phase2/` package |
| N4 | Explicit "cost unavailable" handling in the figures and summaries: 0/120 attempts reported cost, so cost must never be plotted as `$0` | `benchmarks/benchmark/analysis.py`, the viewer |
| N5 | A second replicate on a different pinned model (the current slug is free and may be re-pointed by OpenRouter over time) | protocol §6 |

---

## 5. Required configuration and dependencies

| Item | Value / status |
| --- | --- |
| Provider | `openrouter` (base `https://openrouter.ai/api/v1`). Credential **present**. |
| Pinned model id | **`minimax/minimax-m3.1-flash:free`** — the same slug used by the historical 120-attempt Phase 2 run *and* the 300-attempt ITMS-vs-Aider-vs-Cline run, which keeps the new run comparable with both. A paid pinned slug is also acceptable but requires the spend gate of B2. **A dynamic alias (e.g. `openrouter/free`) must not be used**: it silently serves different models between attempts and voids the comparison. |
| Runtime | Python 3.14.4 in `.venv`; `httpx` 0.28.1, `tiktoken` 0.14.0, `python-dotenv`, `pytest` 9.1.1, `matplotlib` (already used by `analyze_pilot.py`). **No new dependency is required.** |
| Environment | `OPENROUTER_API_KEY` must resolve through `config.AppConfig.resolve_api_key("openrouter")`; `OPENROUTER_HTTP_REFERER` / `OPENROUTER_APP_TITLE` are optional attribution headers (currently unset) and contain no secret. |
| Task set | `benchmarks/data/model_facing_tasks.json` + `benchmarks/data/tasks/HE-EDIT-0xx/` — 20 tasks, manifest digest `92077bdca33b75b3ca3c7c15a55b237b6c1f5c79d1da20ebb0f9efb4d23d08f1` (must be re-pinned in `run_metadata.json` and reported). |
| Output location | **A new directory** — e.g. `results/phase2_twoway_<model>_<UTC timestamp>/`. The runner refuses a non-empty existing output directory unless `--allow-overwrite` is passed; do **not** pass that flag. `pilot_runs/`, `results/pilot_*` and `experiments/**/runs/` must not be written to. |
| Cost | **Unknown / not calculable from this provider endpoint.** The OpenRouter chat-completions response did not report a cost in either live run (0/120 and 0/3 ⇒ `cost_usd: null`). The pinned slug is a `:free` route, so the expected bill is $0, but the *measured* cost is **unavailable** and must be labelled as such — never as `$0` and never as an estimate presented as a measurement. An explicitly-labelled *estimate* (tokens × published price) may be added as a separate column. |

---

## 6. Proposed protocol

### 6.1 Fairness rules (all three strategies)

1. **Same task, same starting file.** One `BenchmarkTask` per attempt; the mutated source is byte-identical for all three methods (checksum-verified by `TaskRepository`).
2. **Same instruction text.** `task.repair_instruction` + `task.problem_prompt` are inserted verbatim by every adapter; only the *presentation* differs (full file vs skeleton) — and the target's AST in the skeleton is identical to the original (verified today, 20/20).
3. **Same evaluator.** `run_official_test(candidate, official_test, target)` — identical function, identical timeout, identical temp-dir discipline, for all three. The official test never enters a prompt (leak probe exists in `benchmarks/tests/test_phase1_adapters.py`).
4. **Same model, same sampling parameters.** One `LiveProvider` instance for the whole run ⇒ one model id, one temperature, one `max_output_tokens`, one timeout, one retry policy.
5. **Same repetition policy.** `reps` identical for all three methods; the schedule is built by `build_schedule(..., seed=…)` and is reproducible from the seed.
6. **Reproducible ordering.** Fixed `--seed` for the shuffle; the run-order seed is written to `run_metadata.json`; the *request-level* seed must also be implemented (B1) so each cell is reproducible.
7. **Identical treatment of failures.** Every attempt — including provider errors, empty responses, format violations and truncations — is recorded and counted in its own category; denominators are always stated (scheduled / recorded / evaluated).
8. **No post-hoc exclusion.** `unevaluated` attempts are excluded from the pass-rate denominator and counted in the completion rate; nothing is imputed (the 120-run's `RESULTS_SUMMARY.md` already follows this rule and it must be preserved).

### 6.2 Pilot plan (Stage B — 9 live requests)

| Step | Action | Gate |
| --- | --- | --- |
| B0 | Offline: `benchmarks/phase0/validate_tasks.py --verify-manifest`; full `pytest -q`; the mock run of 20×3×1 into a temp dir; the method-equivalence and prompt-leak probes (§7 commands) | everything green, artefacts written, no mock record can be mistaken for a measurement |
| B1 | `benchmark_runner.py --dry-run --mode live --provider openrouter --model minimax/minimax-m3.1-flash:free --tasks HE-EDIT-001,HE-EDIT-006,HE-EDIT-009 --reps 1` | preflight OK, schedule printed, **no request sent** |
| B2 | Live pilot: same but `--mode live`, 3 tasks × 3 methods × 1 rep = **9 chat requests** (+1 catalog), interactive `yes` confirmation (no `--yes`) | 9/9 recorded; 1 request per attempt; `served_model_id == requested_model_id` for all 9; `artifacts_status = ok` for 9/9; candidate hashes match; **zero** `truncated_output` at the chosen cap; `cost_usd` either reported or `null` (never 0) |
| B3 | Offline analysis of the pilot + a written comparison of the pilot's numbers with the 120-run's | no unexplained regression; the pilot's limitations section drafted |

### 6.3 Full-run protocol (Stage C — 300 chat requests)

* **Matrix:** 20 tasks × 3 methods × **5 reps** = **300 attempts** (the repetition policy that the historical ITMS-vs-Aider-vs-Cline run used, so the two datasets are comparable). If the budget must be smaller, 3 reps = 180 attempts and the deviation must be declared.
* **Order:** `--seed 20261021` (or a new declared seed) → the same deterministic shuffle; the seed is pinned in `run_metadata.json`.
* **Model:** one pinned slug for the whole run; **no dynamic alias**; record `served_model_id` per attempt and publish the distribution.
* **Sampling:** `temperature 0.0` with per-cell request seeds (as in the ITMS-vs-Aider-vs-Cline run) for reproducibility, **or** `temperature 0.2` with unseeded samples for variance — but the choice must be declared *before* the run, and the same for all three methods. Recommendation: `0.0` + seeds, to make the run exactly re-runnable.
* **Output budget:** `max_output_tokens` raised to a level where truncation is rare for all three (measure the pilot's `completion_tokens` distribution first); the value is recorded in the metadata and treated as a **controlled variable**, and the truncation count is published per method.
* **Timeouts:** `request_timeout 60 s`, `evaluation_timeout 10 s`, `max_retries 2` (transient HTTP 429 only) — identical to the historical run.
* **Spend:** B2's gate must be live before the first request; `--max-spend-usd` with the free-model guard.
* **Interruption:** the runner writes `attempts.jsonl` incrementally (append-only) and refuses to resume an incomplete experiment — a killed run must be inspected, not silently continued.
* **Post-run:** independent validation (N2) re-deriving every published number from the files on disk; then the analysis/figures (B4) and the dashboard wiring (N1).

### 6.4 What the run can and cannot conclude

| It can support | It cannot support |
| --- | --- |
| patch-application reliability per method (strict output contracts + real failures) | "ITMS is more accurate" unless the task set is made discriminating (B6) |
| provider-reported token usage per method on a pinned model | a general "94 % token reduction" claim (B6: the effect is absent on single-function files) |
| wall-clock and model latency per method, including truncation effects | a "secure sandbox" claim (§8) |
| completion rate (recorded/evaluated) per method, with format failures separated | any claim about a **paid** model without a spend gate and a priced model |
| reproducibility of a pinned-model run | a comparison against a *different* model or a different date's free-route behaviour |

---

## 7. Commands (all safe, offline — already executed during this audit)

```bash
cd /Users/noshinebnatadrita/Documents/499B

# 1. Test suite (unchanged by this audit)
.venv/bin/python -m pytest -q                                  # → 471 passed, 23 subtests
.venv/bin/python -m pytest benchmarks/tests benchmarks/phase0 test_itms_phase2_e2e.py -q   # → 254 passed

# 2. Task-set integrity (offline)
.venv/bin/python benchmarks/phase0/validate_tasks.py --verify-manifest   # → 20/20 tasks, manifest OK

# 3. Pipeline dry run in mock mode into a TEMP dir (no network, no repo writes)
.venv/bin/python benchmarks/benchmark_runner.py --mode mock \
    --tasks HE-EDIT-006,HE-EDIT-009 --reps 1 --output-dir <TMP>/p2mock
#   → 6 attempts; per-attempt attempt.json / candidate.py / raw_response.json

# 4. Offline live preflight (no request is sent)
.venv/bin/python benchmarks/benchmark_runner.py --dry-run --mode live \
    --provider openrouter --model minimax/minimax-m3.1-flash:free --output-dir <TMP>/preflight

# 5. Read-only inspection of the historical live run (never modified)
.venv/bin/python experiments/itms_aider_cline_v1/integrity.py \
    --run-dir experiments/itms_aider_cline_v1/runs/itms_aider_cline_v1_live_20261021T171639Z \
    --output <TMP>/integrity_recheck.md          # → 32/32 checks, 300/300 attempts, 0 missing cells

# 6. Fairness probes (offline, ad-hoc; to be promoted to permanent tests, N3)
#    - target AST equality between mutated source and ITMS skeleton  → 20/20 identical
#    - gold injected into each method's output format → all three pass 20/20
```

**Not executed (and not authorised):** any `--mode live` run, any provider request, any paid call.

---

## 8. Security audit of generated-code execution

### 8.1 What the benchmark evaluator actually enforces

`benchmarks/phase0/runner.py::run_official_test` (used by `AttemptPipeline._run_official_test`):

| Control | Status |
| --- | --- |
| Runs in a fresh temporary working directory | **enforced** |
| `PYTHONPATH` scrubbed; `PYTHONNOUSERSITE=1`; user site-packages disabled | **enforced** |
| Environment reduced to `PATH`, `TMPDIR`, `LANG`, `PYTHON*`, `PYTHONHASHSEED` | **enforced** — **no API key or credential is visible to the candidate** |
| Sockets blocked at interpreter level (`sitecustomize`: `socket.socket`, `create_connection`, `socketpair`, `urllib.request.urlopen`) | **enforced, but bypassable** (documented in `PHASE2_AUDIT_REPORT.md` §5 as a *NETWORK-BYPASS*: a child interpreter re-enables sockets) |
| Wall-clock timeout with process termination | **enforced** |
| No convenience imports injected; the candidate is graded as written | **enforced** |

### 8.2 What is **not** enforced (residual risk)

1. **No OS-level isolation.** No Docker/container/namespace/seccomp/cgroup. The candidate runs as the operator's user on the operator's machine.
2. **No file-system confinement.** The candidate can read the entire home directory — including **`.env`** (which holds a live credential) — and can write anywhere the operator can. The temp cwd limits accidental writes, not malicious ones.
3. **No network isolation** (see the child-interpreter bypass above).
4. **No CPU / memory / process-count limits** (no `ulimit`, no pids cap): a fork bomb or a large allocation can affect the host.
5. **No artefact of the executed code is sandboxed for provenance**: stdout/stderr are captured and truncated to a 500-char tail (redacted first), so a candidate cannot flood the artefact store, but the *execution* itself is unbounded except by the timeout.

### 8.3 Required wording

The execution gate **must not** be described as a secure sandbox. The accurate statement is: *"candidate code is executed in a separate OS process with a scrubbed environment, a scrubbed import path and a wall-clock timeout; this is best-effort isolation, not a security boundary."* The existing `sandbox.py` already says this (`ISOLATION_LEVEL = "process_boundary_only"`, `ISOLATION_NOT_ENFORCED`), and the benchmark's `run_metadata.json` notes carry the same caveat.

### 8.4 Minimum recommendations before a live run

1. Run the benchmark **inside a container** (`--net=none --read-only --memory=256m --pids-limit=64 --cpus=1`) or on a disposable VM, and treat the in-process harness as a fallback only.
2. Or accept the risk explicitly and restrict the run to: a trusted pinned model, the synthetic in-repo tasks, and a dedicated low-privilege account whose home directory does **not** contain `.env` or any credential.
3. Remove the bare secret line from `.env` and rotate that credential (operator action; not performed by this audit).
4. Do **not** point the evaluator at untrusted code: it is designed for model output on synthetic tasks, not for hostile input.

---

## 9. Artifact schema and graph plan

### 9.1 Run directory (already implemented — `benchmarks/benchmark/experiment.py` + `artifacts.py`)

```
<run_dir>/
  attempts.jsonl                 # one AttemptRecord per attempt (46 fields, append-only)
  attempts.csv                   # the same records, flat columns
  summary.json                   # counts, per-method metrics, integrity flags, is_measurement
  run_metadata.json              # model id, temperature, max_output_tokens, timeouts, retries,
                                 # reps, task list, task_manifest_digest, seeds, platform, versions
  artifacts/<attempt_id>/
      attempt.json               # the record itself
      candidate.py               # the exact bytes that were evaluated (redacted if credential-shaped)
      raw_response.json          # the provider response + its sha256, redacted
      prompt.txt                 # ← TO BE ADDED (B3)
```

**Record fields already present** (all in `benchmarks/benchmark/records.py::AttemptRecord`): identity (`attempt_id`, `run_id`, `timestamp_utc`, `task_id`, `humaneval_task_id`, `method`, `rep`, `mode`), model identity (`provider`, `requested_model_id`, `served_model_id`), outcome (`status`, `failure_category`, `failure_reason`, `parse_status`, `parse_error`, `syntax_valid`, `test_passed`, `test_error_type`, `timed_out`, `truncated_output`), telemetry (`latency_ms`, `provider_latency_ms`, `prompt_tokens`, `completion_tokens`, `total_tokens`, `usage_source`, `estimated_prompt_tokens`, `estimated_completion_tokens`, `cost_usd`, `cost_source`, `raw_usage`, `retry_count`, `finish_reason`), provenance (`artifacts_*`, `candidate_*`, `raw_response_*`, `evaluation_attempted`).

**Fields to add for a Phase 2 claim run:** `seed_requested`, `seed_supported`, `seed_applied` (B1); `prompt_sha256` + `prompt_token_estimate` (B3); `max_output_tokens` at attempt level (currently run-level only); `spend_usd_cumulative` and `spending_limit_reached` (B2); `edit_mechanism` (the concrete application path actually used, e.g. `ast_splice` / `text_replace` / `verbatim_file`).

### 9.2 Graph plan (all denominators printed on the figure)

| Figure | Content | Denominator | Status |
| --- | --- | --- | --- |
| fig1 | Evaluator pass rate per method | attempts that reached the evaluator | exists in `analyze_pilot.py` |
| fig1b | Completion rate per method (all outcomes) | scheduled attempts | exists |
| fig2 | Success vs failure split per method | recorded attempts | exists |
| fig3 | Mean execution time per method | recorded attempts with `latency_ms` | exists |
| fig4 | Mean token usage per method (provider-reported) | attempts with provider-reported `total_tokens` | exists |
| fig5 | Task × method pass-rate heatmap | per task, over reps | exists |
| fig6 | **Output-token distribution per method** (box), truncation attempts marked | attempts with provider-reported tokens | **new** |
| fig7 | **Failure-category stacked bars per method** | recorded attempts | **new** |
| fig8 | **Latency decomposition** (provider latency vs total) per method | recorded attempts | **new** |
| fig9 | **Truncation rate vs `max_output_tokens`** (budget sensitivity) | attempts per cap | **new** |
| fig10 | **Cost** — only attempts for which the provider reported a cost; otherwise an explicit "unavailable" panel (**never** a `$0` bar) | attempts with `cost_usd is not None` | **new** |
| fig11 | **Per-method completion-rate confidence interval** (bootstrap, `BOOTSTRAP_SEED` already defined in `analysis.py`) | scheduled attempts | **new** |

Every figure must state: mode (`live`), model id (requested **and** served), reps, and the denominator; the `notes` must restate the ceiling effect, the truncation count and the "cost unavailable" fact.

---

## 10. Provider-request estimate and cost

| Scenario | Chat requests | Catalog requests | Total HTTP requests (worst case with `--max-retries 2`) |
| --- | --- | --- | --- |
| Stage B pilot (3 tasks × 3 methods × 1 rep) | 9 | 1 | 9–28 |
| Stage C full run (20 × 3 × 5) | 300 | 1 | 301–901 |
| Stage C reduced (20 × 3 × 3) | 180 | 1 | 181–541 |

* One chat request per attempt, by construction (`AttemptPipeline` calls `provider.complete()` exactly once). Retries only occur on transient HTTP 429, and the historical 120-run recorded `sum(retry_count) = 0`.
* Wall-clock estimate from the historical run: 120 attempts took **768.8 s** (≈ 6.4 s/attempt) ⇒ **≈ 32 min** for 300 attempts, plus the catalog call and artefact I/O.
* **Cost: unknown.** The provider did not report a cost for any of the 123 live attempts recorded in this repository (0/120 and 0/3) ⇒ `cost_usd` is `null` and must be reported as **unavailable**. The pinned slug is a `:free` route, so the expected bill is $0, but that is an expectation about the route, not a measurement. A clearly-labelled *estimate* (provider-reported tokens × the published price for the slug) may be added as a separate, non-authoritative column once B2's gate is in place. **No dollar figure may be published as measured.**

---

## 11. GO / NO-GO checklist

### Gate 1 — offline readiness (no spend) — **currently GO**

| # | Check | Status | Evidence |
| --- | --- | --- | --- |
| 1.1 | All three strategies have a real prompt → request → apply → evaluate → persist path | ✅ | §2.1; 120 live attempts on record |
| 1.2 | Same evaluator for all three methods, official test never in a prompt | ✅ | `benchmarks/phase0/runner.py`; leak test in `benchmarks/tests/test_phase1_adapters.py` |
| 1.3 | Target defect visible to every method (identical AST) | ✅ | 20/20 identical, measured today |
| 1.4 | No method is disadvantaged by a parser/applier bug | ✅ | gold-injection probe: 20/20 for all three, with the fixed splicer |
| 1.5 | Mock run produces a complete, auditable artefact set | ✅ | executed today: `attempt.json`, `candidate.py`, `raw_response.json` per attempt |
| 1.6 | Task set verifies against the manifest | ✅ | `validate_tasks.py --verify-manifest` → 20/20, manifest OK |
| 1.7 | Test suite green | ✅ | 471 passed + 23 subtests; 254 passed for the benchmark packages |
| 1.8 | No historical artefact is written by a new run | ✅ | the runner refuses a non-empty output dir; `pilot_runs/`, `results/pilot_*`, `experiments/**/runs/` untouched |

### Gate 2 — live pilot (9 requests) — **CONDITIONAL GO**

| # | Check | Status | Action |
| --- | --- | --- | --- |
| 2.1 | Pinned, non-alias model id | ✅ ready | `minimax/minimax-m3.1-flash:free`; preflight OK offline |
| 2.2 | Credential present for the declared provider | ✅ ready | present, never printed |
| 2.3 | Same credential not shared with another vendor | ⚠️ **fails today** | `openai`/`deepseek` resolve to the same fingerprint → operator must fix `.env` (or restrict `--provider openrouter`) |
| 2.4 | Request-level seed implemented and recorded | ✅ **closed** (B1) | implemented; `seed_honour_verified` stays `false` |
| 2.5 | Spending gate implemented | ✅ **closed** (B2) | `--max-attempts` mandatory in live mode; `--max-spend-usd`; `--require-free-model` |
| 2.6 | Prompt persisted per attempt | ✅ **closed** (B3) | `prompt.txt` + `prompt.json` per attempt, with the original-prompt digest |
| 2.7 | Output cap high enough that no truncation occurs | ⚠️ **partly closed** (B5) | the limit is now *derived* from the task set and identical for all three strategies; for the current small tasks the derived limit is 2048, so the pilot must confirm the truncation rate |
| 2.8 | Execution isolation adequate for the threat model | ⚠️ **partial** | run in a container, or accept the documented risk on a low-privilege account without `.env` (§8.4) |
| 2.9 | Operator confirms the request budget interactively | ⬜ pending | the runner's prompt; do **not** use `--yes` |
| 2.10 | Shared-credential configuration resolved | ⚠️ **detected, operator action pending** | the live preflight now refuses it; the operator must fix `.env` or pass `--allow-shared-credential` |

### Gate 3 — 300-attempt claim run — **NO-GO today**

| # | Check | Status | Blocking reason |
| --- | --- | --- | --- |
| 3.1 | B1–B5 closed and re-verified offline | ✅ **closed** | see §13.1 (seed, spend gate, prompt persistence, analysis/figures pipeline, derived output cap) |
| 3.2 | A task set that can discriminate the methods | ❌ **still open** | ceiling effect (100 % pass rate) **and** no token advantage on single-function files; the selection rule is delivered (B6) but **0 of the current 20 tasks qualify**, so a new multi-function task family must be built and Phase 0-validated first |
| 3.3 | Analysis/figures produced by a version-controlled tool from the run dir | ✅ **closed** | `benchmarks/analyze_run.py` + `benchmarks/benchmark/{report,figures}.py` (B4) |
| 3.4 | Independent integrity validation of the published numbers | ✅ **closed** | 16 checks inside the analysis tool; it re-validated the historical 120-attempt run read-only |
| 3.5 | Dashboard shows the real run and labels it as live/measured | ❌ **still open** | N1 — the Phase 2 tab is a simulation; the real data is in another tab |
| 3.6 | Cost reported as measured or explicitly unavailable | ✅ rule defined | publish `unavailable`; never `$0` |
| 3.7 | Model identity pinned and published (requested + served) | ✅ rule defined | the 120-run precedent: served == requested for 120/120 |

**Recommended sequence:** close B1–B5 → re-run the offline gates (mock matrix + probes) → 9-request pilot → review → extend the task set (B6) and repeat the pilot on the harder set → only then authorise the 300-attempt run, the analysis, the figures and the dashboard publication.

---

## 12. Files touched by the readiness audit

**The audit itself changed no source file.** Its only write was this document plus temporary files outside the repository (`/var/folders/.../T/opencode/…`) for the mock run, the dry-run preflights and the integrity re-check.

**§13 below documents the follow-up implementation work** (the B1–B6 closure), which did change source files.

---

## 13. B1–B6 closure — implementation evidence (2026-10-22, second pass)

**Implementation plan:** `PHASE2_BLOCKER_IMPLEMENTATION_PLAN.md` (files, tests and acceptance criteria per blocker).
**No live API call was made.** The whole closure was implemented and tested offline; the test session blocks sockets at the interpreter level (`benchmarks/tests/conftest.py`).

### 13.1 Status per blocker

| Blocker | Status | Implementation | Test evidence |
| --- | --- | --- | --- |
| **B1** request-level seed | **CLOSED** | `LiveProvider.complete(..., seed=…)` forwards the seed to `generate_live()` (the dispatcher already put it in the payload); `ProviderResult`/`AttemptRecord` carry `seed_requested`, `seed_supported`, `seed_applied`, `seed_honour_verified`; `ExperimentRunner.seed_for()` derives a per-cell seed from `--seed-base` + the cell's position in the deterministic schedule. **`seed_honour_verified` is always `False`** — honouring is not claimed from a single request. The pipeline detects whether a provider implementation can carry a seed, so third-party providers keep working and are recorded as *not applied*. | `test_b1_seed_is_derived_deterministically`, `test_b1_seed_is_sent_on_the_request`, `test_b1_seed_metadata_is_recorded_without_claiming_honour`, `test_b1_mock_provider_records_no_request_level_seed` — **4 passed** |
| **B2** spend / attempt gate | **CLOSED** | New `benchmarks/benchmark/budget.py` (`BudgetGate`): a **mandatory attempt cap** (`--max-attempts`, required in live mode) and an optional **reported-spend** cap (`--max-spend-usd`) that is never applied to an estimate; `--require-free-model` refuses a non-`:free` route. The gate is validated **before the first request**; when a limit is reached the run stops cleanly and **every remaining cell is recorded as not executed** (`budget.not_executed_cells`, `not_executed_count`). When the provider reports no cost, the gate records `cost_reporting_available: false` and `spend_cap_effective: false` instead of pretending to enforce a spend cap. | `test_b2_attempt_cap_aborts_before_the_next_request`, `test_b2_spend_cap_aborts_on_reported_cost`, `test_b2_free_model_guard_refuses_a_paid_model`, `test_b2_gate_reports_unavailable_cost`, `test_b2_gate_requires_an_explicit_attempt_cap`, `test_b2_live_mode_requires_an_attempt_cap`, `test_b2_not_executed_cells_are_reported` — **7 passed** |
| **B3** prompt persistence | **CLOSED** | The pipeline keeps the exact prompt in the outcome; `ArtifactStore.persist()` writes `artifacts/<attempt_id>/prompt.txt` (redacted) and `prompt.json` (task id, method, attempt id, SHA-256 of the **original** prompt bytes, byte length, local token estimate, `redaction_applied`); the record links both (`prompt_path`, `prompt_sha256`, `prompt_token_estimate`). | `test_b3_prompt_is_persisted_with_identity`, `test_b3_prompt_hash_matches_the_sent_prompt`, `test_b3_prompt_is_redacted` — **3 passed** |
| **B4** offline analysis + figures | **CLOSED** | New `benchmarks/benchmark/report.py` (re-derives every number from the saved records + 16 integrity checks), `benchmarks/benchmark/figures.py` (10 figures, denominators in every title, an explicit *cost unavailable* panel instead of a `$0` bar) and the CLI `benchmarks/analyze_run.py` (`--run-dir`, `--out-dir`, `--no-figures`). The run directory is only **read**; a failed validation writes a *VALIDATION FAILED* banner and exits 2. | `test_b4_analysis_runs_offline_on_a_mock_run`, `test_b4_analysis_reports_unavailable_cost`, `test_b4_analysis_flags_a_mock_run_as_not_a_measurement`, `test_b4_analysis_detects_a_tampered_candidate`, `test_b4_figures_are_written` — **5 passed** |
| **B5** justified output-token limit | **CLOSED** | New `benchmarks/benchmark/output_budget.py`: `limit = ceil(2.0 × max_file_tokens) + 1024`, rounded up to a multiple of 256 and clamped to `[2048, 32768]`, with the constants and their rationale in the module. The same resolved limit is used by **all three strategies** (one provider instance) and is recorded per attempt (`max_output_tokens`), in the metadata (`output_token_limit_justification`) and checked by the analysis (`output_cap_consistent_across_strategies`). Truncation stays a first-class outcome (`truncated_output`) and is counted per method. | `test_b5_justified_limit_covers_the_largest_file`, `test_b5_limit_is_configurable_and_recorded`, `test_b5_same_cap_is_used_for_every_strategy`, `test_b5_truncation_is_recorded_explicitly` — **4 passed** |
| **B6** representative task set | **METHOD DELIVERED; DATASET STILL MISSING** | New `benchmarks/phase2/task_selection.py`: a **pre-registered rule expressed only in source properties** (`file_lines ≥ 200`, `target_ratio ≤ 0.20`, `function_count ≥ 5`, `class_count ≥ 1`) with `select_tasks()` returning both the qualifying set **and a rejection report** naming every excluded task and the property that excluded it. The rule never consults a result. The **current 20-task set is unchanged** and is reported as **not representative** (0/20 qualify; target ratio 0.92–0.99; 1 function, 0 classes per file). A new multi-function task family still has to be built and validated by the Phase 0 pipeline. | `test_b6_properties_are_computed_from_the_source`, `test_b6_rule_rejects_every_current_task`, `test_b6_selection_is_deterministic_and_order_independent`, `test_b6_rejection_report_lists_every_excluded_task`, `test_b6_rule_selects_a_qualifying_task` — **5 passed** |
| Provider-configuration ambiguity | **CLOSED (detection + fail-closed operator path)** | `config.AppConfig.credential_binding_report()` reports the **provider names** that share a credential and nothing else (constant-time comparison in memory; no value, length, hash or fingerprint). `LiveProvider.preflight()` refuses a shared credential when `enforce_credential_binding=True`; the runner sets it, so a live run **fails closed** unless the operator passes `--allow-shared-credential`. `config.AppConfig.env_hygiene_report()` reports whether `.env` holds a line without a `KEY=` assignment (a bare secret) and lists variable **names** only. The bare line itself remains an operator action (delete + rotate); `.env` was not modified. | `test_config_report_lists_providers_without_exposing_values`, `test_preflight_refuses_a_shared_credential_unless_allowed`, `test_credential_binding_is_recorded_without_refusing_by_default`, `test_env_hygiene_detects_a_bare_secret_line` — **4 passed** |

### 13.2 Offline evidence of the new behaviour

```
$ .venv/bin/python benchmarks/benchmark_runner.py --dry-run --mode live \
      --provider openrouter --model minimax/minimax-m3.1-flash:free \
      --max-attempts 3 --tasks HE-EDIT-006 --reps 1 --output-dir <TMP>/preflight_a3
  exit 2
  stderr: live preflight: FAILED — The credential configured for provider 'openrouter'
          is also configured for: deepseek, openai. Refusing to run: ... pass
          --allow-shared-credential to acknowledge the risk explicitly.

$ .venv/bin/python benchmarks/benchmark_runner.py --dry-run --mode live ... --allow-shared-credential
  live preflight: OK (credentials and model id resolved)
  dry run: 3 attempts scheduled, no provider contacted.        exit 0

$ .venv/bin/python benchmarks/analyze_run.py --run-dir pilot_runs/2026-10-21_minimax-m3-1-flash-free_120_attempts \
      --out-dir <TMP>/hist120_analysis
  mode: live | is_measurement: True | records: 120 | evaluated: 114
  integrity: ALL CHECKS PASSED   (16 checks)                 exit 0
  # the historical run directory was NOT modified (git status clean)
```

The new analysis tool independently re-validated the **historical 120-attempt live run** (read-only): 16/16 integrity checks pass, the 6 truncations are attributed 5 × `itms_ast_splice` / 1 × `full_file_regeneration`, and the cost is reported as *unavailable* (0/120 attempts carry a provider-reported cost). The prompt-persistence check reports **0/120 — vacuous, not a pass on merit**, because that run predates B3.

### 13.3 Updated GO / NO-GO

| Gate | Before | Now | Reason |
| --- | --- | --- | --- |
| **1 — offline readiness** | GO | **GO** | unchanged, plus the new gates |
| **2 — 9-request live pilot** | CONDITIONAL GO | **CONDITIONAL GO** | B1, B2, B3, B5 and the configuration guard are implemented and tested. Still open: the operator must either fix the shared credential in `.env` or pass `--allow-shared-credential`; the output cap must be re-checked in the pilot (B5's default is 2048 for the current small task set); the run must be executed with a pinned model. |
| **3 — 300-attempt claim run** | NO-GO | **NO-GO** | **B6 is not closed**: the current task set still cannot demonstrate the thesis (0/20 tasks qualify; the ITMS token advantage is absent on single-function files). A new multi-function task family must be built, validated by the Phase 0 pipeline, and the task-selection rule applied to it — all before the claim run. The task-selection method itself is delivered and tested. |

### 13.4 Files changed in the closure pass

| File | Change |
| --- | --- |
| `config.py` | `credential_binding_report()`, `env_hygiene_report()`, `_env_file_path()` — provider names only, never a secret. |
| `benchmarks/benchmark/provider.py` | seed plumbing (`ProviderResult`, `BenchmarkProvider.complete`, `MockProvider`, `LiveProvider`), credential-binding guard (`enforce_credential_binding` / `allow_shared_credential`). |
| `benchmarks/benchmark/records.py` | new fields: `seed_*` (4), `max_output_tokens`, `prompt_path`, `prompt_sha256`, `prompt_token_estimate` + CSV columns. |
| `benchmarks/benchmark/pipeline.py` | prompt capture, seed plumbing with provider-capability detection, prompt token estimate. |
| `benchmarks/benchmark/artifacts.py` | `prompt.txt` + `prompt.json` persistence, prompt digest and identity in `attempt.json`. |
| `benchmarks/benchmark/budget.py` | **new** — `BudgetGate` (attempt cap, reported-spend cap, free-model guard). |
| `benchmarks/benchmark/output_budget.py` | **new** — the justified, configurable output-token limit. |
| `benchmarks/benchmark/experiment.py` | gate enforcement, per-cell seed derivation, budget/summary/metadata exposure, truncation and cap-consistency counters. |
| `benchmarks/benchmark/report.py` | **new** — offline analysis + 16 integrity checks + markdown report. |
| `benchmarks/benchmark/figures.py` | **new** — 10 deterministic figures. |
| `benchmarks/benchmark_runner.py` | `--max-attempts` (mandatory in live mode), `--max-spend-usd`, `--require-free-model`, `--seed-base`, `--allow-shared-credential`, auto output limit. |
| `benchmarks/analyze_run.py` | **new** — the offline analysis CLI. |
| `benchmarks/phase2/task_selection.py`, `benchmarks/phase2/__init__.py` | **new** — the pre-registered selection rule and the task-set property report. |
| `benchmarks/tests/test_phase2_readiness_gates.py` | **new** — 32 offline tests for the gates, logging, analysis pipeline and configuration validation. |
| `benchmarks/tests/test_phase1_metrics.py` | two CLI tests updated for the new mandatory `--max-attempts` flag (the assertions about the fail-closed behaviour are unchanged). |

**Not touched:** `pilot_runs/`, `results/`, `benchmarks/data/`, `experiments/**/runs/`, `.env`, the evaluator (`benchmarks/phase0/runner.py`), and every historical report.

---

## 14. Final status

* **B1–B5: closed** with direct implementation and offline test evidence (23 dedicated tests, plus 9 configuration tests).
* **B6: the *method* is delivered and tested; the *dataset* is still missing.** The current 20-task set is retained unchanged and is reported as not representative (0/20 qualify under the pre-registered rule).
* **The live tri-way path is ready for the 9-request pilot** once the operator resolves the shared-credential configuration (or acknowledges it) and the output cap is confirmed in the pilot.
* **The 300-attempt claim run remains NO-GO** until a representative task family exists.
* **The execution gate is still not a secure sandbox**, and no document produced by this work claims otherwise.

**The audit itself changed no source file.** Its only write was this document plus temporary files outside the repository (`/var/folders/.../T/opencode/…`) for the mock run, the dry-run preflights and the integrity re-check.

**§13 below documents the follow-up implementation work** (the B1–B6 closure), which did change source files.
