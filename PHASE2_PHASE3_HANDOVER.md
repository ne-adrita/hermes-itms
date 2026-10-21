# Phase 2 + Phase 3 Handover — ITMS vs Aider vs Cline

**Status: complete.** 300/300 planned live attempts were executed and
validated. The Phase 3 analysis is computed from that dataset and is wired
into the Streamlit dashboard.

---

## 1. Headline result

| | ITMS | Aider | Cline |
|---|---|---|---|
| Planned attempts | 100 | 100 | 100 |
| Recorded attempts | 100 | 100 | 100 |
| Execution completed | 99 | 100 | 100 |
| Evaluated | 97 | 100 | 100 |
| Test passed | 97 | 100 | 100 |
| **Completion rate** (of planned) | **99.0 %** (99/100) | **100.0 %** (100/100) | **100.0 %** (100/100) |
| **Evaluator pass rate** (of evaluated) | **100.0 %** (97/97) | **100.0 %** (100/100) | **100.0 %** (100/100) |
| Mean total tokens / attempt | **1,238** | 1,840 | 59,288 |
| Median total tokens / attempt | 1,102 | 1,452 | 50,918 |
| Mean input tokens | 657 | 1,057 | 57,420 |
| Mean output tokens | 581 | 783 | 1,868 |
| Mean latency / attempt | **7.75 s** | 12.42 s | 37.66 s |
| Median latency / attempt | 5.88 s | 9.65 s | 28.45 s |
| Reported cost | unavailable (0/100 reported) | unavailable (0/100 reported) | $0.0000 (100/100 reported) |

**What the data supports:** on this model and this task set, ITMS used
**1.5× fewer** total tokens than Aider and **47.9× fewer** than Cline, and
was **1.6×** / **4.9×** faster respectively.

**What the data does not support:** any claim that ITMS is *more accurate*.
All three systems passed **100 %** of the attempts they actually put in front
of the evaluator. ITMS was in fact marginally *less* complete (99 % vs
100 %), because of three failed attempts. The comparison is an **efficiency**
result, not an accuracy result.

---

## 2. Budget gate — what was resolved and how

The Phase 1 audit listed the spending limit as a hard blocker. Resolution:

| Item | Phase 1 state | Phase 2 state |
|---|---|---|
| Explicit maximum spend | **absent** — no limit in `.env`, no environment variable, no config field, and the OpenRouter key reported `effective_budget: null`, `budgets: []` | **configured and enforced**: `max_spend_usd = 1.00` in `experiment_config.json`, enforced by `ExperimentRunner.check_budget_preflight()` before the first provider call, and charged against *reported* cost after every attempt. A breach stops further calls and is recorded, not swallowed. |
| Model id validity | placeholder, unverified | **verified against the live OpenRouter catalog** (`GET /api/v1/models`, 468 models): `minimax/minimax-m3.1-flash:free` is present. |
| Credential validity | assumed | **verified**: `GET /api/v1/key` → HTTP 200. The value was never printed. |
| Cost exposure | estimated | **actual: $0.00**. The pinned model is a `:free` model; the run's `cumulative_reported_spend_usd` is `0.0` and `limit_reached` is `false`. A `require_free_model` guard makes the runner refuse to start if `model_id` is ever changed to a non-`:free` model while the cap is in force. |

Estimated vs actual request volume: 300 logical attempts produced
roughly 400–500 provider requests (Cline makes several tool-driven calls per
task; Aider one to two; ITMS exactly one). The estimate in the Phase 1
audit (600–1,200) was therefore an upper bound, not an understatement.

---

## 3. What was found and fixed before the run

The Phase 1 dry run passed 30/30 checks but used **mock** adapters, which
never load `config.py` and never launch a CLI. A 9-cell live smoke test
exposed two defects that the dry run structurally could not catch:

1. **Repository-root path bug** — all three adapters resolved `config.py`
   one directory level too high, so every live attempt failed with
   `internal_error`. Fixed centrally in `systems/base.py`
   (`repo_root()`, `load_app_config()`, `resolve_provider_key()`), with a
   regression test.
2. **Missing ITMS token telemetry** — the ITMS adapter read a `usage` dict
   that `ModelResponse` does not have, so ITMS recorded
   `usage_source=missing` for every attempt while Aider and Cline reported
   tokens. Fixed to read the provider-reported fields
   (`provider_prompt_tokens` / `provider_completion_tokens` /
   `provider_total_tokens`), with `cached_tokens_in` captured separately.

Neither defect changed the experimental design, the model, the task manifest,
the evaluator or the adapters' semantics. Both were fixed and the smoke test
re-run successfully **before** the 300-attempt run was launched.

---

## 4. Failures — all preserved, none hidden

Three of the 300 attempts failed. All three are in the dataset with full
artifacts.

| System | Task | Rep | Status | Category | Detail |
|---|---|---|---|---|---|
| ITMS | HE-EDIT-017 | 3 | `invalid_candidate` | `no_candidate` | model returned an empty response (2,584 tokens consumed) |
| ITMS | HE-EDIT-017 | 5 | `invalid_candidate` | `no_candidate` | model returned an empty response (2,584 tokens consumed) |
| ITMS | HE-EDIT-002 | 1 | `execution_failed` | `timeout` | `ReadTimeout` from the provider |

Consequences for the metrics, stated explicitly:

* these 3 attempts are **not** counted as passes;
* the 2 `no_candidate` attempts never reached the evaluator, so they are
  excluded from the evaluator denominator (97, not 100) and counted as
  failures in the completion rate (99/100);
* the 1 timeout is an **execution** failure, kept strictly separate from
  evaluator failures;
* repetition-level pass counts show the variation this produced —
  ITMS `[19, 20, 19, 20, 19]` out of 20 per repetition, Aider and Cline
  `[20, 20, 20, 20, 20]`.

