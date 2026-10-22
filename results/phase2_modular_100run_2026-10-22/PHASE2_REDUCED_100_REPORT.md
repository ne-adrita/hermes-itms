# CSE499B — Phase 2 Reduced Live Benchmark: 100 Requests on the Modular Task Family

**Status: COMPLETE. 100 of 100 planned attempts requested, 100 recorded, 100
officially evaluated, 100 passed the official evaluator. Zero provider
errors, zero truncations, zero retries, zero cost reported.**

- Run directory: `results/phase2_modular_100run_2026-10-22/`
- Run id: see `run_metadata.json`
- Mode: `live` | `is_measurement: true` | **no** mock records
- Integrity: **all 16 checks PASSED** (`benchmarks/analyze_run.py`, exit 0)
- Dataset: `benchmarks/data/modular` (the new representative family)
- **This is NOT 20 tasks × 5 repetitions × 3 methods.** That design needs 300
  requests. This run used a 100-request reduced schedule and is described as
  such throughout.

---

## 1. Why a reduced schedule, and what had to change

### 1.1 The runner could not express this schedule

`benchmarks/benchmark/experiment.py::build_schedule` builds the strict cross
product `reps × tasks × methods`. With 20 tasks and 3 methods the only
reachable plan sizes are **60** (1 repetition) and **120** (2 repetitions).
No combination of `--tasks`, `--methods` and `--reps` yields exactly 100
cells *and* covers all 20 tasks with all 3 methods. The runner's request
cap (`--max-attempts`) was already exact and was not the problem; the
**schedule** was.

### 1.2 The minimal change

One **additive** option, `--schedule-file`, was added to
`benchmarks/benchmark_runner.py`, plus the corresponding support in
`ExperimentRunner`:

* `--schedule-file` loads a JSON list of `{"task_id", "method", "rep"}`
  cells. The file is hashed and its digest is recorded in the run metadata.
* The cells are validated **before** the live preflight and before the
  operator confirmation: unknown task ids, unknown methods, repetitions
  below 1 and duplicate cells are all refused with a non-zero exit and
  **no request is made**.
* The planned-attempt count, the per-method scheduled denominators and the
  seed positions all follow the explicit schedule.
* **Nothing else changed**: the provider layer, the budget gate, the
  official evaluator, the three editing adapters, the records, the artefact
  store and the analysis tool are untouched. The default cross-product
  behaviour is unchanged (verified by a regression test: `--reps 5` still
  plans 300 cells).

The schedule itself is produced by a version-controlled generator,
`benchmarks/phase2/make_reduced_schedule.py`, and committed as
`benchmarks/phase2/schedules/modular_reduced_100_v1.json`
(sha256 `cf90e2ae0fcd81ec404662171d99fa71d0e14c55636b38d4021b46dd77bf833e`).

---

## 2. The schedule (verified before the first request)

| Property | Value |
|---|---|
| Planned cells | **100** (all unique) |
| Per method | `full_file_regeneration` **34**, `search_replace_patch` **33**, `itms_ast_splice` **33** |
| Coverage | **every** task × **every** method at least once (60 cells) |
| Attempts per task | **exactly 5** for all 20 tasks (3 base + 2 extra) |
| Repetition 1 cells | 60 |
| Repetition 2 cells | 40 |
| Cells per (task, method) | 40 cells attempted twice, 20 cells attempted once |
| Execution order | seeded shuffle (seed `20261024`), so the three methods are interleaved |
| Request-level seed | base `991000` + cell position → 100 unique seeds |

**The repetition imbalance is a consequence of the 100-request budget and
is reported, not hidden:** the plan is *not* 5 repetitions per
(task, method) cell. 40 of the 60 (task, method) cells carry two attempts
and 20 carry one. Because the per-task total is exactly 5 for all 20
tasks, the imbalance is balanced at the task level; it is the *within-task*
allocation across methods that is uneven.

Verification before execution (all local, no request spent):

```
$ python benchmarks/phase2/make_reduced_schedule.py --out .../modular_reduced_100_v1.json
cells              : 100
cells per method   : {'full_file_regeneration': 34, 'search_replace_patch': 33, 'itms_ast_splice': 33}
attempts per task  : min=5 max=5 over 20 tasks

$ python benchmarks/benchmark_runner.py --dry-run --mode live ... --schedule-file ... --max-attempts 100
planned attempts    : 100
max attempts        : 100 (hard request cap)
live preflight: OK (credentials and model id resolved)
dry run: 100 attempts scheduled, no provider contacted.
dry run: cells per method: {'full_file_regeneration': 34, 'search_replace_patch': 33, 'itms_ast_splice': 33}
dry run: attempts per task: min=5 max=5 over 20 task(s)
dry run: every task is covered by every method: True
```

