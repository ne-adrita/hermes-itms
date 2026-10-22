# CSE499B — Phase 2 Live Benchmark: 300-Attempt Comparative Report

**Status: COMPLETE 300-ATTEMPT LIVE RUN, WITH A MAJOR MEASUREMENT LIMITATION.**
All 300 planned cells were requested and recorded. Only **84 of 300** reached the
official evaluator, because the operator's network connection to the provider
failed at 01:01 UTC and stayed down for the remainder of the run. The 216
remaining cells are recorded as **provider errors** and are **not** benchmark
passes. This report does not claim 300 completed *evaluations*.

- Run directory: `results/phase2_full_300run_2026-10-22/`
- Run id: `8c886293d46d`
- Mode: `live` | `is_measurement: true` | contains **no** mock records
- Analysis produced by the version-controlled tool
  `benchmarks/analyze_run.py` (exit code 0, **all 16 integrity checks passed**)

---

## 1. Experimental setup

| Item | Value |
|---|---|
| Provider | `openrouter` (declared = effective) |
| Requested model id | `minimax/minimax-m3.1-flash:free` (pinned, `:free` route) |
| Served model id | `minimax/minimax-m3.1-flash:free` — identical to the requested id on **84/84** answered attempts (single served model) |
| Temperature | `0.2` (identical for all three methods) |
| Output-token cap | `4096` — **one** value across all three methods (recorded per attempt) |
| Request timeout | `60 s` |
| Evaluation timeout | `10 s` |
| Retries | `0` (bounded policy; a failed request is recorded, never silently retried) |
| Run-order seed | `20261023` (deterministic schedule) |
| Request-level seed | base `990000`, one unique seed per cell (300 distinct seeds) |
| Seed honour | **unverified** — `seed_honour_verified = false`; no repeated-request probe was run, so honouring is never claimed |
| Hard attempt cap | `300` (`--max-attempts 300`) |
| Reported-spend cap | `$1.00` (`--max-spend-usd 1.00`) — **inert**, see §5 |
| Free-model guard | enabled (`--require-free-model`) |
| Wall clock | 4 957 686 ms ≈ **82.6 min** |
| Task-set digest | `92077bdca33b75b3ca3c7c15a55b237b6c1f5c79d1da20ebb0f9efb4d23d08f1` |

Exact command used (unchanged from the pre-registered protocol):

```bash
printf 'yes\n' | ITMS_EVAL_ISOLATION=seatbelt .venv/bin/python benchmarks/benchmark_runner.py \
  --mode live --provider openrouter --model minimax/minimax-m3.1-flash:free \
  --reps 5 --methods full_file_regeneration,search_replace_patch,itms_ast_splice \
  --max-attempts 300 --max-retries 0 --max-spend-usd 1.00 --require-free-model \
  --max-output-tokens 4096 --temperature 0.2 \
  --seed 20261023 --seed-base 990000 \
  --evaluation-timeout 10 --request-timeout 60 \
  --output-dir results/phase2_full_300run_2026-10-22
```

### Evaluator and its isolation

All three methods were evaluated by the **same** official Phase 0 evaluator
(`benchmarks/phase0/runner.py::run_official_test`) against the official
HumanEval-derived `check` function of each task. The official test is never
placed in a prompt. The candidate is executed in a separate OS process with:

- a scrubbed environment (`PATH`/`TMPDIR`/`LANG`/`PYTHON*` only — **no provider
  credential is inherited**),
- `PYTHONPATH` restricted to the evaluation directory, user site-packages
  disabled,
- an interpreter-level socket guard,
- a wall-clock timeout with process termination,
- a **macOS Seatbelt profile**: network denied by the kernel, the operator's
  home and project directories (and therefore `.env`) unreadable, writes
  confined to the evaluation directory,
- POSIX resource limits (`RLIMIT_CPU`, `RLIMIT_AS`, `RLIMIT_NPROC`, `RLIMIT_FSIZE`).

This is **best-effort isolation, not a security boundary**: there is no
container/VM boundary, and no file-system confinement beyond the rules above.

---

## 2. The schedule

