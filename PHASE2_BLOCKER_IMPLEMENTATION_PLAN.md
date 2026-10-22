# Phase 2 Blocker Implementation Plan (B1–B6)

**Repository:** `/Users/noshinebnatadrita/Documents/499B`
**Created:** 2026-10-22
**Source audit:** `PHASE2_LIVE_BENCHMARK_READINESS.md`
**Scope rule:** B1–B5 are implemented **only** where the behaviour can be made correct and tested **entirely offline**. B6 is delivered as a **reproducible selection method + a property report**, never as a silent dataset replacement and never as a selection based on comparative results.

**No live API call is made by any of this work. No historical run is modified. `.env` is never read, printed or committed.**

---

## 0. Non-negotiable invariants

| # | Invariant | How it is enforced / proven |
| --- | --- | --- |
| I1 | No provider request is issued by the test suite | `benchmarks/tests/conftest.py` installs a session-wide socket guard; any outbound connection raises |
| I2 | A failed, unevaluated, missing-candidate or truncated attempt can never be reported as a pass | existing `AttemptStatus` / `FailureCategory` semantics are preserved; new tests assert it |
| I3 | An unmeasured value is never written as `0`; an estimate is never labelled as measured | new fields default to `None`; `usage_source` / `cost_source` keep their provenance |
| I4 | Credentials never reach a file, a log, a report or a test | new `AppConfig.credential_binding_report()` reports **provider names only**; `redact_secrets` is applied to prompts as well as responses |
| I5 | Historical artefacts are read-only | all new code writes only to a caller-supplied output directory; no default path points into `pilot_runs/`, `results/pilot_*` or `experiments/**/runs/` |
| I6 | The evaluator is not weakened | `benchmarks/phase0/runner.py` is not modified by this work |

---

## 1. B1 — Request-level seed / reproducibility metadata

| Field | Value |
| --- | --- |
| **Files** | `benchmarks/benchmark/provider.py` (`ProviderResult`, `BenchmarkProvider.complete`, `MockProvider.complete`, `LiveProvider.complete`, `LiveProvider.preflight`), `benchmarks/benchmark/records.py` (`AttemptRecord`, `CSV_COLUMNS`), `benchmarks/benchmark/pipeline.py` (`AttemptPipeline.run`), `benchmarks/benchmark/experiment.py` (`ExperimentRunner.run`, `_write_metadata`) |
| **Behaviour** | The runner derives a **deterministic per-attempt seed** from `--seed-base` and the attempt's position in the seeded schedule. The seed is sent as a request-level `seed` field (the dispatcher already supports it, `model_dispatcher.py:727-728`). The record stores `seed_requested`, `seed_supported`, `seed_applied` and `seed_honour_verified`. `seed_honour_verified` is **always `False`**: honouring cannot be proven without a repeated-request probe, and the report must not claim otherwise. |
| **Tests** | `test_b1_seed_is_derived_deterministically`, `test_b1_seed_is_sent_on_the_request`, `test_b1_seed_metadata_is_recorded_without_claiming_honour`, `test_b1_mock_provider_records_no_request_level_seed` |
| **Acceptance** | Two runs with the same `--seed-base` and the same schedule produce the same seed for every cell; the seed reaches the provider payload; the record distinguishes *sent* from *honoured*. |

## 2. B2 — Mandatory spend / attempt gate

| Field | Value |
| --- | --- |
| **Files** | **new** `benchmarks/benchmark/budget.py` (`BudgetGate`), `benchmarks/benchmark/experiment.py` (`ExperimentRunner.__init__`, `.run`, `._summarise`, `._write_metadata`), `benchmarks/benchmark_runner.py` (CLI flags) |
| **Behaviour** | A `BudgetGate` enforces two independent limits **before any request**: (1) an **attempt cap** (`--max-attempts`, mandatory in live mode) and (2) a **reported-spend cap** (`--max-spend-usd`, optional). The attempt cap is the binding guard when the provider does not report cost — which is the case for this endpoint (0/120 attempts reported cost) — and the gate records that fact explicitly (`cost_reporting_available: false`). `--require-free-model` refuses a model id that is not a `:free` route. When a limit is reached the run **stops cleanly**: the remaining cells are recorded as `not_executed`, the summary and the metadata carry the reason, and nothing is silently dropped. |
| **Tests** | `test_b2_attempt_cap_aborts_before_the_next_request`, `test_b2_spend_cap_aborts_on_reported_cost`, `test_b2_free_model_guard_refuses_a_paid_model`, `test_b2_gate_reports_unavailable_cost`, `test_b2_live_mode_requires_an_attempt_cap` |
| **Acceptance** | With `--max-attempts 4`, exactly 4 provider calls happen and the 5th cell is never requested; the summary states how many cells were not executed and why; a spend cap is enforced on **reported** cost only and never on an estimate. |

