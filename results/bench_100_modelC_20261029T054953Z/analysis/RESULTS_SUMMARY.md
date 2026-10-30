# ITMS code-editing benchmark — analysis

- Run directory: `/Users/noshinebnatadrita/Documents/499B/results/bench_100_modelC_20261029T054953Z`
- Run id: `68cd74a04e48`
- Mode: **live** | is_measurement: True
- Requested model id: `nvidia/nemotron-3.5-super-vl:free`
- Served model ids: ['nvidia/nemotron-3.5-super-vl:free']

## Denominators

The four counts below are different numbers and are never merged: a cell can be *planned* and *requested* and still never be *evaluated*.

| Quantity | Value |
|---|---|
| tasks | 6 |
| methods | 1 |
| reps_per_task | 5 |
| planned_attempts | 30 |
| scheduled_attempts | 30 |
| provider_requests_issued | 30 |
| completed_attempts | 30 |
| recorded_attempts | 30 |
| evaluated_attempts | 22 |
| candidate_produced_attempts | 22 |
| provider_generation_success_attempts | 22 |
| unevaluated_attempts | 8 |
| incomplete_attempts | 0 |
| missing_attempts | 0 |
| cells_by_repetition | {'1': 6, '2': 6, '3': 6, '4': 6, '5': 6} |

## Provider generation versus official evaluation

| Stage | Attempts |
|---|---|
| provider_requests_issued | 30 |
| provider_answered | 22 |
| provider_error | 8 |
| candidate_produced | 22 |
| officially_evaluated | 22 |
| evaluator_pass | 22 |
| evaluator_fail | 0 |
| never_evaluated | 8 |
| incomplete | 0 |

> A provider response is not a benchmark pass. Only an attempt that reached the official evaluator and passed it counts as a pass.

## Per-method results

| Method | Planned | Completed | Evaluated | Evaluator passes | Pass rate (of evaluated) | Provider errors | Failures | Truncations | Mean tokens | Median tokens | Mean latency (ms) | Median latency (ms) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| itms_ast_splice | 30 | 30 | 22 | 22 | 100.0% | 8 | 8 | 0 | 1445 | 1435 | 7164 | 4841 |

- Completion rate over the full schedule: itms_ast_splice=73.3%
- The latency column above is measured over **every recorded attempt**, including the attempts that never reached the provider and therefore contain a full connection timeout. The provider-only latency, which is the meaningful comparison between the three methods, is in the latency decomposition figure.

## Truncation and output budget

- Output caps in use: [2048]
- One cap for every strategy: True
- Truncated attempts: 0 ({'itms_ast_splice': 0})

## Cost

- **Unavailable**: the provider reported no cost for this run. The cost is not zero and no estimate is substituted.

## Integrity validation

| Check | Result | Detail |
|---|---|---|
| `jsonl_csv_same_attempt_ids` | PASS | 30 records vs 30 csv rows |
| `jsonl_csv_same_status` | PASS | no status mismatch |
| `attempt_ids_unique` | PASS | 30 unique of 30 |
| `summary_recorded_matches` | PASS | summary=30 files=30 |
| `summary_planned_matches` | PASS | planned=30 recorded=30 |
| `artifacts_present_for_every_attempt` | PASS | all present |
| `candidate_hashes_match_artifacts` | PASS | all match |
| `prompt_persisted_when_supported` | PASS | 30/30 attempts carry a stored prompt |
| `no_mock_records_in_live_run` | PASS | 0 mock attempts |
| `failed_and_unevaluated_attempts_retained` | PASS | 8 non-success attempts present in the records |
| `missing_telemetry_stored_as_null` | PASS | no estimate stored in a provider-reported field |
| `single_served_model` | PASS | served ids: ['nvidia/nemotron-3.5-super-vl:free'] |
| `output_cap_consistent_across_strategies` | PASS | caps in use: [2048] |
| `seed_consistent_per_cell` | PASS | 30 cells, 0 with conflicting seeds |
| `seed_honour_never_claimed_without_probe` | PASS | seed_honour_verified is false for every attempt |
| `cost_either_reported_or_explicitly_unavailable` | PASS | 0/30 attempts carry a provider-reported cost |

## Limitations that MUST be disclosed

- The provider reported no cost for this run, so the cost is unavailable. It is not zero and no estimate is substituted.
- Whether the provider honoured the request-level seed is unverified: no repeated-request probe was run.
- The evaluation gate is a scrubbed-environment subprocess, not a secure OS-level sandbox.

