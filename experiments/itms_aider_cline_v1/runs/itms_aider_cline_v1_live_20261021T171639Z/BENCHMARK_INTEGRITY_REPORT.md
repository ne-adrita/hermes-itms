# Benchmark Integrity Report

**Run directory:** `/Users/noshinebnatadrita/Documents/499B/experiments/itms_aider_cline_v1/runs/itms_aider_cline_v1_live_20261021T171639Z`

**Overall: PASS** — 32 checks passed, 0 failed.

Recorded attempts: **300** of **300** planned.

## Run identity

| Field | Value |
|---|---|
| `run_id` | f17d798b21c9 |
| `experiment_id` | itms_vs_aider_vs_cline_v1 |
| `mode` | live |
| `is_measurement` | True |
| `requested_model_id` | minimax/minimax-m3.1-flash:free |
| `temperature` | 0.0 |
| `repetitions` | 5 |
| `tasks` | 20 items |
| `systems` | 3 items |
| `planned_attempts` | 300 |
| `run_order_seed` | 20260101 |
| `seed_policy` | per_system_support |
| `max_spend_usd` | 1.0 |
| `require_free_model` | True |
| `task_manifest_digest` | 4829524a1429af5d23dcc890e0c86746b09112bb473fbbdde2d85c9128e9e170 |
| `python_version` | 3.14.4 |
| `platform` | macOS-27.0.1-arm64-arm-64bit-Mach-O |

## Checks

| # | Check | Result | Detail |
|---|---|---|---|
| 1 | `required_output_files_present` | PASS | all four required files present |
| 2 | `attempt_matrix_complete` | PASS | planned=300 expected_cells=300 recorded=300 |
| 3 | `attempt_ids_unique` | PASS | 300 unique ids across 300 records |
| 4 | `one_record_per_planned_cell` | PASS | 300 distinct cells across 300 records |
| 5 | `repetition_ids_unique` | PASS | 300 unique repetition ids |
| 6 | `no_mock_records_in_live_dataset` | PASS | no record is flagged is_mock |
| 7 | `run_declared_live` | PASS | mode=live is_measurement=True |
| 8 | `candidate_hash_matches_stored_candidate` | PASS | 0 mismatches |
| 9 | `evaluated_candidate_is_archived` | PASS | 0 evaluated attempts without an archived candidate |
| 10 | `csv_row_count_matches_jsonl` | PASS | csv=300 jsonl=300 |
| 11 | `csv_attempt_ids_match_jsonl` | PASS | csv attempt_id order matches jsonl |
| 12 | `summary_recorded_count_matches` | PASS | summary recorded_attempts=300 actual=300 |
| 13 | `summary_planned_count_matches` | PASS | summary planned=300 metadata planned=300 |
| 14 | `summary_per_system_counts_match` | PASS | per-system evaluated counts agree |
| 15 | `metadata_records_seed_policy` | PASS | seed_policy=per_system_support |
| 16 | `metadata_records_spending_limit` | PASS | max_spend_usd=1.0 |
| 17 | `metadata_records_task_manifest_digest` | PASS | task_manifest_digest=4829524a1429af5d23dcc890e0c86746b09112bb473fbbdde2d85c9128e9e170 |
| 18 | `failed_attempts_preserved` | PASS | 3 non-success records retained in the dataset |
| 19 | `status_and_failure_category_agree` | PASS | 0 records whose status and failure category disagree |
| 20 | `failure_categories_accounted_for` | PASS | 3 categorised failures across 300 records |
| 21 | `unevaluated_attempts_are_never_passes` | PASS | 0 unevaluated attempts recorded a pass |
| 22 | `artifact_directory_present_for_every_attempt` | PASS | 300 artifact directories for 300 attempts |
| 23 | `missing_telemetry_is_null_not_zero` | PASS | 0 records encode missing telemetry as 0 |
| 24 | `reported_usage_is_never_zero` | PASS | 0 records report usage_source with zero tokens |
| 25 | `no_seed_claimed_where_unsupported` | PASS | 0 records claim an applied seed without seed support |
| 26 | `seed_support_consistent_for_aider` | PASS | seed_supported=[True] seed_applied_present=[True] n=100 |
| 27 | `seed_support_consistent_for_cline` | PASS | seed_supported=[False] seed_applied_present=[False] n=100 |
| 28 | `seed_support_consistent_for_itms` | PASS | seed_supported=[True] seed_applied_present=[True] n=100 |
| 29 | `historical_dataset_directories_present` | PASS | pilot_runs=True results=True |
| 30 | `run_written_outside_historical_directories` | PASS | run_dir=/Users/noshinebnatadrita/Documents/499B/experiments/itms_aider_cline_v1/runs/itms_aider_cline_v1_live_20261021T171639Z |
| 31 | `artifact_index_written` | PASS | 1397 artefact files indexed -> ARTIFACT_INDEX.csv |
| 32 | `raw_tool_output_archived_consistently` | PASS | 200 attempts have archived raw tool output |

## Matrix completeness

Every planned (task, system, repetition) cell has exactly one record. No cell is missing and no cell is duplicated.