| Quantity | Value |
|---|---|
| Tasks | 20 (`HE-EDIT-001` … `HE-EDIT-020`), all unique and loadable |
| Methods | 3 (`full_file_regeneration`, `search_replace_patch`, `itms_ast_splice`) |
| Repetitions per task per method | 5 |
| **Planned cells** | `20 × 3 × 5 = 300` |
| Unique (task, method, rep) cells in the schedule | **300** (verified: no duplicates, no omissions) |
| Cells per method | 100 / 100 / 100 — identical task set, identical repetition count |
| Cells per (task, method) pair | 5, for all 60 pairs |
| Schedule determinism | Verified: rebuilding with `--seed 20261023` reproduces the identical order |
| Task set | **Unchanged.** No task was dropped, replaced or re-scoped; no expected output was edited; the preregistered selection criteria were not touched. |

The 20 tasks were also re-validated before execution: every task's defective
source **fails** its official test (20/20), confirming the official evaluator
accepts each task and that the tasks are not trivially passing.

---

## 3. Actual results

### 3.1 The four counts (never merged)

| Quantity | Value |
|---|---|
| **Planned** attempts | **300** |
| **Requested** (provider requests issued) | **300** |
| **Completed** (attempt records written) | **300** |
| **Officially evaluated** (reached the official evaluator) | **84** |
| Never evaluated | 216 (all `provider_error`) |
| Incomplete attempts | 0 |
| Missing / not-executed cells | 0 — every planned cell was actually requested |
| Provider requests issued | 300 of the 300 cap; `not_executed_cells = []` |

### 3.2 Provider generation versus official evaluation

| Stage | Attempts |
|---|---|
| Provider requests issued | 300 |
| Provider answered (a response object was returned) | 84 |
| Provider error (no response) | 216 |
| Candidate produced and parsed | 84 |
| **Officially evaluated** | **84** |
| **Evaluator pass** | **83** |
| Evaluator fail | 1 |
| Never evaluated | 216 |
| Incomplete | 0 |

A generated candidate is **not** a benchmark pass. Only 83 of 300 attempts
passed the official evaluator.

### 3.3 Per-method comparison

| Method | Planned | Completed | Evaluated | Passes | Pass rate (of evaluated) | Provider errors | Failures | Truncations |
|---|---|---|---|---|---|---|---|---|
| `full_file_regeneration` | 100 | 100 | 24 | 23 | 95.8 % | 76 | 77 | 0 |
| `search_replace_patch` | 100 | 100 | 31 | 31 | 100 % | 69 | 69 | 0 |
| `itms_ast_splice` | 100 | 100 | 29 | 29 | 100 % | 71 | 71 | 0 |

Completion rate over the **full scheduled denominator** (planned = 100 each):
`full_file_regeneration` 23 %, `search_replace_patch` 31 %, `itms_ast_splice` 29 %.
These low rates are **not** method properties — they are the provider-outage
rate. The provider errors are spread evenly across the three methods
(76 / 69 / 71), so the outage did not favour or penalise any method.

### 3.4 Token usage (provider-reported only)

| Method | Attempts with provider-reported usage | Mean total tokens | Median total tokens |
|---|---|---|---|
| `full_file_regeneration` | 24 / 100 | 1 102 | 1 036 |
| `search_replace_patch` | 31 / 100 | 992 | 919 |
| `itms_ast_splice` | 29 / 100 | 1 423 | 1 080 |

Token counts are **provider-reported** (`usage_source = provider_reported` on
84 attempts). The 216 provider-error attempts carry **no** token data and are
recorded as *missing*, never as zero. Local estimates are stored in separate
fields and are never mixed into a provider-reported total.

### 3.5 Latency (measured)

| Method | Mean latency (all attempts) | Median latency (all attempts) | Mean provider latency (answered only) | Median provider latency (answered only) |
|---|---|---|---|---|
| `full_file_regeneration` | 15 704 ms | 5 847 ms | 7 023 ms | 3 842 ms |
| `search_replace_patch` | 16 052 ms | 7 237 ms | 5 099 ms | 4 191 ms |
| `itms_ast_splice` | 17 785 ms | 15 748 ms | 11 554 ms | 6 982 ms |

