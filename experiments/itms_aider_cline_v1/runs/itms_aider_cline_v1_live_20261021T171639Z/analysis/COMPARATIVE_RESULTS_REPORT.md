# Comparative Results Report — ITMS vs Aider vs Cline

## 1. Objective

To measure how three **real** code-editing systems — this project's ITMS agent, the `aider` CLI and the `cline` CLI — repair the same 20 HumanEval-derived defective modules, under identical task instructions, identical starting files, an identical hidden official test, an identical evaluator and an identical timeout policy.

## 2. Experimental design

- **Tasks:** 20 (`HE-EDIT-001` … `HE-EDIT-020`), each a 1:1 derivation of one pinned HumanEval problem.
- **Systems:** ITMS, Aider, Cline — all three are real, genuinely invoked tools.
- **Repetitions:** 5 per (task, system) cell.
- **Planned attempts:** 300.
- **Recorded attempts:** 300.
- **Model:** `minimax/minimax-m3.1-flash:free` via `openrouter` (temperature 0.0).
- **Seed policy:** `per_system_support`.
- **Run directory:** `/Users/noshinebnatadrita/Documents/499B/experiments/itms_aider_cline_v1/runs/itms_aider_cline_v1_live_20261021T171639Z`

### 2.1 Task-selection rationale

Task difficulty was assigned **a priori** by a fixed rubric over task-intrinsic features only (mutation category, indentation and nesting, anchor ambiguity, spec-vs-code divergence, target size). **No observed model output was used to select, rank or drop any task**, so the set cannot be biased by any system's performance. The rubric assigns tiers A (6 tasks), B (11) and C (3).

### 2.2 Seed control

| System | Seed supported | Distinct seeds applied |
|---|---|---|
| ITMS | yes | 100 |
| Aider | yes | 100 |
| Cline | no | 0 |

Cline exposes no request-level random seed, so its five repetitions are identified by `repetition_id` only. **No claim of independent seed control is made for Cline.** For ITMS and Aider a seed was sent with every request; whether the upstream provider honours it is not verified and is not claimed.

## 3. Methods

Each attempt runs in a fresh temporary workspace outside the git repository. The system receives the mutated module as `candidate.py` and the task's repair instruction. The hidden official test is not present in the workspace while the system runs. Afterwards the candidate is graded by `benchmarks/phase0/runner.run_official_test`, the same isolated, socket-blocked evaluator used for every system.

## 4. Metrics and their provenance

| Metric | Denominator | Provenance |
|---|---|---|
| Completion rate | all planned attempts | local measurement |
| Evaluator pass rate | evaluated attempts only | local evaluator |
| Patch-application rate | recorded attempts | local measurement |
| Failure categories | recorded attempts | local classification |
| Token usage | attempts with reported usage | provider-reported (ITMS) / tool-reported (Aider, Cline) |
| Latency | attempts with a measurement | local wall clock |
| API cost | attempts with a reported cost | tool-reported |

## 5. Quantitative results

| Metric | ITMS | Aider | Cline |
|---|---|---|---|
| Planned attempts | 100 | 100 | 100 |
| Recorded attempts | 100 | 100 | 100 |
| Execution completed | 99 | 100 | 100 |
| Execution failed | 1 | 0 | 0 |
| Evaluated | 97 | 100 | 100 |
| Test passed | 97 | 100 | 100 |
| Test failed | 0 | 0 | 0 |
| Completion rate (of planned) | 99.0% (99/100) | 100.0% (100/100) | 100.0% (100/100) |
| Evaluator pass rate (of evaluated) | 100.0% (97/97) | 100.0% (100/100) | 100.0% (100/100) |
| Patch applied (of recorded) | 97.0% (97/100) | 100.0% (100/100) | 100.0% (100/100) |
| Syntax errors | 0 | 0 | 0 |
| Timeouts | 1 | 0 | 0 |
| Truncations | 0 | 0 | 0 |
| Quota exceeded | 0 | 0 | 0 |
| Mean total tokens | 1,238 | 1,840 | 59,288 |
| Median total tokens | 1,102 | 1,452 | 50,918 |
| Mean input tokens | 657 | 1,057 | 57,420 |
| Mean output tokens | 581 | 783 | 1,868 |
| Mean latency (s) | 7.75 | 12.42 | 37.66 |
| Median latency (s) | 5.88 | 9.65 | 28.45 |
| Reported cost total (USD) | unavailable | unavailable | 0.0000 |