### 2.1 Request accounting

| Request class | Count | Notes |
|---|---|---|
| **Model-inference requests (chat completions)** | **100** | the experimental budget; 34 / 33 / 33 |
| Connectivity probe | 1 × `GET /models` | metadata only, **0 chat completions, 0 tokens**; run before the experiment so a dead network could not eat the budget |
| Dispatcher model-catalog check | 1 × `GET /models` | issued once per run by the dispatcher; the runner announces it in advance; **0 chat completions, 0 tokens** |
| **Total HTTP requests to the provider** | **102** | of which **100 are inference requests** |
| Retries | **0** | `--max-retries 0`; `retry_count = 0` on all 100 attempts |
| Requests that produced no response | **0** | every attempt received an HTTP response |

The project's own accounting for the earlier pilot used the same convention
("9 chat completions + 1 model-catalog `GET /models`"). No request outside
the table above was made, and **no chat-completion request was spent on a
probe**.

---

## 3. Setup

| Item | Value |
|---|---|
| Provider | `openrouter` (declared = effective) |
| Requested model | `minimax/minimax-m3.1-flash:free` (pinned, `:free` route) |
| Served model | `minimax/minimax-m3.1-flash:free` — identical on **100/100** attempts (single served model) |
| Temperature | `0.2` (identical for all three methods) |
| Output-token cap | `4096` — **one** value across all three methods |
| Request timeout / evaluation timeout | `60 s` / `10 s` |
| Wall clock | 934 106 ms ≈ **15.6 min** |
| Task-set digest | `da4eb6b1857a3706a0f83ae09804f08c32ad430a9e225210410dd6793a2edab9` |
| Evaluation isolation | macOS Seatbelt profile (no network, home/project directory unreadable, POSIX rlimits) |
| Budget gate | `max_attempts 100`, `max_spend_usd 1.00` (inert), free-model guard on |

---

## 4. Results

### 4.1 Counts (never merged)

| Quantity | Value |
|---|---|
| Planned | 100 |
| Requested (provider requests issued) | **100** |
| Completed (attempt records) | **100** |
| Officially evaluated | **100** |
| Evaluator pass | **100** |
| Evaluator fail | 0 |
| Provider error | 0 |
| Truncation | 0 |
| Incomplete | 0 |
| Missing / not-executed cells | 0 |

`budget.requests_issued = 100`, `not_executed_cells = []`: the run consumed
exactly the approved budget and not one request more.

> **Reading the two budget records.** `run_metadata.json` is written *before*
> the first request, so its `budget.requests_issued` is `0` — that file is a
> pre-flight snapshot of the configuration. The **post-run** budget state is
> in `summary.json` (`requests_issued: 100`, `not_executed_cells: []`), and
> that is the authoritative one. The 100 request records in `attempts.jsonl`
> agree with it.

### 4.2 Per-method comparison (denominator = evaluated attempts)

| Method | Planned | Completed | Evaluated | Passes | **Pass rate (of evaluated)** | Provider errors | Truncations |
|---|---|---|---|---|---|---|---|
| `full_file_regeneration` | 34 | 34 | 34 | 34 | **100 %** | 0 | 0 |
| `search_replace_patch` | 33 | 33 | 33 | 33 | **100 %** | 0 | 0 |
| `itms_ast_splice` | 33 | 33 | 33 | 33 | **100 %** | 0 | 0 |

Paired task-level comparison (bootstrap over the 20 tasks, seed 20260101):
**mean difference 0.00 and 95 % CI [0.00, 0.00] for all three pairs,
20/20 task ties.** The three methods are *indistinguishable on
correctness* on this task set.

### 4.3 Efficiency (this is where the methods differ)

| Method | mean output tokens | median output tokens | mean input tokens | mean total tokens | median total tokens | mean provider latency |
|---|---|---|---|---|---|---|
| `full_file_regeneration` | 2 178 | 2 074 | 2 286 | 4 465 | 4 415 | 14 030 ms |
| `search_replace_patch` | **295** | **238** | 2 287 | 2 582 | 2 485 | **5 298 ms** |
| `itms_ast_splice` | 539 | 417 | **757** | **1 295** | **1 135** | 8 175 ms |

Derived comparisons:

| Comparison | Output tokens | Total tokens | Latency |
|---|---|---|---|
| `search_replace` vs `full_file` | **−86.5 %** | −42.2 % | **−62.2 %** |
| `itms_ast_splice` vs `full_file` | −75.3 % | **−71.0 %** | −41.7 % |
| `itms_ast_splice` vs `search_replace` | **+82.7 % (ITMS emits MORE)** | −49.8 % | **+54.3 % (ITMS is SLOWER)** |