## 3. B3 — Exact prompt persistence

| Field | Value |
| --- | --- |
| **Files** | `benchmarks/benchmark/pipeline.py` (capture the prompt in the outcome), `benchmarks/benchmark/artifacts.py` (`ArtifactStore.persist`), `benchmarks/benchmark/records.py` (`prompt_path`, `prompt_sha256`, `prompt_token_estimate`) |
| **Behaviour** | Every attempt stores the **exact** prompt that was sent, next to the response: `artifacts/<attempt_id>/prompt.txt` (redacted copy) and `artifacts/<attempt_id>/prompt.json` (task id, method, attempt id, SHA-256 of the *original* prompt bytes, byte length, local token estimate, `redaction_applied`). The record links both by relative path and hash. |
| **Tests** | `test_b3_prompt_is_persisted_with_identity`, `test_b3_prompt_hash_matches_the_sent_prompt`, `test_b3_prompt_is_redacted` |
| **Acceptance** | For every recorded attempt the prompt file exists, its metadata names the task id, the strategy and the attempt id, and the stored hash equals the hash of the prompt the provider received. |

## 4. B4 — In-repository analysis + figures (offline)

| Field | Value |
| --- | --- |
| **Files** | **new** `benchmarks/benchmark/figures.py`, **new** `benchmarks/analyze_run.py` (CLI), `benchmarks/benchmark/analysis.py` (integrity + truncation + cap-consistency metrics) |
| **Behaviour** | `benchmarks/analyze_run.py --run-dir <dir> --out-dir <dir>` reads only the files in the run directory, re-derives every number from the records, runs an **integrity validation** (matrix completeness, JSONL↔CSV agreement, no mock record in a live run, candidate hash ↔ artefact bytes, unevaluated attempts retained, missing telemetry stored as `null`, one shared output cap across strategies, seed consistency), writes `analysis.json`, the figures and `RESULTS_SUMMARY.md`. If a validation check fails the report is written with `VALIDATION FAILED` and the numbers are not presented as publishable. Cost is plotted **only** for attempts whose cost the provider reported; otherwise an explicit *unavailable* panel is written — never a `$0` bar. |
| **Tests** | `test_b4_analysis_runs_offline_on_a_mock_run`, `test_b4_analysis_reports_unavailable_cost`, `test_b4_analysis_flags_a_mock_run_as_not_a_measurement`, `test_b4_analysis_detects_a_tampered_candidate`, `test_b4_figures_are_written` |
| **Acceptance** | A mock run analysed by the CLI produces `analysis.json`, ≥ 6 figures and `RESULTS_SUMMARY.md` with the denominators, without any network access; a tampered candidate file is detected. |

## 5. B5 — Justified, configurable output-token limit

| Field | Value |
| --- | --- |
| **Files** | **new** `benchmarks/benchmark/output_budget.py` (`justified_output_token_limit`, `resolve_output_token_limit`), `benchmarks/benchmark_runner.py` (`--max-output-tokens` default → auto), `benchmarks/benchmark/records.py` (`max_output_tokens` per attempt), `benchmarks/benchmark/experiment.py` (metadata + summary) |
| **Behaviour** | The cap is **derived from the task set**, not guessed: the largest output a strategy can be obliged to emit is the whole file (full-file and search/replace), so the limit is `ceil(safety_factor × max_file_tokens) + reasoning_allowance`, rounded up to a multiple of 256 and clamped to `[2048, 32768]`. `safety_factor = 2.0` and `reasoning_allowance = 1024` are declared constants with their rationale. The same resolved value is used by **all three strategies** (one provider instance) and is recorded per attempt and in the metadata. Truncation stays an explicit, first-class outcome (`truncated_output`), and the analysis reports truncation counts per strategy plus a `cap_consistent_across_strategies` check. |
| **Tests** | `test_b5_justified_limit_covers_the_largest_file`, `test_b5_limit_is_configurable_and_recorded`, `test_b5_same_cap_is_used_for_every_strategy`, `test_b5_truncation_is_recorded_explicitly` |
| **Acceptance** | The resolved limit is ≥ the largest file's token count plus the allowance; it is identical for the three strategies in a run; every truncated attempt is visible in the records and in the analysis. |

