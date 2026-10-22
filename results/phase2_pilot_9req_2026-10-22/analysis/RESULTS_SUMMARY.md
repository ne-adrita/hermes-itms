# ITMS code-editing benchmark — analysis

- Run directory: `/Users/noshinebnatadrita/Documents/499B/results/phase2_pilot_9req_2026-10-22`
- Run id: `a9d45ea35a53`
- Mode: **live** | is_measurement: True
- Requested model id: `minimax/minimax-m3.1-flash:free`
- Served model ids: ['minimax/minimax-m3.1-flash:free']

## Denominators

| Quantity | Value |
|---|---|
| tasks | 3 |
| methods | 3 |
| reps_per_task | 1 |
| scheduled_attempts | 9 |
| recorded_attempts | 9 |
| evaluated_attempts | 9 |
| candidate_produced_attempts | 9 |

## Per-method results

| Method | n | Success | Evaluated | Evaluator pass rate | Completion rate | Mean latency (ms) | Median latency (ms) | Mean tokens (provider-reported) |
|---|---|---|---|---|---|---|---|---|
| full_file_regeneration | 3 | 3 | 3 | 100.0% | 100.0% | 11519 | 7689 | 1119 |
| itms_ast_splice | 3 | 3 | 3 | 100.0% | 100.0% | 14712 | 9706 | 1792 |
| search_replace_patch | 3 | 3 | 3 | 100.0% | 100.0% | 7311 | 8557 | 1149 |

## Truncation and output budget

- Output caps in use: [4096]
- One cap for every strategy: True
- Truncated attempts: 0 ({'full_file_regeneration': 0, 'itms_ast_splice': 0, 'search_replace_patch': 0})

## Cost

- **Unavailable**: the provider reported no cost for this run. The cost is not zero and no estimate is substituted.

## Integrity validation

| Check | Result | Detail |
|---|---|---|
| `jsonl_csv_same_attempt_ids` | PASS | 9 records vs 9 csv rows |
| `jsonl_csv_same_status` | PASS | no status mismatch |
| `attempt_ids_unique` | PASS | 9 unique of 9 |
| `summary_recorded_matches` | PASS | summary=9 files=9 |
| `summary_planned_matches` | PASS | planned=9 recorded=9 |
| `artifacts_present_for_every_attempt` | PASS | all present |
| `candidate_hashes_match_artifacts` | PASS | all match |
| `prompt_persisted_when_supported` | PASS | 9/9 attempts carry a stored prompt |
| `no_mock_records_in_live_run` | PASS | 0 mock attempts |
| `failed_and_unevaluated_attempts_retained` | PASS | 0 non-success attempts present in the records |
| `missing_telemetry_stored_as_null` | PASS | no estimate stored in a provider-reported field |
| `single_served_model` | PASS | served ids: ['minimax/minimax-m3.1-flash:free'] |
| `output_cap_consistent_across_strategies` | PASS | caps in use: [4096] |
| `seed_consistent_per_cell` | PASS | 9 cells, 0 with conflicting seeds |
| `seed_honour_never_claimed_without_probe` | PASS | seed_honour_verified is false for every attempt |
| `cost_either_reported_or_explicitly_unavailable` | PASS | 0/9 attempts carry a provider-reported cost |

## Limitations that MUST be disclosed

- The provider reported no cost for this run, so the cost is unavailable. It is not zero and no estimate is substituted.
- Whether the provider honoured the request-level seed is unverified: no repeated-request probe was run.
- The evaluation gate is a scrubbed-environment subprocess, not a secure OS-level sandbox.

