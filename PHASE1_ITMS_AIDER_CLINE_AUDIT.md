# Phase 1 Audit — ITMS vs Aider vs Cline (300-attempt comparison)

**Scope:** audit, verification and safe preparation only. **No live model/API
call was made to any provider**, no benchmark was launched, and the historical
120-attempt dataset was not modified. New work lives in
`experiments/itms_aider_cline_v1/`.

---

## 1. Existing architecture and reusable files

### 1.1 What already exists and works

| Component | Location | Reuse decision |
|---|---|---|
| Task construction (HumanEval → 20 mutated repair tasks) | `benchmarks/phase0/build_tasks.py` | **Reuse as-is.** Already pins a HumanEval commit, verifies both dataset digests, and produces a 1:1 task↔problem mapping. |
| Task eligibility rules (AST self-containment) | `benchmarks/phase0/eligibility.py` | Reuse as-is. |
| Deterministic mutation catalogue (5 rules) | `benchmarks/phase0/mutation_engine.py` | Reuse as-is. |
| 12-check offline task validator | `benchmarks/phase0/validate_tasks.py` | Reuse as-is; re-run before Phase 2. |
| **Isolated official-test evaluator** | `benchmarks/phase0/runner.py:118` `run_official_test()` | **Reuse as-is.** Subprocess-isolated, sockets blocked, timeout-bounded, returns `(passed, error_type, error_message, duration_ms)`. This is the single evaluator for all three systems. |
| Model-facing task payload (no gold, no mutation metadata) | `benchmarks/data/model_facing_tasks.json` | Reuse via `TaskRepository`. |
| Gold / original solutions | `benchmarks/data/gold_solutions.json`, `original_source.py` | **Not reachable** through `TaskRepository` by design. Keep unreachable. |
| Provider abstraction + secret redaction + usage honesty | `benchmarks/benchmark/provider.py` | Reuse for the ITMS system's provider call. |
| ITMS editing adapter (skeletonise → LLM → AST splice) | `benchmarks/benchmark/adapters/itms_splice.py` | **Reuse as-is.** This is the real ITMS edit mechanism. |
| Attempt record schema (46 columns) | `benchmarks/benchmark/records.py` | Reuse the discipline; a new, wider schema was written for the system comparison. |
| Deterministic schedule builder | `benchmarks/benchmark/experiment.py:77` `build_schedule()` | Superseded by an equivalent in the new runner (adds a system dimension). |
| Paired task-level comparison + bootstrap CI | `benchmarks/benchmark/analysis.py` | Reuse the analysis approach; the new summary is Phase 1 only. |
| Artifact store (atomic writes, redaction, digests) | `benchmarks/benchmark/artifacts.py` | Reuse the *approach*; the new runner writes into its own new directory. |
| Streamlit dashboard | `app.py`, `completed_benchmark_viewer.py` | **Read-only** consumers of the historical run. Extend in Phase 3, not now. |

### 1.2 The critical finding

`grep -r "aider\|cline" benchmarks/` returns **zero matches**. The three
"methods" in the historical 120-attempt run —
`full_file_regeneration`, `search_replace_patch`, `itms_ast_splice` — are
**prompt-contract treatments of one model**, not three systems. They are
applied in-process by the project's own `surgical_splicer` / `skeletonizer`.
No external editing agent was ever invoked.

**Consequence:** the historical 120 attempts measure *prompt/edit-format
variants*, not *systems*. They are valid historical evidence about the Phase 1
harness, and they are **not** a partial result for the requested comparison.
They are also not comparable to the new run: different experimental factor,
different model budget, different system semantics.

### 1.3 Historical dataset (immutable)

* **Location:** `pilot_runs/2026-10-21_minimax-m3-1-flash-free_120_attempts/`
  (not `results/`; `results/` holds a separate 3-attempt pilot).
