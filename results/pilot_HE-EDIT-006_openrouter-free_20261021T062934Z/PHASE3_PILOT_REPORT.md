# Phase 3 — Live Pilot Report (3 attempts, 1 task, 3 methods)

**Project:** Hermes / ITMS code-editing benchmark
**Date:** 2026-10-20 (UTC run at 2026-10-21T06:29:34Z)
**Mode:** `live` (real provider requests)
**Verdict:** **PASS WITH LIMITATIONS**

> The pilot **ran**: 3 of 3 planned chat-completion attempts were executed
> against OpenRouter and recorded. One attempt produced a repair that passed
> the task's official test. The limitations are listed in §8 — the most
> important one being that the dynamic alias served **two different models**
> across the three attempts, so this pilot establishes **no** comparison
> between the three editing methods.

---

## 1. Configuration

| Item | Value |
|---|---|
| Provider (declared / effective) | `openrouter` / `openrouter` |
| Requested model id | `openrouter/free` — forwarded **verbatim** (preflight confirmed the string that reaches the wire) |
| Dynamic alias opt-in | `--allow-dynamic-alias` (explicitly authorised by the operator) |
| Task | `HE-EDIT-006` (HumanEval/3, `below_zero`; defect: `if balance < 0:` → `if balance <= 0:`) |
| Methods | `full_file_regeneration`, `search_replace_patch`, `itms_ast_splice` |
| Repetitions | 1 per method |
| **Planned chat-completion attempts** | **3** |
| Additional requests | **1** model-catalog `GET /models` (dispatcher support check, cached per dispatcher, dispatcher reused → exactly one) |
| **Total HTTP requests to the provider** | **4** (1 catalog + 3 chat); `retry_count = 0` on every attempt, so no request was repeated |
| Temperature / max output tokens | `0.2` / `2048` |
| Request timeout / evaluation timeout | 60 s / 10 s |
| Retry policy | max 2 retries, transient HTTP 429 only; 402 and daily-quota never retried (none were triggered) |
| Confirmation | the runner's **interactive** prompt was answered with `yes`; `--yes` was **not** used |
| Output directory | `results/pilot_HE-EDIT-006_openrouter-free_20261021T062934Z` (fresh; did not exist before the run) |
| Task manifest digest | `92077bdca33b75b3ca3c7c15a55b237b6c1f5c79d1da20ebb0f9efb4d23d08f1` |
| Secrets | never printed; verified absent from every artefact (§6) |

---

## 2. Commands actually executed

```bash
# 1) offline pre-run verification (no provider request)
.venv/bin/python benchmarks/phase0/validate_tasks.py --verify-manifest
#   → tasks: 20 / passed: 20 / failed: 0 / manifest: OK (88 files)      exit 0

PYTHONPATH=/tmp/offline_guard .venv/bin/python -m pytest -q
#   → 304 passed, 23 subtests passed                                     exit 0

.venv/bin/python benchmarks/benchmark_runner.py --dry-run --mode live \
    --provider openrouter --model openrouter/free --allow-dynamic-alias \
    --tasks HE-EDIT-006 --reps 1 --output-dir /tmp/preflight_check
#   → live preflight: OK (credentials and model id resolved)
#     dry run: 3 attempts scheduled, no provider contacted.              exit 0

# 2) the authorised pilot (run exactly once; --yes NOT used)
printf 'yes\n' | .venv/bin/python benchmarks/benchmark_runner.py \
    --mode live --provider openrouter --model openrouter/free --allow-dynamic-alias \
    --tasks HE-EDIT-006 --reps 1 \
    --output-dir "results/pilot_HE-EDIT-006_openrouter-free_20261021T062934Z"
#   → [1/3] HE-EDIT-006 full_file_regeneration rep=1 -> output_parse_error
#     [2/3] HE-EDIT-006 search_replace_patch     rep=1 -> output_parse_error
#     [3/3] HE-EDIT-006 itms_ast_splice          rep=1 -> repair_success
#     recorded attempts : 3 / mode : live                              exit 0

# 3) post-run verification
.venv/bin/python benchmarks/phase0/validate_tasks.py --verify-manifest
#   → tasks: 20 / passed: 20 / failed: 0 / manifest: OK (88 files)      exit 0
```

No command was re-run automatically, no other live request was issued, and
the 300-attempt experiment was **not** started.

---

## 3. Attempt results

