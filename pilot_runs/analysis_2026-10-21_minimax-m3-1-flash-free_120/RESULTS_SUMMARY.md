# ITMS Code-Editing Benchmark — Live Run Results (120 attempts)

- Run directory: `/Users/noshinebnatadrita/Documents/499B/pilot_runs/2026-10-21_minimax-m3-1-flash-free_120_attempts`
- Run id: `604e37cd3dc4`  (created 2026-10-21T14:18:23Z)
- Mode: **live**  |  is_measurement: True
- Provider: openrouter (effective: openrouter)
- Requested model id: `minimax/minimax-m3.1-flash:free`
- Task manifest digest: `92077bdca33b75b3ca3c7c15a55b237b6c1f5c79d1da20ebb0f9efb4d23d08f1`
- Run-order seed: 20261021

## Counts

| Metric | Value |
|---|---|
| Planned attempts | 120 |
| Recorded attempts | 120 |
| Successful (repair_success) | 114 |
| Failed / not successful | 6 |
| Wall clock | 768822.085 ms |

### Status breakdown

- `output_parse_error`: 6
- `repair_success`: 114

### Failure categories

- `truncated_output`: 6

## Limitations that MUST be disclosed

- **Ceiling effect on the evaluator pass rate.** Every attempt that reached the official evaluator passed (see the pass-rate column). Fig 1 therefore shows 100% for all three methods and does NOT discriminate between them; Fig 1b (completion rate over all scheduled attempts) is the discriminating comparison, and Fig 2 shows the raw success/failure split.
- **All 6 non-successes are output truncations** (`finish_reason = length`, `failure_category = truncated_output`), not wrong repairs: the model's mandatory reasoning consumed the 2048-token output cap before a parseable patch was emitted. They are concentrated in `itms_ast_splice` (5 of 40), so the completion-rate difference between methods is partly a token-budget effect of the harness, not purely a difference in edit quality.
- **Task set is easy for this model.** The 20 HumanEval-derived repair mutations are solved nearly perfectly, so this run cannot separate the methods on repair quality. It is a valid measurement of the harness on a pinned model, not a discriminating benchmark of editing strategies.
- **Free-model route.** The requested id is a pinned OpenRouter slug, but OpenRouter may re-point that slug at a different upstream serving implementation over time. This run observed a single served id for all 120 attempts, so it is a same-model comparison *as observed on this date*; it is not a guarantee for future runs.
- **One run, seed 20261021, 2 repetitions.** Per-task results are based on 2 repetitions, so per-task pass rates are coarse (0%, 50%, 100%).

## Per-method results

| Method | n | Success | Failed | Evaluated | Pass rate | Mean latency (ms) | Mean tokens |
|---|---|---|---|---|---|---|---|
| full_file_regeneration | 40 | 39 | 1 | 39 | 100.0% | 5139 | 1092 |
| search_replace_patch | 40 | 40 | 0 | 40 | 100.0% | 4150 | 931 |
| itms_ast_splice | 40 | 35 | 5 | 35 | 100.0% | 9912 | 1412 |

## Model identity

- Requested id(s): ['minimax/minimax-m3.1-flash:free']
- Served id counts: {'minimax/minimax-m3.1-flash:free': 120}
- All attempts served the same model: **True**
- Served id == requested id for every attempt: **True**

## Integrity checks

- jsonl_csv_same_attempt_ids: True
- jsonl_csv_same_status: True
- summary_recorded_matches: True
- summary_planned_matches: True
- summary_artifacts_persisted_matches: True
- candidate_hashes_all_match: True
- candidate_hash_mismatches: []
- candidate_redacted_count: 0
- candidate_records_without_hash: []
- artifact_status_counts: {'ok': 120}
- mock_attempts: 0
- modes_seen: ['live']
- is_measurement: True
- requested_model_ids: ['minimax/minimax-m3.1-flash:free']
- served_model_id_counts: {'minimax/minimax-m3.1-flash:free': 120}
- single_served_model: True
- served_matches_requested: True
- attempts_without_served_model_id: 0
- secret_like_hits_in_artifacts: []

## Exclusions (never imputed)

- latency: attempts with latency_ms == null are excluded (never imputed)
- tokens: attempts without a provider-reported total_tokens (usage_source 'local_estimate*' or null) are excluded (never imputed)
- pass_rate: denominator is the number of attempts for which the official evaluator actually ran (evaluation_attempted == true)

## Figures

- `fig1_pass_rate_by_method` -> `pilot_runs/analysis_2026-10-21_minimax-m3-1-flash-free_120/fig1_pass_rate_by_method.png`
- `fig1b_completion_rate_by_method` -> `pilot_runs/analysis_2026-10-21_minimax-m3-1-flash-free_120/fig1b_completion_rate_by_method.png`
- `fig2_success_vs_failure_by_method` -> `pilot_runs/analysis_2026-10-21_minimax-m3-1-flash-free_120/fig2_success_vs_failure_by_method.png`
- `fig3_mean_execution_time_by_method` -> `pilot_runs/analysis_2026-10-21_minimax-m3-1-flash-free_120/fig3_mean_execution_time_by_method.png`
- `fig4_mean_token_usage_by_method` -> `pilot_runs/analysis_2026-10-21_minimax-m3-1-flash-free_120/fig4_mean_token_usage_by_method.png`
- `fig5_task_by_method_heatmap` -> `pilot_runs/analysis_2026-10-21_minimax-m3-1-flash-free_120/fig5_task_by_method_heatmap.png`
