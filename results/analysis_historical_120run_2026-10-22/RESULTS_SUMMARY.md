# ITMS code-editing benchmark — analysis

> **HISTORICAL LIVE RUN (120 attempts, 2026-10-21) - re-analysed offline with the current tooling. Unchanged on disk.**

This is the existing 120-attempt live run (20 tasks x 3 methods x 2 repetitions, pinned model minimax/minimax-m3.1-flash:free, output cap 2048, no request-level seed, no stored prompts). It was executed before the seed, budget, prompt-persistence and derived-output-cap changes, and its artefacts are preserved byte-for-byte. The re-analysis is read-only and was written to a separate directory. It shows the same ceiling effect as the 9-request pilot: every attempt that reached the official evaluator passed (100% for all three methods), so only completion rate, tokens and latency differ. It also shows the truncation confound of the 2048-token cap: 6 truncated attempts, 5 of them in itms_ast_splice.

- Run directory: `/Users/noshinebnatadrita/Documents/499B/pilot_runs/2026-10-21_minimax-m3-1-flash-free_120_attempts`
- Run id: `604e37cd3dc4`
- Mode: **live** | is_measurement: True
- Requested model id: `minimax/minimax-m3.1-flash:free`
- Served model ids: ['minimax/minimax-m3.1-flash:free']

## Denominators

| Quantity | Value |
|---|---|
| tasks | 20 |
| methods | 3 |
| reps_per_task | 2 |
| scheduled_attempts | 120 |
| recorded_attempts | 120 |
| evaluated_attempts | 114 |
| candidate_produced_attempts | 114 |

## Per-method results

| Method | n | Success | Evaluated | Evaluator pass rate | Completion rate | Mean latency (ms) | Median latency (ms) | Mean tokens (provider-reported) |
|---|---|---|---|---|---|---|---|---|
| full_file_regeneration | 40 | 39 | 39 | 100.0% | 97.5% | 5139 | 3747 | 1092 |
| itms_ast_splice | 40 | 35 | 35 | 100.0% | 87.5% | 9912 | 6596 | 1412 |
| search_replace_patch | 40 | 40 | 40 | 100.0% | 100.0% | 4150 | 3109 | 931 |

## Truncation and output budget

- Output caps in use: (none recorded)
- One cap for every strategy: True
- Truncated attempts: 6 ({'full_file_regeneration': 1, 'itms_ast_splice': 5, 'search_replace_patch': 0})

## Cost

- **Unavailable**: the provider reported no cost for this run. The cost is not zero and no estimate is substituted.

## Integrity validation

| Check | Result | Detail |
|---|---|---|
| `jsonl_csv_same_attempt_ids` | PASS | 120 records vs 120 csv rows |
| `jsonl_csv_same_status` | PASS | no status mismatch |
| `attempt_ids_unique` | PASS | 120 unique of 120 |
| `summary_recorded_matches` | PASS | summary=120 files=120 |
| `summary_planned_matches` | PASS | planned=120 recorded=120 |
| `artifacts_present_for_every_attempt` | PASS | all present |
| `candidate_hashes_match_artifacts` | PASS | all match |
| `prompt_persisted_when_supported` | PASS | no attempt carries a stored prompt (this run predates prompt persistence); the check is vacuous, not a pass on merit |
| `no_mock_records_in_live_run` | PASS | 0 mock attempts |
| `failed_and_unevaluated_attempts_retained` | PASS | 6 non-success attempts present in the records |
| `missing_telemetry_stored_as_null` | PASS | no estimate stored in a provider-reported field |
| `single_served_model` | PASS | served ids: ['minimax/minimax-m3.1-flash:free'] |
| `output_cap_consistent_across_strategies` | PASS | no cap recorded (mock runs) |
| `seed_consistent_per_cell` | PASS | 120 cells, 0 with conflicting seeds |
| `seed_honour_never_claimed_without_probe` | PASS | seed_honour_verified is false for every attempt |
| `cost_either_reported_or_explicitly_unavailable` | PASS | 0/120 attempts carry a provider-reported cost |

## Limitations that MUST be disclosed

- 6 attempt(s) were truncated by the output-token cap. Truncation is a harness-budget outcome and is reported per method; it is not a repair-quality result.
- The provider reported no cost for this run, so the cost is unavailable. It is not zero and no estimate is substituted.
- Whether the provider honoured the request-level seed is unverified: no repeated-request probe was run.
- The evaluation gate is a scrubbed-environment subprocess, not a secure OS-level sandbox.