## 6. Failure analysis

| Failure category | ITMS | Aider | Cline |
|---|---|---|---|
| `no_candidate` | 2 | 0 | 0 |
| `timeout` | 1 | 0 | 0 |

## 7. Cost / token / latency trade-offs

- **ITMS** — mean 1,238 total tokens, mean 7.75 s per attempt, reported cost unavailable USD (0/100 attempts reported a cost).
- **Aider** — mean 1,840 total tokens, mean 12.42 s per attempt, reported cost unavailable USD (0/100 attempts reported a cost).
- **Cline** — mean 59,288 total tokens, mean 37.66 s per attempt, reported cost 0.0000 USD (100/100 attempts reported a cost).

### 7.1 Observed token and latency ratios

The figures below are computed directly from the recorded means. They are **not** a claim of a general efficiency property: the three systems use different context strategies and their token counts come from different sources (provider-reported for ITMS, tool-reported for Aider and Cline).

| Comparison | ITMS mean total tokens | Other system mean | Ratio (other / ITMS) |
|---|---|---|---|
| Aider vs ITMS | 1,238 | 1,840 | 1.5x |
| Cline vs ITMS | 1,238 | 59,288 | 47.9x |

- Mean latency: Aider 12.42 s vs ITMS 7.75 s (1.6x).
- Mean latency: Cline 37.66 s vs ITMS 7.75 s (4.9x).

These ratios describe **this** model, **these** tasks and **this** harness. They must not be generalised to other models, other task sets or other agent configurations, and they are not a claim that ITMS is more accurate — the evaluator pass rates in section 5 are the accuracy evidence, and they are reported with their own denominators.

## 8. Limitations and threats to validity

- **Context strategy differs by design.** ITMS shows a *skeleton*, Aider builds a repo map, Cline plans with its own tools. Token counts are therefore a property of the system, not of the task, and are only meaningful within a system.
- **Telemetry provenance differs.** ITMS token counts are provider-reported; Aider's and Cline's are tool-reported summaries. They are not pooled into a single cross-system total.
- **Cline has no temperature control** and no seed, so its sampling is at the provider default and its repetitions are not seed-controlled.
- **Latency includes tool overhead** (Aider's diff loop, Cline's planning and tool calls) and is a system-level cost, not a pure model-latency measurement.
- **The task set is not HumanEval.** These are synthetic single-mutation repair tasks derived from pinned HumanEval problems. The scores must never be presented as HumanEval scores.
- **Task difficulty is a predicted mechanism**, not an observed failure probability; no observed data was used in selection.

## 9. Conclusions

ITMS completed 99/100 planned attempts (99.0%), passed 97/97 evaluated attempts (100.0%), used a mean of 1,238 total tokens and a mean of 7.75 s per attempt. Aider completed 100/100 planned attempts (100.0%), passed 100/100 evaluated attempts (100.0%), used a mean of 1,840 total tokens and a mean of 12.42 s per attempt. Cline completed 100/100 planned attempts (100.0%), passed 100/100 evaluated attempts (100.0%), used a mean of 59,288 total tokens and a mean of 37.66 s per attempt.

## 10. Figures

- `fig1_completion_rate_by_system.png`
- `fig2_evaluator_pass_rate_by_system.png`
- `fig3_patch_application_reliability.png`
- `fig4_failure_categories_by_system.png`
- `fig5_token_usage_by_system.png`
- `fig6_latency_by_system.png`
- `fig7_api_cost_by_system.png`
- `fig8_task_by_system_heatmap.png`
- `fig9_repetition_variation.png`
- `fig10_per_task_comparison.png`