**The measured data does not support the Phase 2 thesis on this task set.**
The thesis says ITMS wins because it edits a small region of a large file.
Measured here:

* **Search/Replace is the most output-efficient method**, emitting 45.2 %
  fewer output tokens than ITMS (295 vs 539 mean). ITMS is *not* the cheapest
  method in output tokens.
* **Search/Replace is also the fastest** (5 298 ms vs 8 175 ms provider
  latency; ITMS is 54.3 % slower than search/replace).
* ITMS's *total*-token advantage over search/replace (−49.8 %) is driven by
  its **input** tokens (757 vs 2 287 mean, −66.9 %), not by a cheaper edit.
  A large part of that gap is a **harness property, not an editing
  property** — see §5.2.
* ITMS *is* clearly cheaper and faster than **full-file regeneration**
  (−75.3 % output tokens, −71.0 % total tokens, −41.7 % latency). That is
  the only advantage the data supports, and it is an advantage of *any*
  patch-style method over regenerating the whole file.

### 4.4 Prompt-length difference (measured from the persisted prompts)

The bytes in `artifacts/<id>/prompt.txt` are the exact requests that were
sent:

| Method | prompt bytes (mean) | median | min | max |
|---|---|---|---|---|
| `full_file_regeneration` | 8 779 | 8 200 | 7 707 | 10 302 |
| `search_replace_patch` | 8 699 | 8 165 | 7 767 | 10 362 |
| `itms_ast_splice` | **2 378** | 2 312 | 1 985 | 3 062 |

The ITMS prompt is ~3.7× smaller because the ITMS adapter builds a
**pruned skeleton** (`prune_unreachable=True`): the model is shown the
target node and the module header, not the whole 200–280 line file. The
three methods therefore do **not** receive the same amount of input
context. This is pre-existing ITMS behaviour, not something this dataset
introduced, but on the modular family it is now a large and measurable
asymmetry.

### 4.5 Per-task results

Every task passed on every attempt of every method (100/100). The per-task
attempt counts are uneven by design (§2): tasks `MOD-EDIT-001…006` give the
extra attempts to `search_replace_patch` and `itms_ast_splice`,
`MOD-EDIT-007…013` to `full_file_regeneration` and `itms_ast_splice`, and
`MOD-EDIT-014…020` to `full_file_regeneration` and `search_replace_patch`.

---

## 5. Limitations that must be disclosed

### 5.1 Small sample and a ceiling effect

33–34 attempts per method, 1 or 2 attempts per (task, method) cell, 20
tasks. All three methods pass **100 %** of what they evaluated, so the
task set **does not discriminate on correctness** either. A 100 % ceiling
means the experiment can compare *efficiency* but says nothing about
accuracy. The paired bootstrap interval is [0.00, 0.00] because there is no
difference to estimate, not because the sample is tight.

### 5.2 The prompt-length asymmetry is a confound for the efficiency claim

The three methods saw 8 779 / 8 699 / **2 378** prompt bytes. The
input-token gap between ITMS and search/replace (−66.9 %) is a property of
the ITMS adapter's context pruning. Without controlling for it, the
"total tokens" comparison flatters ITMS. The **output-token** comparison
is the fair one for the thesis, and it points the other way.

To make the comparison airtight, the recommended next step is a **fourth
arm**, `itms_ast_splice_unpruned` (`prune_unreachable=False`), in which
ITMS sees the whole file. That arm is **not** implemented in this work.

### 5.3 The authored 20-task dataset

The modular family was written for this project: 5 clean, idiomatic modules
(`text_metrics`, `interval_math`, `inventory_math`, `grid_geometry`,
`rate_limiting`), 20 target functions. It satisfies the pre-registered
representative-task rule (204–277 lines, target ratio 0.057–0.150, 15–17
functions, 1 class, 20/20 qualifying) — but it is **clean, authored code**,
not a real repository. It does not contain the messy reality of production
code, and 5 modules is a small corpus. **No conclusion from this run
generalises beyond these 20 tasks.**

### 5.4 Other disclosures

* **Cost: unavailable.** The provider reported a cost on **0 / 100**
  attempts. The cost is *not* zero and no estimate is substituted. The
  `$1.00` reported-spend cap was therefore inert
  (`spend_cap_effective: false`); the attempt cap was the binding guard.
* **Seed honour: unverified.** `seed_honour_verified = false` for every
  attempt; no repeated-request probe was run, so honouring is never
  claimed. The seed was *sent* on all 100 attempts.
* **Isolation is best-effort**, not a security boundary: no container or VM,
  macOS Seatbelt + scrubbed environment + POSIX rlimits only.
* **This is not HumanEval.** These are not HumanEval tasks and no
  HumanEval score can be derived from them.
* **One model, one route.** `minimax/minimax-m3.1-flash:free` only. No
  claim is made about a paid model or about any other model.

