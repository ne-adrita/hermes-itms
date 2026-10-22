# Phase 2 Tri-Way Editing Benchmark — Live Pilot Report

**Project:** Hermes / ITMS capstone — `/Users/noshinebnatadrita/Documents/499B`
**Date:** 2026-10-22
**Status: SMALL LIVE PILOT — 9 model requests. NOT statistically conclusive.**
**No simulated data is mixed into this report.** Every number below was measured on a real provider response and re-derived from the saved run artefacts.

---

## 1. Setup

| Item | Value |
|---|---|
| Provider | `openrouter` → `https://openrouter.ai/api/v1` |
| Model (requested) | `minimax/minimax-m3.1-flash:free` (pinned, **not** a dynamic alias) |
| Model (served) | `minimax/minimax-m3.1-flash:free` — **identical on all 9 attempts** |
| Temperature / output-token cap | 0.2 / **4096**, applied identically to all three methods |
| Retries | **disabled** (`--max-retries 0`) so the request cap was absolute |
| Request timeout / evaluation timeout | 60 s / 10 s |
| Request-level seed | base 880000, one distinct seed per cell (880000–880008) |
| Run-order seed | 20261022 (deterministic shuffle) |
| Tasks | HE-EDIT-001, HE-EDIT-002, HE-EDIT-003 (the first three task ids in lexicographic order — not selected by outcome) |
| Methods | `full_file_regeneration`, `search_replace_patch`, `itms_ast_splice` |
| Repetitions | 1 per task per method → 3 × 3 × 1 = **9 attempts** |
| Evaluator | the shared official `check(candidate)` test of each task — **the same evaluator for all three methods** |
| Execution gate | scrubbed-environment subprocess (env scrubbed: no API key is visible to the candidate). **Not a secure OS-level sandbox** — the file system is not confined. |
| **Requests issued** | **9 chat completions + 1 model-catalog `GET /models`** (the catalog call is made once per run by the dispatcher). `retry_count` = 0 on every attempt. |
| **Reported cost** | **UNAVAILABLE** — the provider reported no cost for any of the 9 attempts (0/9). It is *not* zero and no estimate was substituted. The model is a `:free` route, so the expected bill is $0, but that is a property of the route, not a measurement. |

**Command executed**

```bash
printf 'yes\n' | .venv/bin/python benchmarks/benchmark_runner.py \
  --mode live --provider openrouter --model minimax/minimax-m3.1-flash:free \
  --tasks HE-EDIT-001,HE-EDIT-002,HE-EDIT-003 --reps 1 \
  --methods full_file_regeneration,search_replace_patch,itms_ast_splice \
  --max-attempts 9 --max-retries 0 --max-output-tokens 4096 \
  --seed 20261022 --seed-base 880000 --temperature 0.2 \
  --evaluation-timeout 10 --request-timeout 60 \
  --output-dir results/phase2_pilot_9req_2026-10-22
```

---

## 2. Results (9 live attempts, all three methods)

| Method | n | Repair success | Evaluated | Evaluator pass rate | Mean total tokens (provider-reported) | Mean attempt latency (ms) | Median latency (ms) | Truncated | Reported cost |
|---|---|---|---|---|---|---|---|---|---|
| Full-File Rewrite | 3 | 3 | 3 | 100.0 % (3/3) | 1 118.7 | 11 519 | 7 689 | 0 | unavailable |
| Search/Replace | 3 | 3 | 3 | 100.0 % (3/3) | 1 149.0 | 7 311 | 8 557 | 0 | unavailable |
| ITMS AST-Guided | 3 | 3 | 3 | 100.0 % (3/3) | 1 792.0 | 14 712 | 9 706 | 0 | unavailable |

**Per attempt (all 9, ordered by task and method)**

| Task | Method | Status | Official test | Input tok | Output tok | Total tok | Latency (ms) | Provider lat (ms) | Retries | Seed |
|---|---|---|---|---|---|---|---|---|---|---|
| HE-EDIT-001 | full_file_regeneration | repair_success | PASS | 688 | 702 | 1 390 | 24 288 | 24 103 | 0 | 880004 |
| HE-EDIT-001 | search_replace_patch | repair_success | PASS | 707 | 732 | 1 439 | 10 295 | 10 031 | 0 | 880005 |
| HE-EDIT-001 | itms_ast_splice | repair_success | PASS | 710 | 554 | 1 264 | 9 706 | 9 457 | 0 | 880001 |
| HE-EDIT-002 | full_file_regeneration | repair_success | PASS | 710 | 524 | 1 234 | 7 689 | 7 520 | 0 | 880000 |
| HE-EDIT-002 | search_replace_patch | repair_success | PASS | 729 | 572 | 1 301 | 8 557 | 8 335 | 0 | 880007 |
| HE-EDIT-002 | itms_ast_splice | repair_success | PASS | 733 | 2 371 | 3 104 | 27 720 | 27 587 | 0 | 880008 |
| HE-EDIT-003 | full_file_regeneration | repair_success | PASS | 566 | 166 | 732 | 2 580 | 2 347 | 0 | 880006 |
| HE-EDIT-003 | search_replace_patch | repair_success | PASS | 585 | 122 | 707 | 3 082 | 2 830 | 0 | 880002 |
| HE-EDIT-003 | itms_ast_splice | repair_success | PASS | 587 | 421 | 1 008 | 6 712 | 6 506 | 0 | 880003 |