---

## 5. Seed-control limitations (recorded per attempt)

| System | `seed_supported` | Distinct seeds applied | Note |
|---|---|---|---|
| ITMS | `true` | 100 | `seed` sent on the chat-completions payload. Provider honouring **not verified**. |
| Aider | `true` | 100 | `seed` injected via the model-settings file (`extra_params.seed`). Provider honouring **not verified**. |
| Cline | `false` | 0 | **Cline exposes no request-level seed.** Its five repetitions are identified by `repetition_id` only. |

Every record carries `seed_requested`, `seed_supported` and `seed_applied`
independently. The integrity validator has a dedicated check
(`no_seed_claimed_where_unsupported`) that fails if any record claims an
applied seed without support — it passed.

Additional disclosed asymmetries: Cline exposes **no temperature control**
(sampling is at the provider default), and the token counts come from
different sources (provider-reported for ITMS, tool-reported for Aider and
Cline), so they are never pooled.

---

## 6. Dataset and artefact locations

**Run directory**
`experiments/itms_aider_cline_v1/runs/itms_aider_cline_v1_live_20261021T171639Z/`

| File | Description |
|---|---|
| `attempts.jsonl` | 300 records, one per attempt, append-only |
| `attempts.csv` | the same 300 records, 48 stable columns |
| `summary.json` | the runner's summary (counts, spending limit, per-system totals) |
| `run_metadata.json` | the full run configuration (model, provider, temperature, seed policy, spending limit, manifest digest, platform) |
| `BENCHMARK_INTEGRITY_REPORT.md` | 32 independent validation checks, all passing |
| `ARTIFACT_INDEX.csv` | every archived artefact with its sha256 |
| `artifacts/<attempt_id>/` | 300 directories: `attempt.json`, `candidate.py`, `stdout.log`/`stderr.log` (Aider) or `cline.stdout.jsonl` (Cline) |
| `analysis/` | `analysis.json`, `fig1…fig10*.png`, `COMPARATIVE_RESULTS_REPORT.md`, `RUN_SUMMARY.md` |
| `COMPARATIVE_RESULTS_REPORT.md` | copy of the report next to the raw data |
| `RUN_SUMMARY.md` | copy of the run summary next to the raw data |

**Graphs** (all in `<run dir>/analysis/`)

1. `fig1_completion_rate_by_system.png` — denominator: all planned attempts
2. `fig2_evaluator_pass_rate_by_system.png` — denominator: evaluated attempts
3. `fig3_patch_application_reliability.png` — denominator: recorded attempts
4. `fig4_failure_categories_by_system.png`
5. `fig5_token_usage_by_system.png` — mean and median, with provenance in the title
6. `fig6_latency_by_system.png` — mean and median
7. `fig7_api_cost_by_system.png` — only attempts that reported a cost
8. `fig8_task_by_system_heatmap.png` — per-task pass rate, `n/a` for unevaluated cells
9. `fig9_repetition_variation.png` — pass rate per repetition index
10. `fig10_per_task_comparison.png` — per-task pass rate across the three systems

---

## 7. Integrity validation (independent re-derivation)

`integrity.py` re-derives every claim from the files on disk; it never trusts
the runner's summary. **32 checks, 0 failures**, 0 missing cells.

Notable checks: matrix completeness (300/300, no duplicates, one record per
cell); candidate hash ↔ stored candidate ↔ evaluated candidate; JSONL/CSV/
summary/metadata agreement; no mock records; failed and unevaluated attempts
retained; missing telemetry stored as `null` and never as `0`; seed
consistency per system; raw tool output archived consistently; and a check
that the run was written outside `pilot_runs/` and `results/`.

The historical 120-attempt dataset is **byte-identical to `HEAD`**
(`sha256 7e82697767ff703c…`) and `git status` for `pilot_runs/` and
`results/` is empty. The raw `attempts.jsonl` / `attempts.csv` of the new run
are also unchanged by the analysis step (verified by checksum before and
after).

---

## 8. Dashboard

A new read-only tab **“Comparative Benchmark Results”** was added to
`app.py`, backed by a new pure-reader module `comparative_viewer.py`
(no network client, no writer). It shows the dataset identity, integrity and
completion status, per-system metrics with explicit denominators, all ten
figures, the per-task table, the failure breakdown, and the limitations /
telemetry-provenance disclosure. All seven pre-existing tabs are preserved
and still render.

Launch:

```bash
cd /Users/noshinebnatadrita/Documents/499B && .venv/bin/python -m streamlit run app.py
```

Local URL: <http://localhost:8501/>

---

## 9. Open items for the user

1. **The `.env` credential-shaped first line** (Phase 1 finding B8) is still
   unresolved. It is a bare secret string with no `KEY=value` assignment, so
   the project's loaders ignore it, but it is a live secret in the working
   tree. **Safe remediation:** open `.env` in an editor, delete that first
   line, and rotate the credential with its issuer — no other file needs to
   change, and the run is unaffected because the key was never read from
   that line. I did not modify or rotate it automatically.
2. **Free-tier daily quota.** The OpenRouter key reports
   `is_free_tier: true` with `free_model_daily_requests.remaining: 0` at the
   time of the check, yet requests still succeeded. A future run may hit
   429s; those would be recorded as `execution_failed` / `quota_exceeded`.
3. **A second replicate** of the whole 300-cell matrix on a different model
   would be needed before generalising the efficiency ratios beyond this
   model. The current ratios are specific to
   `minimax/minimax-m3.1-flash:free` and these 20 tasks.
4. **Seed honouring is unverified** for ITMS and Aider. Verifying it would
   need a controlled repeated-request probe against the provider.
