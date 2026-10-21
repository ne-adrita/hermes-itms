# ITMS vs Aider vs Cline — Phase 1 preparation

Preparation for a real, three-way comparison of three **real** code-editing
systems over the same 20 HumanEval-derived repair tasks, 5 repetitions per
cell, **300 planned attempts**.

Nothing in this directory has been run against a live provider. The only
executable path is the offline dry run.

```
select_tasks.py        builds the task manifest from a fixed a priori rubric
task_manifest.json     20 tasks: provenance, mechanism, test path, rationale
experiment_config.json model / provider / reps / timeouts / seed policy
config.py              ExperimentConfig loader
systems/base.py        the system-adapter interface + shared record types
systems/itms.py        the real ITMS agent (skeletonise -> LLM -> AST splice)
systems/aider.py       the real `aider` CLI, driven as a subprocess
systems/cline.py       the real `cline` CLI, driven as a subprocess
systems/mock.py        offline stand-ins used only by the dry run
runner.py              the orchestrator: schedule, workspaces, evaluation, artifacts
run_experiment.py      CLI entry point (Phase 2)
integrity.py           independent validation of a finished run (Phase 2)
analyze.py             metrics, 10 figures and the reports (Phase 3)
dry_run.py             offline validation; never touches the network
tests/                 offline test suite
```

## Budget gate

The runner refuses to start a live run unless an explicit maximum spend is
configured:

* `max_spend_usd` — the maximum cumulative **reported** cost. The run stops
  issuing further provider calls once the cumulative reported cost reaches
  it. A breach is recorded, not silently swallowed.
* `require_free_model` — the runner refuses to start unless `model_id` is a
  zero-cost (`:free`) model id, so an accidental edit to `model_id` cannot
  turn a free run into a paid one.

Both are enforced in `ExperimentRunner.check_budget_preflight()`, which runs
before the first provider call.

## Design

**Same for every system.** The same task instruction, the same starting file
(`candidate.py`, byte-identical across systems), the same hidden official
test, the same evaluator (`benchmarks.phase0.runner.run_official_test`), the
same timeout policy, the same model and provider.

**Different on purpose.** How the file gets edited, and how the tool is
driven. That is the independent variable.

**Isolation.** Each attempt gets a fresh workspace under the system temp
directory — deliberately *not* inside this repository, because both Aider and
Cline walk upwards looking for a repository root and would otherwise be able
to see the whole project. The hidden official test is not in the workspace
while the system runs; it is only used afterwards, by the evaluator.

**Failure accounting.** `execution_failed` (the tool never produced a
candidate), `invalid_candidate` (it produced something unparsable or did not
edit) and `repair_success` / `repair_failed` (the candidate reached the
hidden test) are separate statuses. A failed attempt still keeps its
artifacts.

**Seeds.** A seed is *requested* for every cell. It is only *applied* by
systems that expose a request-level seed: ITMS (a `seed` field on the chat
completions payload) and Aider (via its model-settings `extra_params`). Cline
has no seed parameter, so its cells are identified by `repetition_id` only.
Every record carries `seed_requested`, `seed_supported` and `seed_applied`,
so "we asked for a seed" and "a seed was used" are never conflated.

## Commands

Offline dry run (no network, no subprocess, no provider):

```bash
cd /Users/noshinebnatadrita/Documents/499B
.venv/bin/python -m experiments.itms_aider_cline_v1.dry_run
```

Rebuild / verify the task manifest:

```bash
.venv/bin/python experiments/itms_aider_cline_v1/select_tasks.py
.venv/bin/python experiments/itms_aider_cline_v1/select_tasks.py --check
```

Tests:

```bash
.venv/bin/python -m pytest experiments/itms_aider_cline_v1/tests -q
```

Phase 2 (live) — the 300-cell matrix:

```bash
.venv/bin/python -m experiments.itms_aider_cline_v1.run_experiment \
    --mode live --yes \
    --output-dir experiments/itms_aider_cline_v1/runs/itms_aider_cline_v1_live_<UTC>
```

Phase 2 validation (independent re-derivation from the files on disk):

```bash
.venv/bin/python -m experiments.itms_aider_cline_v1.integrity \
    --run-dir experiments/itms_aider_cline_v1/runs/itms_aider_cline_v1_live_<UTC>
```

Phase 3 analysis (metrics, 10 figures, reports):

```bash
.venv/bin/python -m experiments.itms_aider_cline_v1.analyze \
    --run-dir experiments/itms_aider_cline_v1/runs/itms_aider_cline_v1_live_<UTC>
```

## Outputs

A run directory contains `attempts.jsonl`, `attempts.csv`, `summary.json`,
`run_metadata.json`, `BENCHMARK_INTEGRITY_REPORT.md` and
`artifacts/<attempt_id>/`. The analysis directory (default
`<run dir>/analysis`) contains `analysis.json`, `fig1…fig10*.png`,
`COMPARATIVE_RESULTS_REPORT.md` and `RUN_SUMMARY.md`. The historical
120-attempt dataset under `pilot_runs/` is never read, appended to, or
rewritten.