**Read the provider-only column.** The "all attempts" latency includes 216
attempts that never reached the provider and therefore contain a full
connection timeout; it measures the outage, not the method.

### 3.6 Truncation, output budget, model identity

- Truncated outputs: **0 / 300** for every method. The 4 096-token cap was
  never hit, so no result is a harness-budget artefact.
- One output cap (4 096) was used by all three strategies — recorded per attempt.
- A single served model id was observed across all 84 answered attempts.

### 3.7 Paired task-level comparison (bootstrap over tasks, seed 20260101)

| A vs B | Paired tasks | Mean difference (A − B) | Bootstrap 95 % CI | Task W / L / T |
|---|---|---|---|---|
| `full_file_regeneration` vs `itms_ast_splice` | 20 | −0.06 | [−0.19, +0.07] | 4 / 9 / 7 |
| `full_file_regeneration` vs `search_replace_patch` | 20 | −0.08 | [−0.23, +0.06] | 5 / 8 / 7 |
| `itms_ast_splice` vs `search_replace_patch` | 20 | −0.02 | [−0.14, +0.10] | 6 / 8 / 6 |

**Every confidence interval contains zero.** The three methods are
statistically indistinguishable on this task set with these data.

### 3.8 Per-task view

Per-task pass rate (fraction of the 5 repetitions that passed the official
evaluator). A value of `0.20` means 1 of 5 repetitions was actually evaluated
and passed; the other 4 repetitions are unmeasured (provider error), so these
per-task rates are **lower bounds**, not estimates of true per-task accuracy.

| Task | `full_file_regeneration` | `search_replace_patch` | `itms_ast_splice` |
|---|---|---|---|
| HE-EDIT-001 | 0.20 | 0.60 | 0.20 |
| HE-EDIT-002 | 0.20 | 0.40 | 0.20 |
| HE-EDIT-003 | 0.40 | 0.20 | 0.40 |
| HE-EDIT-004 | 0.20 | 0.20 | 0.20 |
| HE-EDIT-005 | 0.20 | 0.40 | 0.40 |
| HE-EDIT-006 | 0.00 | 0.80 | 0.40 |
| HE-EDIT-007 | 0.40 | 0.20 | 0.00 |
| HE-EDIT-008 | 0.00 | 0.60 | 0.20 |
| HE-EDIT-009 | 0.00 | 0.40 | 0.40 |
| HE-EDIT-010 | 0.40 | 0.40 | 0.00 |
| HE-EDIT-011 | 0.00 | 0.20 | 0.60 |
| HE-EDIT-012 | 0.40 | 0.00 | 0.40 |
| HE-EDIT-013 | 0.20 | 0.20 | 0.20 |
| HE-EDIT-014 | 0.20 | 0.20 | 0.40 |
| HE-EDIT-015 | 0.20 | 0.20 | 0.00 |
| HE-EDIT-016 | 0.20 | 0.20 | 0.20 |
| HE-EDIT-017 | 0.20 | 0.00 | 0.60 |
| HE-EDIT-018 | 1.00 | 0.40 | 0.40 |
| HE-EDIT-019 | 0.00 | 0.40 | 0.20 |
| HE-EDIT-020 | 0.20 | 0.20 | 0.40 |

---

## 4. What went wrong: the provider outage (the central limitation)

The run was healthy for its first ten minutes (**83 consecutive successful
attempts, 00:50–01:00 UTC**), then connectivity to `openrouter.ai` failed and
never recovered during the run:

| Failure | Count |
|---|---|
| `network_error` — `ConnectError` | 192 |
| `timeout` — `ConnectTimeout` | 18 |
| `network_error` — `RemoteProtocolError` | 4 |
| `timeout` — `ReadTimeout` | 2 |
| **Total provider errors** | **216** |

This is an **environment/network fault, not a property of any editing method**:
the same window affected all three methods almost equally (76 / 69 / 71), and
all 83 attempts that the provider did answer were successfully parsed,
evaluated and (with one exception) passed. `--max-retries 0` was used, so a
failed request was recorded as a failure rather than silently re-attempted.