* **Contents:** exactly **120 records** = 20 tasks × 3 methods × 2 reps.
  47 JSONL keys, identical key set on every record. 120 unique `attempt_id`s,
  120 unique `(task, method, rep)` cells, each occurring exactly once.
* **Provenance:** `mode=live`, `is_mock=false`, provider `OpenRouter`,
  `requested_model_id = served_model_id = minimax/minimax-m3.1-flash:free`,
  `run_id=604e37cd3dc4`, `artifacts_status=ok` on all 120, 120 per-attempt
  artifact directories.
* **Outcome:** 114 `repair_success` / 6 `output_parse_error`
  (`truncated_output`); per method 39/40, 40/40, 35/40.
* **Analysis artefacts:** `pilot_runs/analysis_2026-10-21_.../` (figures,
  `results_summary.json`, `RESULTS_SUMMARY.md`) — read-only, regenerate from
  the JSONL.
* **Not touched.** No file under `pilot_runs/` or `results/` was created,
  modified, renamed or deleted. Verified with `git status` (clean except the
  new directory and the two files listed in §5).

### 1.4 The 20 `HE-EDIT-*` tasks

Each task corresponds to **one specific HumanEval problem** — the mapping is
1:1 and bijective (20 distinct `humaneval_task_id` values), so they are
HumanEval-*derived*, and they are **not** HumanEval benchmark items.

| Task | HumanEval | Function | Mutation | Task | HumanEval | Function | Mutation |
|---|---|---|---|---|---|---|---|
| 001 | /0 | has_close_elements | comparison swap | 011 | /7 | filter_by_substring | comparison swap |
| 002 | /1 | separate_paren_groups | off-by-one | 012 | /15 | string_sequence | off-by-one |
| 003 | /2 | truncate_number | incorrect constant | 013 | /8 | sum_product | incorrect constant |
| 004 | /4 | mean_absolute_deviation | arithmetic swap | 014 | /18 | how_many_times | arithmetic swap |
| 005 | /36 | fizz_buzz | boolean swap | 015 | /64 | vowels_count | boolean swap |
| 006 | /3 | below_zero | comparison swap | 016 | /9 | rolling_max | comparison swap |
| 007 | /14 | all_prefixes | off-by-one | 017 | /24 | largest_divisor | off-by-one |
| 008 | /5 | intersperse | incorrect constant | 018 | /17 | parse_music | incorrect constant |
| 009 | /13 | greatest_common_divisor | arithmetic swap | 019 | /20 | find_closest_elements | arithmetic swap |
| 010 | /57 | monotonic | boolean swap | 020 | /71 | triangle_area | boolean swap |

The `HE-EDIT-NNN` numbering is *not* HumanEval numbering: ids were assigned in
rule-diversity round-robin selection order. Provenance is pinned in
`benchmarks/data/source/PROVENANCE.json` (commit
`463c980b59e818ace59f6f9803cd92c749ceae61`, MIT, both dataset digests
verified, 164 problems) and the 20 used problems are vendored in
`benchmarks/data/source/humaneval_subset.jsonl`.

Every task was checked by the 12-check validator: gold passes the official
test, the mutated source fails it, the mutation is reproducible, the prompt
and docstring are byte-preserved, the task is self-contained, and the payload
is compatible with all three edit formats. `validation_report.json`:
20/20 passed.

---

## 2. Verified status of ITMS, Aider and Cline

Nothing below is assumed from an installed binary. Each entry states what was
actually executed during Phase 1.

### 2.1 ITMS — **available** (in-repo)

* Not a CLI. It is this project's own agent: `skeletonizer` (context
  reduction) → model call → `surgical_splicer.ASTSurgicalSplicer` (AST-level
  splice).
* Invocation: `benchmarks/benchmark/adapters/itms_splice.py` +
  `benchmarks/benchmark/provider.py:LiveProvider` →
  `model_dispatcher.ModelDispatcher.generate_live()`.