**Failures: none.** 9/9 attempts produced a candidate, 9/9 parsed, 9/9 were evaluated by the official test, 9/9 passed. 0 truncations, 0 provider errors, 0 timeouts, 0 retries.

---

## 3. Context: the existing 120-attempt live run (unchanged on disk)

Re-analysed read-only with the same tooling (`results/analysis_historical_120run_2026-10-22/`). The run itself is byte-identical (sha256 of the whole tree: `6249fbae…559c` before and after).

| Method | n | Success | Evaluated | Evaluator pass rate | Completion rate (of scheduled) | Mean total tokens | Mean latency (ms) | Truncated |
|---|---|---|---|---|---|---|---|---|
| Full-File Rewrite | 40 | 39 | 39 | 100.0 % | 97.5 % | 1 091.8 | 5 139 | 1 |
| Search/Replace | 40 | 40 | 40 | 100.0 % | 100.0 % | 931.1 | 4 150 | 0 |
| ITMS AST-Guided | 40 | 35 | 35 | 100.0 % | 87.5 % | 1 411.8 | 9 912 | 5 |

---

## 4. What these results do and do not show

**Do not show (and are not claimed to show):**
* That any of the three methods is **more accurate**: every attempt that reached the official evaluator passed, in both datasets (9/9 and 114/114). The task set is too easy for this model to discriminate repair quality.
* That ITMS is **cheaper or faster**: on this task set it was neither. Its mean token count was the **highest** of the three in both runs, and its mean latency the highest in both. The reason is structural: the three tasks are single-function modules, so the target region is ~95 % of each file and there is nothing for a surgical edit to save. The Phase 2 thesis (a large file where the edited function is a small region) is **not exercised** by this task set.
* Any **cost** figure: the provider reported no cost (0/9 and 0/120). Cost is unavailable.

**Do show:**
* The **full live path works end to end** with the current code: 9/9 live requests, 9/9 evaluated, 9/9 passed, provider-reported tokens for every attempt, prompts, candidates and raw responses stored per attempt, and 16/16 integrity checks passing on the re-analysis.
* The **output-cap confound was removed** for the pilot: raising the cap from 2048 to 4096 (identically for all three methods) produced **0 truncations**, versus 6 truncations in the 120-attempt run at 2048 (5 of them in ITMS). That earlier difference was a harness artefact, not a method property.
* The **same model served all attempts** (served id == requested id on 9/9 and 120/120), so both datasets are same-model comparisons.

---

## 5. Missing / unknown metrics

| Metric | State |
|---|---|
| Cost | **Unavailable** — not reported by the provider (0/9, 0/120). Not zero; not estimated. |
| Seed honouring | **Unverified** — a request-level seed was sent on every attempt, but honouring cannot be proven without a repeated-request probe. |
| Cached tokens | Not reported by the provider. |
| Statistical power | **9 observations** (pilot) and 120 (historical) with a 100 % ceiling — no significance test is meaningful. |
| Isolated execution | The gate is a scrubbed-environment subprocess. The file system is **not** confined, so it must not be used with untrusted code. |

---

## 6. Where everything is saved

```
results/phase2_pilot_9req_2026-10-22/            <- the 9-request live pilot (new)
  attempts.jsonl / attempts.csv / summary.json / run_metadata.json
  artifacts/<attempt_id>/{attempt.json, prompt.txt, prompt.json, candidate.py, raw_response.json}
  analysis/analysis.json
  analysis/attempts_analysis.csv                 <- per-attempt machine-readable table
  analysis/RESULTS_SUMMARY.md
  analysis/figures/fig1..fig10*.png              <- 10 figures

results/analysis_historical_120run_2026-10-22/   <- read-only re-analysis of the 120-attempt run
  analysis.json / attempts_analysis.csv / RESULTS_SUMMARY.md / figures/

pilot_runs/2026-10-21_minimax-m3-1-flash-free_120_attempts/   <- HISTORICAL RUN, UNCHANGED
```

**Figures available for the supervisor**

| Figure | File |
|---|---|
| Official-evaluator pass rate | `analysis/figures/fig1_pass_rate_by_method.png` |
| Completion rate | `analysis/figures/fig1b_completion_rate_by_method.png` |
| Success vs other outcomes | `analysis/figures/fig2_success_vs_failure_by_method.png` |
| Measured latency (mean/median) | `analysis/figures/fig3_latency_by_method.png` |
| Input/output tokens (provider-reported) | `analysis/figures/fig4_token_usage_by_method.png`, `fig6_token_distribution_by_method.png` |
| Latency decomposition (provider vs total) | `analysis/figures/fig8_latency_decomposition_by_method.png` |
| Truncations | `analysis/figures/fig9_truncation_by_method.png` |
| Cost | `analysis/figures/fig10_cost_availability.png` — an explicit **"unavailable"** panel, not a `$0` bar |
| Task × method heat map | `analysis/figures/fig5_task_by_method_heatmap.png` |

---

## 7. Next step (not taken, needs approval)

A 300-attempt run is **not** recommended on the current task set: it would reproduce the same 100 % ceiling and the same absent token advantage, and would spend 300 requests to demonstrate nothing new. The scientifically useful next step is to **build a multi-function task family** (files of 200+ lines where the target is ≤ 20 % of the file), validate it through the Phase 0 pipeline, and only then repeat the pilot on it with the same 9-request cap.