**The 300-attempt budget was fully consumed** (`requests_issued = 300`,
`not_executed_cells = []`). Per the protocol, failed attempts were **not**
re-run to "make up" the missing evaluations; doing so would have exceeded the
approved request budget. The operator was informed and explicitly decided
**not** to authorise a continuation run.

---

## 5. Missing / unknown metrics (stated, not estimated)

| Metric | Status |
|---|---|
| **Monetary cost** | **UNAVAILABLE.** The provider reported a cost on **0 / 300** attempts. The cost is *not* zero and no estimate is substituted. Consequently the `$1.00` reported-spend cap was **inert** (`spend_cap_effective = false`) — the attempt cap was the only binding guard, exactly as the budget module documents. No dollar figure may be published as measured. |
| Token usage on 216 attempts | **Missing** (provider returned no response). Recorded as missing, never as 0. |
| Seed honour | **Unverified** (`seed_honour_verified = false` for every attempt). |
| Evaluated pass rate on 216 attempts | **Unknown** — the official evaluator never saw those candidates. |
| True per-task pass rate | **Unknown**; the per-task table is a lower bound. |

---

## 6. Task-set limitation (must be read before any conclusion)

**The current 20-task dataset may not test the preregistered research
hypothesis, and this experiment does not prove the hypothesis or establish any
general superiority of ITMS.**

Previous project analysis established that **none of the current 20 tasks
satisfy the preregistered representative-task criteria** (the preregistered
rule was expressed in measurable properties of a task file: `file_lines ≥ 200`,
`target_ratio ≤ 0.20`, `function_count ≥ 5`, `class_count ≥ 1`). The current
set is **0/20 qualifying**: every task is a single-function file, and the
target function occupies roughly 92–99 % of the file. The Phase 2 thesis —
that ITMS wins because it edits a small region of a large file — is therefore
**not exercised** by this dataset, and the token advantage the thesis rests on
is **absent** here.

This is why the ITMS token mean (1 423) is not lower than the baselines'
rather than dramatically lower: the strategy cannot demonstrate an advantage
that the task geometry does not offer. A new multi-function task family must be
built and validated through the Phase 0 pipeline before the hypothesis can be
tested. The dataset was nevertheless **retained unchanged** for this requested
comparison, as instructed.

The `PHASE2_FINAL_HANDOVER.md` conclusion stands and is reinforced by these
data: on this task set the three methods are statistically indistinguishable on
correctness (all bootstrap CIs contain 0), so the data supports at most an
**efficiency** comparison, and even that is confounded by the 72 % provider
outage.

---

## 7. Deviations from the plan

1. **Measurement coverage.** The plan expected 300 evaluated attempts; **84**
   were evaluated. Cause: provider network outage (§4). All 300 cells were
   still requested and recorded — the deviation is in *evaluation coverage*,
   not in the schedule.
2. **No continuation run.** The 216 unevaluated cells were deliberately not
   re-run, because that would have exceeded the approved 300-attempt budget.
3. **Provider-generation success ≠ benchmark success.** The report counts the
   official evaluator, not the HTTP 200, as the pass criterion.
4. **Latency is reported twice** (all attempts vs provider-answered only)
   because the outage contaminates the former.
5. Tooling changes made in support of this report (all in the version-controlled
   analysis layer, none in the evaluator or the task set): median token usage
   added to the per-method metrics; an explicit planned/requested/completed/
   evaluated/missing denominator block and a generation-vs-evaluation table
   added to the analysis bundle; a new `fig11` outcome-funnel figure; a
   `method_comparison.csv` emitted alongside the other outputs. The official
   evaluator, the task dataset and the provider layer were **not** modified.

---

## 8. What these results establish — and what they do not

**Establish:**

- The three editing strategies can be driven end-to-end through a 300-cell
  live schedule against a pinned, single served model with one shared output
  cap, one shared official evaluator and a shared prompt format, and the whole
  run is reproducible from a deterministic schedule and a recorded per-cell
  seed.
- The 300-attempt budget was enforced exactly: 300 requests issued, 0 cells
  silently skipped, 0 retries.