* Credential: `AppConfig.resolve_api_key("openrouter")` → `OPENROUTER_API_KEY`
  (present in the project's `.env`).
* Output capture: the model response is the splice input; the candidate
  module is produced in-process.
* Telemetry: **provider-reported** (`prompt_tokens`, `completion_tokens`,
  `total_tokens`, `usage_source`, `cost_usd`).
* Seed: **supported**. `seed` is now an optional request-level field on the
  chat-completions payload (see §5). Whether the upstream model honours it is
  provider-dependent and is not claimed.
* One documented defect is preserved deliberately (measured, not hidden): a
  body-only replacement that declares a nested `def` is mis-hoisted by
  `ASTSurgicalSplicer.splice()` (`adapters/itms_splice.py:15-22`).

### 2.2 Aider — **available, verified headless**

* Binary: `/Users/noshinebnatadrita/.local/bin/aider`, **aider 0.86.2**,
  installed as a `uv` tool with its own Python 3.12 environment and
  **litellm 1.81.10**.
* Verified during Phase 1: the CLI starts and runs one shot in a
  **non-TTY** context. The probe was deliberately pointed at
  `http://127.0.0.1:9/v1` so that **no external request was made**; it
  produced litellm connection errors, which proves the whole path (arg
  parsing → litellm → retry loop → error) executes headlessly.
* Invocation (implemented in `systems/aider.py`):

  ```
  aider --message "<task instruction>" --yes-always --exit \
        --no-git --no-auto-commits --no-dirty-commits \
        --no-pretty --no-stream --no-analytics --no-check-update \
        --no-show-release-notes --no-show-model-warnings \
        --model openrouter/<model> \
        --model-settings-file <seed/temperature yaml> \
        candidate.py
  ```
  with `cwd` = the attempt's isolated workspace.
* Model configuration: `--model openrouter/<model-id>`; the credential is
  taken from the `OPENROUTER_API_KEY` **environment variable** and is never
  placed on the command line (so it never appears in `ps`).
* Output capture: Aider edits the file in place in the workspace; the
  candidate is read back from `candidate.py`. Its own stdout/stderr are
  archived as artifacts.
* Telemetry: **tool-reported**, parsed from Aider's summary line
  (`Tokens: N sent, N received.` / `Cost: $x.xx message, $y.yy session.`).
  Aider's numbers are never merged with provider-reported numbers from the
  other systems; `usage_source` records the origin.
* Seed: **supported** via the model-settings file
  (`name: aider/extra_params` → `extra_params.seed`), which litellm forwards
  to the model API. Whether the upstream model honours the seed is not
  claimed.
* Caveats found: (a) Aider retries connection errors with a long backoff
  (1→32 s), so the runner enforces a hard wall-clock timeout of 300 s and
  kills the process; (b) Aider's `--list-models` needs a TTY and is therefore
  unusable for a scripted preflight; (c) Aider walks upwards for a git root,
  so workspaces must live **outside** this repository — the runner places
  them under the system temp dir and also passes `--no-git`.

### 2.3 Cline — **available, verified headless**

* Binary: `/opt/homebrew/bin/cline`, **cline 3.0.73** (npm global,
  `@cline/sdk` / `@cline/core` / `@cline/agents` / `@cline/llms`).
* Verified during Phase 1: the CLI runs one shot with **no TTY**, emitting
  one JSON object per line. The probe used the default configuration and
  failed at the provider with an authentication error, which proves the
  non-interactive path works. (That probe did reach `api.openai.com` and
  received a 401; no model inference occurred. Subsequent probes use
  `127.0.0.1`.)
* Invocation (implemented in `systems/cline.py`):

  ```
  cline --json --yolo --auto-approve true \
        --provider openrouter --model <model-id> \
        --cwd <workspace> --timeout <seconds> \
        "<task instruction>"
  ```
* Model configuration: Cline ships a **native `openrouter` provider**
  (`openrouter.ai/api/v1` is built into the binary, alongside
  `openai-compatible`, `anthropic`, `gemini`, `openai`). The credential is
  taken from `OPENROUTER_API_KEY` in the environment, not from `-k/--key`,
  so it does not appear in the process list.
* Output capture: Cline edits files in `--cwd`; the candidate is read back
  from `candidate.py`. The full JSONL event stream is archived.
* Telemetry: **tool-reported**, from the `run_result` event:
  `usage.inputTokens`, `usage.outputTokens`, `usage.cacheReadTokens`,
  `usage.cacheWriteTokens`, `usage.totalCost`, `durationMs`, and
  `model.{id,provider}`.
* Seed: **not supported.** Cline's CLI exposes no request-level random seed
  (`--thinking`, `--model`, `--provider` and the provider settings are the
  only request controls). Cline cells are therefore identified by
  `repetition_id` only, and the record says so explicitly.
* Caveats found: (a) `cline config` and `cline auth` are **interactive and
  require a TTY** — they cannot be used for a scripted preflight, so
  provider readiness is checked by inspecting the CLI and the credential
  instead; (b) the default `~/.cline` profile is used so that a first-run
  TTY prompt is avoided; (c) `--data-dir` may be set to isolate session
  state, at the cost of losing the stored provider settings.

### 2.4 Summary

| | ITMS | Aider | Cline |
|---|---|---|---|
| Real system, really invoked | in-repo agent | yes, CLI subprocess | yes, CLI subprocess |
| Headless / non-TTY | n/a | **verified** | **verified** |
| Same model + provider possible | yes | yes | yes |
| Credential source | `OPENROUTER_API_KEY` | `OPENROUTER_API_KEY` (env) | `OPENROUTER_API_KEY` (env) |
| Token/cost telemetry | provider-reported | tool-reported | tool-reported |
| Request-level seed | **yes** (added) | **yes** | **no** |
| Blocking issue | none | none | none |

---

## 3. Task-selection protocol and candidate manifest

### 3.1 Protocol (implemented in `experiments/itms_aider_cline_v1/select_tasks.py`)

1. **Candidate pool** — the 20 frozen `HE-EDIT-*` tasks, each a 1:1 derivation
   of a pinned HumanEval problem, each passing the 12-check validator.
2. **Difficulty is assigned a priori** by a fixed rubric (v1.0.0) over
   task-intrinsic features only:
   * mutation category weight (logic 3, boundary 3, arithmetic 2, constant 1);
   * `+2` if the defect sits inside an indented/nested block;
   * `+2` for textually duplicated anchor lines, `+1` for ≥2 same-kind
     sibling statements;
   * `+2` if the target's docstring constrains the mutated operator
     (spec-vs-code divergence — the defect is only detectable by reading the
     specification);
   * `+1` if the target function is ≥20 lines (code-boundary risk);
   * `+1` for nesting depth ≥2; `+1` for ≥3 numeric literals in a
     boundary/constant mutation.
   * Tier A ≥7, B ≥4, C below.
3. **Bias control** — *no observed model output* influences selection,
   ranking, or exclusion. The historical 120-attempt run contains per-task
   outcomes for these same 20 tasks; it was **deliberately excluded** from
   selection, and the manifest records `used_for_selection: false` with an
   explicit note.
4. **Held-out pool for any pilot** — 144 HumanEval problems were inspected
   but not selected (28 rejected, 116 not selected; `build_log.json`). A
   pilot, if one is run, must draw from that pool, never from the final 20.
5. **The rubric version is pinned** in the manifest, and
   `select_tasks.py --check` re-derives the manifest and fails on a digest
   mismatch.

**What this protocol does *not* claim.** Difficulty is a *predicted*
mechanism, not an observed failure probability. No claim is made that any
task "has a high observed failure rate", because no such evidence was used
(by design). The manifest's `expected_difficulty_mechanism` field states the
mechanism as a hypothesis, and the rationale string says so.

### 3.2 Manifest

`experiments/itms_aider_cline_v1/task_manifest.json` — 20 entries, each with:
task id; source/provenance (HumanEval id, entry point, dataset repo,
revision, path, MIT license, and the explicit
`is_humaneval_benchmark_item: false` flag); the task description (problem
prompt, repair instruction, source paths); the mutation (rule, parameters,
validation outcome); the official test path + SHA-256 + `hidden_from_model:
true`; the static feature vector; the expected difficulty mechanism; the
selection rationale with the rubric score and tier; and the
non-use-of-observed-outcome note.

Resulting tier distribution: **A = 6, B = 11, C = 3**.
Manifest digest: `4829524a1429af5d…` (recomputed by `--check`).

### 3.3 Optional pilot (not executed, cost stated up front)

If the user wants observed difficulty before the 300-run experiment, the
**only** bias-free option is a pilot on the **held-out** pool, not on the
final 20. A 30-cell pilot (10 held-out tasks × 3 systems × 1 rep) at the
historical run's mean of ~1.0k tokens/attempt and ~5 s/attempt would cost on
the order of **30k tokens** — free with the `:free` model tier currently
configured, or a small fraction of a cent on a paid tier. It is **not**
recommended for ranking the final 20, because that would reintroduce exactly
the bias the protocol removes. **No pilot was run in Phase 1.**

---

## 4. Experimental controls and seed limitations

### 4.1 Controls that are in place

| Control | Implementation |
|---|---|
| Same task instruction | `task.repair_instruction`, identical for all three systems |
| Same starting file | `candidate.py`, written from the same `mutated_source` bytes; asserted by a test |
| Same hidden test | the official test is **not in the workspace** during the run; asserted by a test |
| Same evaluator | `benchmarks.phase0.runner.run_official_test` for every candidate, with sockets blocked |
| Same timeout policy | 300 s system wall clock, 20 s evaluation, 120 s request |
| Same model & provider | one `model_id` + `provider` in `experiment_config.json`, used by all three |
| Same temperature | 0.0, set for ITMS and Aider; **Cline does not expose a temperature control** (see §4.3) |
| Workspace isolation | fresh temp dir per attempt, **outside** the git repository |
| Deterministic order | `build_schedule` + `run_order_seed = 20260101` |
| Unique cell identity | `attempt_id = sha256(run_id | task|system|rep)[:32]`; uniqueness asserted over all 300 |
| Failures never lose artifacts | artifacts are written for every attempt, including failed executions; asserted by a test |
| Execution vs evaluator failure | separate statuses `execution_failed` / `invalid_candidate` / `repair_success` / `repair_failed` |
| Mock never masquerades as measurement | every record carries `is_mock`; the mock run's metadata sets `is_measurement: false` |
| Usage honesty | `usage_source` ∈ {provider_reported, tool_reported, local_estimate, missing}; provider-reported and tool-reported numbers are never summed together; missing stays `None`, never `0` |
| Secrets | credentials travel in the environment, never in argv; the config file holds no credential; `redact_secrets` from the existing harness is reused for stored responses |

### 4.2 Seeds — precise statement

* A **seed is requested** for every one of the 300 cells
  (`seed_policy: per_system_support`, derived deterministically from
  task + system + repetition).
* A seed is **applied** only where the client supports a request-level seed:
  * **ITMS — yes.** `seed` added as an optional field on the chat-completions
    payload.
  * **Aider — yes.** injected through the model-settings file
    (`extra_params.seed`), forwarded by litellm.
  * **Cline — no.** the CLI has no seed parameter.
* Each record therefore carries `seed_requested`, `seed_supported` and
  `seed_applied` independently. **The experiment will not claim that three
  distinct random seeds were applied to Cline.** Cline's five repetitions
  differ only by repetition id and by whatever nondeterminism the upstream
  model has.
* **Additional caveat:** even for ITMS and Aider, the *client* accepts a
  seed; whether the upstream OpenRouter-served model honours it is
  provider-dependent and is **not** claimed. The report should say
  "seed sent, honouring unverified".
* Note that temperature 0.0 largely removes sampling variance for the ITMS
  and Aider cells, which further reduces the value of the seed as a
  variance-control device; the repetitions then mainly measure
  *run-to-run nondeterminism* (tool state, network variance, truncation).

### 4.3 Known asymmetries that cannot be eliminated (to be disclosed in the report)

1. **Context strategy differs by design.** ITMS deliberately shows a
   *skeleton*; Aider builds a repo map; Cline plans with its own tools. That
   is the systems' real behaviour and is the point of the comparison — but it
   means prompt-token counts are not directly comparable between systems, and
   only within-system token comparisons are meaningful.
2. **Cline has no temperature control.** Its sampling is at the provider
   default. This is a genuine, disclosed asymmetry, not something the harness
   can equalise.
3. **Telemetry provenance differs** (provider-reported vs tool-reported).
   Numbers are comparable in kind (tokens, cost, latency) but not in
   derivation, and must not be pooled.
4. **Aider's tool overhead is included in its latency**, as is Cline's
   planning overhead, while ITMS's latency is essentially the model call
   plus a splice. Latency therefore measures *system* cost, not just model
   cost. That is a legitimate system-level metric but should not be
   presented as "model latency".
5. **Retry behaviour differs.** Aider retries connection errors with a long
   backoff; the ITMS dispatcher retries on its own policy. Timeouts are
   recorded as `timeout` and counted as execution failures, never as
   evaluator failures.

---

## 5. Files changed and tests executed

### 5.1 Files changed (existing)

| File | Change | Risk |
|---|---|---|
| `model_dispatcher.py` | **+15 / −3**. `seed: Optional[int] = None` added to `generate()`, `generate_live()` and `_call_openai_compatible()`; the `seed` field is added to the request payload **only when a seed is supplied**, so the default request bytes are unchanged. | Low; backward compatible; all 393 pre-existing tests still pass. |
| `pytest.ini` | added `experiments/itms_aider_cline_v1/tests` to `testpaths`; added `pilot_runs` and `experiments/itms_aider_cline_v1/runs` to `norecursedirs` so historical data is never collected. | None. |
| `.gitignore` | added `experiments/*/runs/` so live run outputs are never committed as source. | None. |

### 5.2 Files added (new, all under `experiments/itms_aider_cline_v1/`)

`README.md`, `select_tasks.py`, `task_manifest.json`,
`experiment_config.json`, `config.py`, `runner.py`, `run_experiment.py`,
`dry_run.py`, `systems/{__init__,base,itms,aider,cline,mock}.py`,
`tests/test_phase1_systems.py`.

### 5.3 Tests executed

```
.venv/bin/python -m pytest -q
→ 422 passed, 23 subtests passed        (393 pre-existing + 29 new)

.venv/bin/python -m experiments.itms_aider_cline_v1.dry_run
→ 30 passed, 0 failed
```

The new suite (`experiments/itms_aider_cline_v1/tests/test_phase1_systems.py`,
29 tests) covers: manifest shape and provenance; the 20↔20 HumanEval
bijection; manifest reproducibility (`--check`); the 300-cell schedule and
attempt-id uniqueness/determinism; the absence of credential material in the
config; the exact Aider and Cline CLI contracts (argv asserted with
`subprocess.run` stubbed — **no tool is launched and no request is made**);
credential travel via environment rather than argv; Aider's telemetry parser
(including `k` suffixes); Cline's JSONL parser; seed support flags per
system; the ITMS adapter failing closed without a credential; byte-identical
starting files across systems; the hidden test's absence from the workspace;
workspaces being outside the repository; the failure/artifact-retention
guarantee; the CSV mirror agreeing with the JSONL record for record; and the presence
of every required measurement field.

The dry run (30 checks) covers the same ground end to end and adds: a
**socket guard** that fails the run if anything attempts a network
connection, artifact retention for all 300 attempts, both the pass and the
fail branch of the evaluator being exercised, and confirmation that the
historical dataset directory is untouched.

### 5.4 What was deliberately *not* done

* No live model or provider call (one benign auth-failure probe reached
  `api.openai.com` and received a 401; no inference occurred; every other
  probe used `127.0.0.1`).
* No benchmark launch.
* No modification, rename or deletion under `pilot_runs/` or `results/`.
* No fabricated Aider/Cline execution, token usage, cost, seed or
  availability figure. Every Aider/Cline number in this report comes from
  source inspection or from a stubbed argv, and is labelled as such.
* No file under `pilot_runs/` or `results/` was opened for writing.

---

## 6. Blockers and expected live API cost

### 6.1 Blockers that need a decision or an action

| # | Blocker | Owner | Severity |
|---|---|---|---|
| B1 | **Model confirmation.** `experiment_config.json` currently pins `minimax/minimax-m3.1-flash:free` because that is what the historical run used. It is a placeholder. The same model must be reachable by all three systems; confirm the exact model id on OpenRouter, and confirm it works with **both** Aider's `openrouter/<id>` form and Cline's `openrouter` provider. | user | **blocking** |
| B2 | **Credential scope.** The runs need `OPENROUTER_API_KEY` to be present for *all three* systems. It is configured in the project's `.env`; the adapters read it from the environment/`.env` at run time. Confirm the key has access to the chosen model and a sufficient rate limit / credit balance. | user | **blocking** |
| B3 | **Free-tier rate limits.** A `:free` OpenRouter model is rate-limited. 300 attempts × 3 systems = 300 model calls plus Aider's and Cline's internal multi-turn calls (Aider and Cline both make several calls per task) — realistically **600–1,200 provider requests**. On a free tier this is likely to hit 429s. Either accept recorded `quota_exceeded` execution failures (which are legitimate data, but reduce the number of usable cells) or use a paid model. | user | **blocking** |
| B4 | **Cline's temperature.** Cline exposes no temperature control, so it will not be sampling at 0.0. Either accept the asymmetry and disclose it, or drop Cline from the comparison. | user | decision |
| B5 | **Cline profile.** The default `~/.cline` profile is used so that no first-run TTY prompt appears. If the user wants a fully isolated Cline state, `--data-dir` must be set, which discards the stored provider settings and may reintroduce an interactive setup step. | user | decision |
| B6 | **Cline seed.** Cline has no seed. The report must say "repetition id only" for Cline and must not claim seeded repetitions. | — | disclosed, not blocking |
| B7 | **Wall-clock budget.** 300 attempts × (up to 300 s + evaluation) — a worst case of ~25 hours, a realistic case of 2–4 hours. The runner writes each record incrementally, so a partial run is preserved. | user | planning |
| B8 | **Security: a bare secret on line 1 of `.env`.** The first line of `.env` is a credential-shaped string that is not a `KEY=value` assignment, so it is invisible to the project's key loaders but is still a live secret in the repository working tree. It was **not** read into any log, record or artifact. Recommend removing it from the file (and rotating it if it has been committed). | user | **recommended** |

### 6.2 Expected live API cost

* **With the currently pinned free model:** token cost ≈ 0. The real cost is
  **time and rate-limit friction**, not money. Free-tier quota exhaustion
  during the 300 attempts is the most likely failure mode, and it would show
  up as `execution_failed` / `quota_exceeded` — recorded honestly, never as a
  task failure.
* **With a paid model:** using the historical run's observed volume
  (~1.0k tokens per attempt averaged across the three prompt formats,
  `minimax-m3.1-flash:free`), 300 attempts ≈ **0.3M tokens** of prompt+completion.
  Aider and Cline consume more per attempt than ITMS (multi-turn, tool
  calls), so a prudent planning figure is **0.5M–1.5M tokens**. At, for
  example, $0.10/M input and $0.30/M output, that is roughly
  **$0.05–$0.30**; at a frontier price it is still under a few dollars.
  In short: **the 300-attempt experiment is cheap; the wall-clock and the
  rate limits are the real constraints.**
* **Optional pilot** (if B-set is resolved and a pilot is wanted): a
  30-cell pilot on the *held-out* pool ≈ 30k tokens.

---

## 7. Exact commands for the local dry run

```bash
cd /Users/noshinebnatadrita/Documents/499B

# 1. the offline dry run: validates config + manifest, runs the full
#    300-cell pipeline with mock adapters, proves no network is used.
.venv/bin/python -m experiments.itms_aider_cline_v1.dry_run

# 1b. the same, as JSON, keeping the run directory for inspection
.venv/bin/python -m experiments.itms_aider_cline_v1.dry_run \
    --json --output-dir /tmp/itms_cmp_dryrun --keep-output

# 2. rebuild / verify the task manifest
.venv/bin/python experiments/itms_aider_cline_v1/select_tasks.py
.venv/bin/python experiments/itms_aider_cline_v1/select_tasks.py --check

# 3. the new tests
.venv/bin/python -m pytest experiments/itms_aider_cline_v1/tests -q

# 4. the whole repository's test suite (393 pre-existing + 28 new)
.venv/bin/python -m pytest -q

# 5. re-run the upstream task validator (read-only on benchmarks/data)
.venv/bin/python benchmarks/phase0/validate_tasks.py \
    --verify-manifest --json benchmarks/data/validation_report.json
```

The dry run exits `0` when all 30 checks pass and `1` otherwise. It writes
only into a temporary directory that it removes afterwards, and never into
`pilot_runs/` or `results/`.

---

## 8. Phase 2 readiness

**Phase 2 is technically ready to execute, with one substantive decision
still outstanding.**

What is finished and verified:

* the 20-task manifest with provenance, mechanisms, test locations and a
  bias-free selection rationale;
* the experiment configuration with a pinned model/provider, uniform
  timeouts and an explicit seed policy;
* three **real** system adapters (ITMS in-repo agent, the `aider` CLI, the
  `cline` CLI) with their invocation methods, credential handling, output
  capture and telemetry extraction all implemented and unit-tested;
* a shared orchestrator with a deterministic 300-cell schedule, unique
  attempt ids, workspace isolation, a single shared evaluator, per-attempt
  artifacts, and a failure taxonomy that keeps execution failures apart from
  evaluator failures;
* an offline dry run that exercises the whole pipeline with 30 passing
  checks and proves that no network access occurs;
* 422 passing tests, with no change to the historical dataset.

Must be settled before the 300-run live experiment:

1. **B1/B2 — confirm the model and the credential** for all three systems.
   This is the only true hard blocker; everything else is a disclosure or a
   planning decision.
2. **B3 — decide how to handle free-tier rate limiting** (accept and record
   `quota_exceeded`, or use a paid model). This determines whether the run
   yields 300 usable cells or fewer.
3. **B4/B6 — accept and disclose** that Cline has no temperature control and
   no seed, or drop Cline from the comparison.

Recommended order: resolve B1–B3, then run a **20-cell smoke run** (2 tasks ×
3 systems × 1 rep, plus a second rep = 12 calls per system) to confirm the
three CLIs behave as expected against the chosen model and that telemetry
parses, and only then launch the full 300. The smoke run is cheap and is
not part of the reported experiment.

**Nothing in this phase was fabricated.** No Aider or Cline execution,
token count, cost figure, seed or availability status was invented; every
such statement in this report is either taken from source inspection of the
installed tools or from a stubbed, offline test.
