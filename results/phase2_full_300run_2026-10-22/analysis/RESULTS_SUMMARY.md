# ITMS code-editing benchmark — analysis

- Run directory: `/Users/noshinebnatadrita/Documents/499B/results/phase2_full_300run_2026-10-22`
- Run id: `8c886293d46d`
- Mode: **live** | is_measurement: True
- Requested model id: `minimax/minimax-m3.1-flash:free`
- Served model ids: ['minimax/minimax-m3.1-flash:free']

## Denominators

The four counts below are different numbers and are never merged: a cell can be *planned* and *requested* and still never be *evaluated*.

| Quantity | Value |
|---|---|
| tasks | 20 |
| methods | 3 |
| reps_per_task | 5 |
| planned_attempts | 300 |
| scheduled_attempts | 300 |
| provider_requests_issued | 300 |
| completed_attempts | 300 |
| recorded_attempts | 300 |
| evaluated_attempts | 84 |
| candidate_produced_attempts | 84 |
| provider_generation_success_attempts | 84 |
| unevaluated_attempts | 216 |
| incomplete_attempts | 0 |
| missing_attempts | 0 |

## Provider generation versus official evaluation

| Stage | Attempts |
|---|---|
| provider_requests_issued | 300 |
| provider_answered | 84 |
| provider_error | 216 |
| candidate_produced | 84 |
| officially_evaluated | 84 |
| evaluator_pass | 83 |
| evaluator_fail | 1 |
| never_evaluated | 216 |
| incomplete | 0 |

> A provider response is not a benchmark pass. Only an attempt that reached the official evaluator and passed it counts as a pass.

## Per-method results

| Method | Planned | Completed | Evaluated | Evaluator passes | Pass rate (of evaluated) | Provider errors | Failures | Truncations | Mean tokens | Median tokens | Mean latency (ms) | Median latency (ms) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| full_file_regeneration | 100 | 100 | 24 | 23 | 95.8% | 76 | 77 | 0 | 1102 | 1036 | 15704 | 5847 |
| itms_ast_splice | 100 | 100 | 29 | 29 | 100.0% | 71 | 71 | 0 | 1423 | 1080 | 17785 | 15748 |
| search_replace_patch | 100 | 100 | 31 | 31 | 100.0% | 69 | 69 | 0 | 992 | 919 | 16052 | 7237 |

- Completion rate over the full schedule: full_file_regeneration=23.0%, itms_ast_splice=29.0%, search_replace_patch=31.0%
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
| `jsonl_csv_same_attempt_ids` | PASS | 300 records vs 300 csv rows |
| `jsonl_csv_same_status` | PASS | no status mismatch |
| `attempt_ids_unique` | PASS | 300 unique of 300 |
| `summary_recorded_matches` | PASS | summary=300 files=300 |
| `summary_planned_matches` | PASS | planned=300 recorded=300 |
| `artifacts_present_for_every_attempt` | PASS | all present |
| `candidate_hashes_match_artifacts` | PASS | all match |
| `prompt_persisted_when_supported` | PASS | 300/300 attempts carry a stored prompt |
| `no_mock_records_in_live_run` | PASS | 0 mock attempts |
| `failed_and_unevaluated_attempts_retained` | PASS | 217 non-success attempts present in the records |
| `missing_telemetry_stored_as_null` | PASS | no estimate stored in a provider-reported field |
| `single_served_model` | PASS | served ids: ['minimax/minimax-m3.1-flash:free'] |
| `output_cap_consistent_across_strategies` | PASS | caps in use: [4096] |
| `seed_consistent_per_cell` | PASS | 300 cells, 0 with conflicting seeds |
| `seed_honour_never_claimed_without_probe` | PASS | seed_honour_verified is false for every attempt |
| `cost_either_reported_or_explicitly_unavailable` | PASS | 0/300 attempts carry a provider-reported cost |

## Limitations that MUST be disclosed

- The provider reported no cost for this run, so the cost is unavailable. It is not zero and no estimate is substituted.
- Whether the provider honoured the request-level seed is unverified: no repeated-request probe was run.
- The evaluation gate is a scrubbed-environment subprocess, not a secure OS-level sandbox.

