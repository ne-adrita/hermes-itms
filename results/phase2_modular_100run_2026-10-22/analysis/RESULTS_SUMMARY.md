# ITMS code-editing benchmark — analysis

- Run directory: `/Users/noshinebnatadrita/Documents/499B/results/phase2_modular_100run_2026-10-22`
- Run id: `eb1b0f4b58a9`
- Mode: **live** | is_measurement: True
- Requested model id: `minimax/minimax-m3.1-flash:free`
- Served model ids: ['minimax/minimax-m3.1-flash:free']

## Denominators

The four counts below are different numbers and are never merged: a cell can be *planned* and *requested* and still never be *evaluated*.

| Quantity | Value |
|---|---|
| tasks | 20 |
| methods | 3 |
| reps_per_task | 2 |
| planned_attempts | 100 |
| scheduled_attempts | 100 |
| provider_requests_issued | 100 |
| completed_attempts | 100 |
| recorded_attempts | 100 |
| evaluated_attempts | 100 |
| candidate_produced_attempts | 100 |
| provider_generation_success_attempts | 100 |
| unevaluated_attempts | 0 |
| incomplete_attempts | 0 |
| missing_attempts | 0 |
| cells_by_repetition | {'1': 60, '2': 40} |

## Provider generation versus official evaluation

| Stage | Attempts |
|---|---|
| provider_requests_issued | 100 |
| provider_answered | 100 |
| provider_error | 0 |
| candidate_produced | 100 |
| officially_evaluated | 100 |
| evaluator_pass | 100 |
| evaluator_fail | 0 |
| never_evaluated | 0 |
| incomplete | 0 |

> A provider response is not a benchmark pass. Only an attempt that reached the official evaluator and passed it counts as a pass.

## Per-method results

| Method | Planned | Completed | Evaluated | Evaluator passes | Pass rate (of evaluated) | Provider errors | Failures | Truncations | Mean tokens | Median tokens | Mean latency (ms) | Median latency (ms) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| full_file_regeneration | 34 | 34 | 34 | 34 | 100.0% | 0 | 0 | 0 | 4465 | 4415 | 14146 | 11988 |
| itms_ast_splice | 33 | 33 | 33 | 33 | 100.0% | 0 | 0 | 0 | 1295 | 1135 | 8291 | 4864 |
| search_replace_patch | 33 | 33 | 33 | 33 | 100.0% | 0 | 0 | 0 | 2582 | 2485 | 5419 | 4972 |

- Completion rate over the full schedule: full_file_regeneration=100.0%, itms_ast_splice=100.0%, search_replace_patch=100.0%
- The latency column above is measured over **every recorded attempt**, including the attempts that never reached the provider and therefore contain a full connection timeout. The provider-only latency, which is the meaningful comparison between the three methods, is in the latency decomposition figure.

## Truncation and output budget

- Output caps in use: [4096]
- One cap for every strategy: True
- Truncated attempts: 0 ({'full_file_regeneration': 0, 'itms_ast_splice': 0, 'search_replace_patch': 0})

## Cost

- **Unavailable**: the provider reported no cost for this run. The cost is not zero and no estimate is substituted.

## Integrity validation

| Check | Result | Detail |
|---|---|---|
| `jsonl_csv_same_attempt_ids` | PASS | 100 records vs 100 csv rows |
| `jsonl_csv_same_status` | PASS | no status mismatch |
| `attempt_ids_unique` | PASS | 100 unique of 100 |
| `summary_recorded_matches` | PASS | summary=100 files=100 |
| `summary_planned_matches` | PASS | planned=100 recorded=100 |
| `artifacts_present_for_every_attempt` | PASS | all present |
| `candidate_hashes_match_artifacts` | PASS | all match |
| `prompt_persisted_when_supported` | PASS | 100/100 attempts carry a stored prompt |
| `no_mock_records_in_live_run` | PASS | 0 mock attempts |
| `failed_and_unevaluated_attempts_retained` | PASS | 0 non-success attempts present in the records |
| `missing_telemetry_stored_as_null` | PASS | no estimate stored in a provider-reported field |
| `single_served_model` | PASS | served ids: ['minimax/minimax-m3.1-flash:free'] |
| `output_cap_consistent_across_strategies` | PASS | caps in use: [4096] |
| `seed_consistent_per_cell` | PASS | 100 cells, 0 with conflicting seeds |
| `seed_honour_never_claimed_without_probe` | PASS | seed_honour_verified is false for every attempt |
| `cost_either_reported_or_explicitly_unavailable` | PASS | 0/100 attempts carry a provider-reported cost |

## Limitations that MUST be disclosed

- The provider reported no cost for this run, so the cost is unavailable. It is not zero and no estimate is substituted.
- Whether the provider honoured the request-level seed is unverified: no repeated-request probe was run.
- The evaluation gate is a scrubbed-environment subprocess, not a secure OS-level sandbox.