## 6. B6 — Reproducible, non-cherry-picked task selection (method + evidence, no dataset change)

| Field | Value |
| --- | --- |
| **Files** | **new** `benchmarks/phase2/__init__.py`, **new** `benchmarks/phase2/task_selection.py` (`TaskProperties`, `compute_task_properties`, `SelectionCriteria`, `select_tasks`, `write_report`), **new** `benchmarks/phase2/__init__.py` CLI `python -m benchmarks.phase2.task_selection --report` |
| **Method** | A **pre-registered rule expressed only in measurable properties of a task file** — never in model outcomes: `file_lines ≥ 200`, `target_ratio ≤ 0.20`, `function_count ≥ 5`, `class_count ≥ 1`. `select_tasks` applies the rule to the **whole pool** and returns the qualifying set **and a rejection report** listing every excluded task with the property that failed, so the selection is auditable and reproducible. The rule is fixed in code and version-controlled **before** any model run. |
| **Evidence** | The current 20-task set is measured with the same tool: every task is single-function, so `target_ratio ≈ 0.96` and **0 of 20 qualify**. The current dataset is therefore *retained unchanged* and reported as **not representative**; a new multi-function task family must be built and validated by the Phase 0 pipeline before it can be used. |
| **Tests** | `test_b6_properties_are_computed_from_the_source`, `test_b6_rule_rejects_every_current_task`, `test_b6_selection_is_deterministic_and_order_independent`, `test_b6_rejection_report_lists_every_excluded_task` |
| **Acceptance** | The property report for the current 20 tasks is reproducible, shows 0 qualifying tasks, and the selection function never consults a result field. |

## 7. Provider-configuration ambiguity (safe resolution)

| Field | Value |
| --- | --- |
| **Files** | `config.py` (`AppConfig.credential_binding_report`, `AppConfig.env_hygiene_report`), `benchmarks/benchmark/provider.py` (`LiveProvider.preflight` shared-credential guard), `benchmarks/benchmark_runner.py` (`--allow-shared-credential`) |
| **Behaviour** | The audit found that the same credential resolves for more than one provider. The resolution is **detection, not disclosure**: `credential_binding_report()` returns the **provider names** that share a credential and nothing else — no value, no length, no hash, no fingerprint. `LiveProvider.preflight()` then **refuses to start** when the declared provider shares its credential with another provider, unless the operator passes `--allow-shared-credential` explicitly. `env_hygiene_report()` reports whether `.env` contains a line without a `KEY=` assignment (a bare secret) and lists the **variable names** only. The bare line itself is an operator action (rotate it); this work never reads, prints or rewrites `.env`. |
| **Tests** | `test_config_report_lists_providers_without_exposing_values`, `test_config_detects_a_shared_credential`, `test_preflight_refuses_a_shared_credential_unless_allowed`, `test_env_hygiene_detects_a_bare_secret_line` |
| **Acceptance** | No report, log or test output contains a credential value, a length, a hash or a fingerprint; the preflight fails closed on a shared credential by default. |

---

## 8. Execution order

1. `config.py` (configuration detection) → 2. `provider.py` (seed + guard) → 3. `records.py` (schema) → 4. `pipeline.py` (prompt + seed) → 5. `artifacts.py` (prompt persistence) → 6. `budget.py` (new) → 7. `output_budget.py` (new) → 8. `experiment.py` (gate, seeds, metadata) → 9. `benchmark_runner.py` (CLI) → 10. `analysis.py` + `figures.py` + `analyze_run.py` (new) → 11. `benchmarks/phase2/task_selection.py` (new) → 12. tests → 13. re-run the suite → 14. update the readiness report.