---

## 6. What this run establishes — and what it does not

**Establishes**

* The three editing strategies can be driven end-to-end over a
  **representative** task set (one that satisfies the preregistered rule)
  with 100 live requests, **every** attempt evaluated, and **no** provider
  error, truncation or retry.
* On that task set the three methods are **statistically indistinguishable
  in correctness** (100 % each, CI [0.00, 0.00] on all pairs).
* The **measured efficiency ranking is
  `search_replace_patch` > `itms_ast_splice` > `full_file_regeneration`
  for output tokens and for latency**, and
  `itms_ast_splice` > `search_replace_patch` > `full_file_regeneration`
  for total tokens (the last driven by the input-token asymmetry).
* The harness is sound on this family: 100/100 candidates were
  syntactically valid, 100/100 were evaluated, **0** candidate-digest
  mismatches, 0 redacted candidates, 0 artifact failures, 0 mock records,
  one shared output cap, one served model, 100 unique seeds.

**Does not establish**

* That any method is **more accurate** — there is no accuracy difference to
  establish (100 % ceiling).
* That **ITMS is superior**. On the measured output tokens and latency it
  is *behind* search/replace, and its total-token lead is partly an
  artefact of the harness giving it a smaller prompt.
* Any **cost** comparison.
* Anything about **real repositories**, other models, or the Phase 2 thesis
  in general. What it does establish is that the thesis is **not supported
  by this measurement on this task set** — which is a legitimate and
  reportable finding, not a failure of the experiment.

---

## 7. Artefacts

| Artefact | Path (relative to the run directory) |
|---|---|
| Per-attempt records | `attempts.jsonl` (canonical), `attempts.csv` |
| Run summary | `summary.json` |
| Run metadata (config, schedule + digest, isolation, budget) | `run_metadata.json` |
| Machine-readable analysis | `analysis/analysis.json` |
| Per-attempt analysis CSV | `analysis/attempts_analysis.csv` |
| Three-method comparison CSV | `analysis/method_comparison.csv` |
| Auto-generated summary | `analysis/RESULTS_SUMMARY.md` |
| **This report** | `PHASE2_REDUCED_100_REPORT.md` |
| Per-attempt artefacts (100 dirs × 5 files) | `artifacts/<attempt_id>/{attempt.json,prompt.txt,prompt.json,candidate.py,raw_response.json}` |
| Figures (11) | `analysis/figures/fig1…fig11*.png` |

Figures: pass rate (fig1), completion rate (fig1b), success vs failure
(fig2), latency (fig3), mean+median token usage (fig4), per-task heatmap
(fig5), token distribution (fig6), failure categories (fig7), latency
decomposition (fig8), truncation (fig9), cost availability — "unavailable,
not zero" (fig10), outcome funnel including never-evaluated and incomplete
(fig11). Every figure is derived from the measured records of this run; no
simulated or dashboard data is mixed in.

---

## 8. Reproduction

```bash
# regenerate the schedule (deterministic) and check the plan
python benchmarks/phase2/make_reduced_schedule.py \
    --out benchmarks/phase2/schedules/modular_reduced_100_v1.json

# offline validation of the configuration (no request spent)
python benchmarks/benchmark_runner.py --dry-run --mode live \
    --provider openrouter --model minimax/minimax-m3.1-flash:free \
    --data-dir benchmarks/data/modular \
    --schedule-file benchmarks/phase2/schedules/modular_reduced_100_v1.json \
    --max-attempts 100 --max-retries 0 --max-spend-usd 1.00 \
    --require-free-model --max-output-tokens 4096 --temperature 0.2 \
    --seed 20261024 --seed-base 991000 \
    --evaluation-timeout 10 --request-timeout 60 \
    --output-dir <TMP>/preflight

# the live run (the command that produced this directory)
printf 'yes\n' | ITMS_EVAL_ISOLATION=seatbelt .venv/bin/python \
    benchmarks/benchmark_runner.py \
    --mode live --provider openrouter --model minimax/minimax-m3.1-flash:free \
    --data-dir benchmarks/data/modular \
    --schedule-file benchmarks/phase2/schedules/modular_reduced_100_v1.json \
    --max-attempts 100 --max-retries 0 --max-spend-usd 1.00 \
    --require-free-model --max-output-tokens 4096 --temperature 0.2 \
    --seed 20261024 --seed-base 991000 \
    --evaluation-timeout 10 --request-timeout 60 \
    --output-dir results/phase2_modular_100run_2026-10-22

# offline analysis and integrity validation (no provider contact)
python benchmarks/analyze_run.py \
    --run-dir results/phase2_modular_100run_2026-10-22 \
    --out-dir  results/phase2_modular_100run_2026-10-22/analysis
```