| Method | Status | Failure category | Official test | Syntax valid | `finish_reason` |
|---|---|---|---|---|---|
| `full_file_regeneration` | `output_parse_error` | `output_parse_error` (reason `no_code_block`) | not run (no candidate) | n/a | `stop` |
| `search_replace_patch` | `output_parse_error` | **`truncated_output`** (reason `malformed_patch`) | not run (no candidate) | n/a | **`length`** |
| `itms_ast_splice` | **`repair_success`** | `none` | **PASSED** | `true` | `stop` |

### 3.1 `full_file_regeneration` — no code block
The model answered with prose that contained no fenced `python` block, so the
strict output contract rejected it (`no fenced python code block found in the
response`). No candidate was produced, therefore no official test was run.
This is a genuine method failure (output-format compliance), correctly
recorded as a failed attempt and **not** as a silent pass.

### 3.2 `search_replace_patch` — truncated response
`finish_reason = "length"` and `completion_tokens = 2048`, i.e. the response
hit the configured `max_output_tokens` limit and was cut off mid-answer, so no
`<<<<<<< SEARCH … >>>>>>> REPLACE` block was present. The Phase 2 truncation
detection classified it as `truncated_output` (distinct from a plain
`output_parse_error`), which is exactly the intended behaviour. It is a
**format/truncation failure of the attempt**, not a failed repair attempt on a
well-formed patch.

### 3.3 `itms_ast_splice` — successful repair
The model returned a replacement for the target function, the existing ITMS
splicer applied it, the resulting file parsed, and **the task's official
`check(candidate)` test passed** (`test_passed: true`).

---

## 4. Model identity (requested vs. served)

| Attempt | `requested_model_id` | `served_model_id` (provider-reported) | Same model? |
|---|---|---|---|
| 1 `full_file_regeneration` | `openrouter/free` | `nvidia/nemotron-3-ultra-550b-a55b:free` | alias → concrete |
| 2 `search_replace_patch` | `openrouter/free` | `nvidia/nemotron-3-ultra-550b-a55b:free` | alias → concrete |
| 3 `itms_ast_splice` | `openrouter/free` | **`nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free`** | alias → concrete |

**Two different models served the three attempts.** The dynamic alias behaved
exactly as the audit predicted. The records keep `requested_model_id` and
`served_model_id` separate, so the difference is fully visible in the
artefacts and in `summary.json → observed_served_model_ids`.

**Consequence for research integrity:** the successful `itms_ast_splice`
repair was produced by a *different model* from the two failures. The pilot
therefore **cannot** support any claim that ITMS outperforms the other
methods — the attempts are not comparable. This is a property of the selected
model id, not of the harness.

---

## 5. Token and cost provenance

| Attempt | provider prompt / completion / total | local estimates | `usage_source` | `cost_usd` | `cost_source` | `retry_count` |
|---|---|---|---|---|---|---|
| 1 | 477 / 535 / 1012 | `null` / `null` | `provider_reported` | `null` | `null` | 0 |
| 2 | 500 / 2048 / 2548 | `null` / `null` | `provider_reported` | `null` | `null` | 0 |
| 3 | 499 / 818 / 1317 | `null` / `null` | `provider_reported` | `null` | `null` | 0 |

* Every token count came from the **provider**, is labelled `provider_reported`,
  and the local estimate fields are `null` — the Phase 2 M1 fix behaves
  correctly on live data.
* Attempt 2's `completion_tokens = 2048` equals `max_output_tokens = 2048`,
  independently confirming the truncation diagnosed from `finish_reason`.
* **Cost was not reported by the provider** for this endpoint, so `cost_usd`
  and `cost_source` are `null` — never `0`, never an estimate. No cost can be
  stated for this pilot.
* No attempt was retried (`retry_count = 0`); the 4 HTTP requests are the
  1 catalog + 3 chat calls and nothing else.

---

## 6. Validation evidence

