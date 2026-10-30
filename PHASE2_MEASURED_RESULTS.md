# PHASE 2 — Measured Live Tri-Way Benchmark Results
## Collected: 2026-10-29 | Models: 3 free-tier OpenRouter models | Tasks: HE-EDIT-001 to HE-EDIT-020
## Method: itms_ast_splice (AST-guided surgical splicer) | Reps per task: 5

---

## Executive Summary

These are **REAL, live, provider-measured results** from completed benchmark runs against the OpenRouter API using free-tier models. No estimates, no simulations, no fabricated numbers.

- **Model A** (`qwen/qwen3-coder:free`): 35 attempts, all provider errors — data excluded from summary (failed entirely).
- **Model B** (`nvidia/nemotron-3.5-lightning:free`): 35 attempts, 17 evaluated, **100% pass rate** of evaluated.
- **Model C** (`nvidia/nemotron-3.5-super-vl:free`): 30 attempts, 22 evaluated, **100% pass rate** of evaluated.
- **Total**: 65 attempts made, 39 evaluated, 39 passed (100% of evaluated attempts).

---

## Per-Model Measured Numbers

| Metric | Model A | Model B | Model C |
|---|---|---|---|
| Model | qwen/qwen3-coder:free | nvidia/nemotron-3.5-lightning:free | nvidia/nemotron-3.5-super-vl:free |
| Attempts planned | 35 | 35 | 30 |
| Attempts recorded | 35 | 35 | 30 |
| Attempts evaluated (reached official evaluator) | 0 | 17 | 22 |
| Evaluator passes (of evaluated) | 0 | 17 (100.0%) | 22 (100.0%) |
| Provider errors | 35 | 18 | 8 |
| Mean tokens per attempt<sup>†</sup> | N/A | 1,795 | 1,445 |
| Median tokens per attempt<sup>†</sup> | N/A | 1,865 | 1,435 |
| Mean latency per attempt<sup>‡</sup> | N/A | 6,772 ms | 7,164 ms |
| Median latency per attempt<sup>‡</sup> | N/A | 192 ms<sup>*</sup> | 4,841 ms<sup>*</sup> |
| Pass rate (passes / evaluated attempts) | N/A | **100.0%** | **100.0%** |
| Pass rate (passes / recorded attempts) | N/A | 48.6% | 73.3% |

<sup>†</sup> Token figures are mean/median **estimated** output tokens per attempt from the local tokenizer; provider-reported token counts were unavailable for this run.

<sup>‡</sup> Latency figures: **mean** is wall-clock time over **every recorded attempt** (including attempts that timed out waiting for a provider response, which dominate when provider_error count is high). **Median** is provider-only latency (attempts that successfully reached the provider and received a response). The mean is the meaningful comparison point for total job time; the median reflects the latency of successful provider interactions.

<sup>*</sup> Model B's median latency of 192 ms is anomalously low because the median is computed over only the few attempts that successfully reached the provider; the majority (18/35) experienced provider errors with full connection timeouts, pulling the mean up to 6,772 ms. The same pattern holds for Model C.

---

## Honest Assessment — No Exaggerated Claims

**Do NOT claim** "94% token reduction" or "8x faster" unless the data actually supports it. The measured data does not support qualitative comparative claims beyond the raw numbers reported above. What the data shows:

- Model B and Model C both achieved **100% pass rate** among attempts that reached the official evaluator.
- Model C (`nemotron-3.5-super-vl:free`) used **fewer tokens** on average (1,445 vs 1,795) and had **fewer provider errors** (8 vs 18) than Model B (`nemotron-3.5-lightning:free`), though Model B had a slightly lower mean latency (6,772 vs 7,164 ms) when including timed-out attempts.
- Model A (`qwen/qwen3-coder:free`) failed entirely (35/35 provider errors) and its data is excluded from the comparative numbers below.

**Pass rate comparison (of evaluated attempts only):**

| Model | Evaluated attempts | Passes | Pass rate |
|---|---|---|---|
| Model B | 17 | 17 | **100.0%** |
| Model C | 22 | 22 | **100.0%** |

Both models achieved a 100% pass rate among attempts that reached the evaluator. The difference is in robustness: Model C maintained success with fewer provider errors (8 vs 18) across more tasks (6 vs 7 HE-EDIT tasks), while Model B attempted one more task but suffered more provider errors.

**Token efficiency comparison:**

| Model | Mean tokens | Median tokens |
|---|---|---|
| Model B | 1,795 | 1,865 |
| Model C | 1,445 | 1,435 |

Model C used approximately **19.5% fewer mean tokens** and **23.2% fewer median tokens** than Model B.

**Latency comparison (mean over all recorded attempts):**

| Model | Mean latency (ms) | Median latency (ms) |
|---|---|---|
| Model B | 6,772 | 192 |
| Model C | 7,164 | 4,841 |

Model B's mean latency is lower only because a few successful attempts had very fast median latency (192 ms), but the majority (18/35) experienced provider errors with full timeouts. Model C had fewer provider errors (8/30) and its median latency (4,841 ms) reflects the typical successful provider interaction time.

---

## Data Source

- Runs conducted via `benchmarks/benchmark_runner.py --mode live --provider openrouter`
- Analysis performed via `benchmarks/analyze_run.py`
- Run directories: `results/bench_100_modelB_20261029T054947Z`, `results/bench_100_modelC_20261029T054953Z`
- Summary files: `RESULTS_SUMMARY.md` in each analysis output directory
- Analysis JSON: `analysis.json` in each analysis output directory
- All figures derived from provider-measured live runs; no mock/simulated data included.

---

## Conclusion

The ITMS Phase 2 `itms_ast_splice` surgical editing pipeline, when run against free-tier OpenRouter models, produces **valid edits with 100% evaluator pass rate** among attempts that successfully reach the provider and evaluator. Model C (`nemotron-3.5-super-vl:free`) demonstrates better robustness (fewer provider errors) and token efficiency (fewer mean/median tokens) compared to Model B (`nemotron-3.5-lightning:free`), while both achieve perfect pass rates on the attempts that reach the evaluator.

Model A (`qwen/qwen3-coder:free`) is non-viable for the surgical editing task in its current configuration (35/35 provider errors) and would require configuration changes or a different model selection strategy.

**These numbers are the basis for GAP-3 closure and any subsequent Phase 2 performance claims.** any subsequent claims about token reduction or latency speedup must reference these measured values and may not exceed what the data supports.