- Where the provider answered (84 attempts), the harness is sound: 84/84
  produced a syntactically valid candidate, 84/84 were evaluated, and
  **83/84 passed the official evaluator**. **Zero** truncations, zero
  malformed outputs, zero artifact-persistence failures, zero candidate-digest
  mismatches, zero mock contamination.
- On this task set, with these data, the three methods show **no statistically
  distinguishable difference in correctness** (all paired task-level bootstrap
  95 % CIs contain 0).
- The provider-side efficiency numbers are suggestive but under-powered:
  `search_replace_patch` had the lowest measured mean (992) and median (919)
  total tokens, `full_file_regeneration` the lowest median provider latency
  (3 842 ms), `itms_ast_splice` the highest on both. These are measured on
  24–31 answered attempts per method and are **not** a general ranking.

**Do not establish:**

- Any claim that one method is more **accurate** than another.
- Any general claim of ITMS **superiority**, and specifically **not** the
  "94 % output-token reduction" or "8× faster" claims: the task set does not
  contain the large-file/small-target geometry the thesis requires, and the
  measured numbers do not show such an advantage.
- Any **cost** comparison — the provider reported no cost.
- Any **generalisation** beyond these 20 single-function synthetic tasks.
- Any claim that the evaluation sandbox is a **secure** sandbox.
- Any claim that the provider honoured the request-level seed.

---

## 9. Artefacts

| Artefact | Path (relative to the run directory) |
|---|---|
| Per-attempt records (canonical) | `attempts.jsonl` |
| Per-attempt table (flat) | `attempts.csv` |
| Run summary (runner-generated) | `summary.json` |
| Run metadata (config, isolation, budget) | `run_metadata.json` |
| Machine-readable analysis bundle | `analysis/analysis.json` |
| Per-attempt analysis CSV | `analysis/attempts_analysis.csv` |
| Three-method comparison table | `analysis/method_comparison.csv` |
| Auto-generated summary | `analysis/RESULTS_SUMMARY.md` |
| This report | `PHASE2_300_ATTEMPT_REPORT.md` |
| Per-attempt artefacts (300 dirs) | `artifacts/<attempt_id>/{attempt.json,prompt.txt,prompt.json,candidate.py,raw_response.json}` |
| Figures | `analysis/figures/fig1…fig11*.png` |

### Figures

| File | Shows |
|---|---|
| `fig1_pass_rate_by_method.png` | Official-evaluator pass rate per method (denominator: evaluated attempts) |
| `fig1b_completion_rate_by_method.png` | Completion rate per method (denominator: scheduled attempts) |
| `fig2_success_vs_failure_by_method.png` | Success vs other outcomes per method |
| `fig3_latency_by_method.png` | Mean and median measured latency per method |
| `fig4_token_usage_by_method.png` | Mean and median provider-reported token usage per method |
| `fig5_task_by_method_heatmap.png` | Per-task pass rate by method (per-task comparison) |
| `fig6_token_distribution_by_method.png` | Token distribution per method (provider-reported only) |
| `fig7_failure_categories_by_method.png` | Failure categories per method (network errors, timeouts, test failures) |
| `fig8_latency_decomposition_by_method.png` | Provider latency vs total attempt latency per method |
| `fig9_truncation_by_method.png` | Attempts truncated by the output-token cap (0 for all methods) |
| `fig10_cost_availability.png` | Cost: **unavailable** — explicitly *not* a zero-cost result |
| `fig11_outcome_funnel_by_method.png` | Provider generation vs official evaluation vs never-evaluated vs incomplete |

No simulated or dashboard data is mixed into any of these figures; every value
is derived from the measured attempt records of this run.

---

## 10. Reproduction

```bash
# offline analysis + integrity validation (no provider contact)
.venv/bin/python benchmarks/analyze_run.py \
  --run-dir results/phase2_full_300run_2026-10-22 \
  --out-dir  results/phase2_full_300run_2026-10-22/analysis
```

Exit code `0` and `integrity: ALL CHECKS PASSED` (16/16) reproduce the numbers
above from the saved records alone.