| Check | Result |
|---|---|
| Scheduled vs. recorded attempts | 3 scheduled → **3 recorded**; no attempt dropped, including both failures |
| Attempt ids unique | 3 / 3 |
| `is_mock` | `false` for all 3 records; `summary.contains_mock_results = false`; `summary.is_measurement = true`; no mock warning |
| Method / task / rep recorded | `full_file_regeneration`, `search_replace_patch`, `itms_ast_splice` × `HE-EDIT-006` × rep 1 |
| Official test evaluation | run on the **candidate** produced by the method (1 candidate produced, 1 test run, PASS) |
| JSONL ↔ CSV consistency | 3 rows / 3 records, **0 field mismatches** across 27 compared fields (null-aware) |
| Summary ↔ records | `status_counts = {output_parse_error: 2, repair_success: 1}` matches a manual recount |
| Run metadata completeness | 22 required fields present with the *actual* values (`temperature 0.2`, `max_output_tokens 2048`, `request_timeout 60.0`, `evaluation_timeout 10.0`, `max_retries 2`, `reps 1`, `task_count 1`, `bootstrap_seed 20260101`, `planned_attempts 3`, task digest) |
| `served_model_id` in metadata | `null` — never fabricated before a response existed; the observed ids are in the summary |
| Secret scan of all artefacts | the credential value is **absent** from `attempts.jsonl`, `attempts.csv`, `summary.json`, `run_metadata.json`; **0** credential-shaped strings found |
| Phase 0 manifest (pre- and post-run) | `manifest: OK (88 files)`, 20/20 tasks, exit 0 both times |
| Existing test suite | 304 passed, 23 subtests (unchanged by this pilot) |
| No package installed, `.env` untouched, no account change | confirmed |

---

## 7. Request accounting

| Request | Count | When |
|---|---|---|
| `GET /models` (model catalog) | 1 | first attempt only (dispatcher support check, cached per dispatcher) |
| `POST /chat/completions` | 3 | one per method |
| **Total** | **4** | `retry_count = 0` on every attempt |

The runner printed *"WARNING: this will make 3 live chat-completion requests"*
plus the explicit note that the catalog may be asked once per run — the
advertised count matches what actually happened.

---

## 8. Limitations and residual risks

1. **No method comparison is possible from this pilot.** Two different models
   served the three attempts (§4). The `itms_ast_splice` success and the two
   parse failures are not attributable to the editing method.
2. **`openrouter/free` is a dynamic alias**, so even a larger run with it
   would not be a controlled experiment. A controlled comparison requires a
   pinned, non-alias model id, and the Phase 2 M3 enforcement already refuses
   anything else.
3. **Cost is unavailable.** The provider did not return a cost for these
   chat-completion responses, so no cost figure can be reported — the
   `null`s are the correct result, not a gap to be filled with an estimate.
4. **The candidate source and the raw provider responses are not persisted.**
   The successful repair's candidate file was written to a temporary
   workspace and deleted, and the raw model text is not stored. A reviewer
   cannot independently re-run the official test on the successful candidate.
   This is an auditability gap (the `candidates_dir` in the output paths is
   never populated) and should be closed before any larger live run.
5. **One task, one repetition.** A single task cannot support any statistic.
6. **The evaluation sandbox remains best-effort** (no filesystem or process
   containment; the socket guard is bypassable by a child process). It is not
   suitable for hostile code. The model output here was benign.
7. **Provider-reported token counts were accepted as reported**; the harness
   cannot independently verify them.
8. **These 20 tasks are synthetic and HumanEval-derived.** This pilot is
   **not** the HumanEval benchmark and produces **no** HumanEval pass rate.

---

## 9. Verdict

# PASS WITH LIMITATIONS

**Why not a full PASS.** Two of the three attempts did not reach the
evaluation stage: one produced no code block, one was truncated by the output
limit. Those are genuine, correctly recorded attempt failures — but they mean
the pilot produced only **one** evaluated candidate, and the non-comparable
model assignment (§4) means even that single success cannot support a
performance claim. The missing cost and the unpersisted candidate (§8) are
further non-critical measurement gaps.

**Why not a FAIL.** Every critical requirement held: the configuration was
honoured verbatim (`openrouter/free` reached the wire unchanged), the run was
fail-closed and made exactly the 4 advertised requests, all 3 attempts were
recorded without loss, the record/CSV/summary/metadata artefacts are mutually
consistent, no mock result was present, no secret reached an artefact, the
Phase 0 manifest verified before and after, and one real repair passed the
official test end to end.

**What this pilot establishes:** the live integration works — transport,
strict-live behaviour, model-id forwarding, token/cost provenance, retry
accounting, output-contract enforcement, truncation detection, and record
integrity are all confirmed on real provider data.

**What it does not establish:** that ITMS, full-file regeneration or
search/replace is better than the others. That requires a pinned model id
and a properly powered run, which is Phase 4 and needs separate approval.

**Next step (not taken):** close the candidate-persistence gap (§8.4), select
a **pinned** model id, confirm quota, and re-run the pilot — then, only after
review, plan the 300-attempt experiment.
