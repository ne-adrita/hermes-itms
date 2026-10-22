# Phase 2 Final Handover

**Project:** `/Users/noshinebnatadrita/Documents/499B` (Hermes / ITMS capstone)
**Date:** 2026-10-22
**Baseline commit:** `3b0ffce`
**No paid/live API call was made in this task. No historical benchmark data was modified.**

---

## 1. What was fixed

### 1.1 The surgical splicer had a real, silent corruption bug — now fixed

`surgical_splicer.py` classified a candidate as "body-only" by looking at its
**first line**. A body-only replacement whose first statement was a nested
`def` was therefore mis-classified as a *full-node* replacement: the target's
`def` header was overwritten and the nested function was emitted at the
target's base indentation — **hoisted out of the method body** — while
`splice()` still returned `success=True`. The file usually remained
syntactically valid, so the corruption passed the syntax gate and could only
be caught by an evaluator (if one ran at all).

The candidate is now classified **semantically** (parse → does it define the
target's own name?), a body-only candidate keeps the target's header and is
re-indented to the target's real body indentation, and a nested `def`/`class`
stays nested. Two characterisation tests that *pinned the bug* were rewritten
to pin the correct behaviour; the historical benchmark runs measured against
the unfixed splicer were left byte-identical.

### 1.2 The splicer could report a false success — now fails closed

* The target is re-located in the file being edited; a **stale** target is rejected.
* The spliced file must parse.
* The target must **still exist** in the spliced file — an edit that removes or
  replaces the target with something else is rejected, not reported as success.
* `splice_multiple` no longer falls back to a stale target when re-location fails.
* An **ambiguous** target name (the same method name in two classes) is now
  detected and rejected instead of silently resolving to the first match.
* Module-level helpers are inserted after any shebang/encoding header instead
  of at byte 0.

### 1.3 The ITMS pipeline now really goes through locator → splice → validate → evaluate

`relay_orchestrator.py` previously handed the raw model response to the
execution harness, where the splice was a **silent best-effort** attempt: if
the splice failed, the harness fell back to *appending* the candidate to the
module and evaluating that. An invalid edit could therefore still come back
green.

Now `RelayOrchestrator.prepare_candidate()` performs the ITMS contract
explicitly (locate → splice → validate) and returns an explicit status
(`spliced` / `fallback` / `splice_failed` / `no_candidate`). The **complete
spliced source file** is what reaches the evaluator, a rejected splice is a
**failed attempt** (never a pass), a missing candidate is never executed and
never counted as a pass, and `verified_code` now returns the artefact that was
actually verified instead of the raw model text. `CascadeRunResult` gained
`outcome_status`, `evaluated`, `candidate_status`, `splice_status`,
`splice_error` and `attempts`.

Bounded Tier-1 retries and the single Tier-2 escalation were already in place
and were left as they were.

### 1.4 Fabricated benchmark measurements removed

`edit_orchestrator.py` used to hard-code `1650.0 ms` (full-file),
`520.0 ms` (search/replace), `185.0 ms` (ITMS), a fake `73` cached tokens, and
`success=True` for the full-file strategy regardless of the evaluator. The
dashboard then rendered `SUCCESS (100%)`, `100% Guaranteed` and an `8x`
latency acceleration derived from those constants.

* All hard-coded values are gone. Latency is measured with
  `time.perf_counter()` (monotonic) around an **identical** work definition for
  all three strategies (apply the edit mechanism **and** evaluate the
  resulting complete source).
* Token counts are local tiktoken **estimates** and are labelled
  `local_estimate`; costs are labelled `estimated_from_local_token_estimate`;
  the cached-token figure is reported as **unavailable** instead of invented.
* Success/failure/timeout/missing-candidate/unevaluated/invalid-edit are
  recorded **separately** (`status` + per-attempt counters).
* All three strategies use the same task, the same evaluator, the same
  repetition policy (`repetitions`) and the same measurement definitions.
* `latency_speedup_factor` is now `None`: no speedup is claimed from local
  processing times, because no provider request is made in this mode.
* The report object carries `measurement_mode = "simulated_local_processing"`,
  `is_measurement = False` and an explicit `notes` string.

### 1.5 The dashboard is now truthful

* `SUCCESS (100%)` and `100% Guaranteed` removed; the matrix shows the real
  per-strategy **status** and **evaluated attempts**.
* A prominent warning states that the Phase 2 tab is a **simulated comparison,
  not a measured benchmark run**, and explains what each number is.
* The relay tab shows the real `outcome_status`, the splice status, the
  attempt counts and the evaluated flag.
* The macro-benchmark tab labels a **mock run** as "not a live measurement".
* The existing tabs, navigation and inspection panels all still work (verified
  headless: 0 exceptions, 8 tabs).

### 1.6 Sandbox claims corrected

The execution gate is a **subprocess with a scrubbed environment and a
timeout** — there is no Docker, no container, no namespace, no memory/CPU
limit, and the child process keeps the real file-system and network
permissions. This is now documented in `sandbox.py`
(`ISOLATION_LEVEL = "process_boundary_only"`, `ISOLATION_ENFORCED`,
`ISOLATION_NOT_ENFORCED`, `describe_isolation()`) and surfaced in the
dashboard. The "Docker Sandbox Hardening" item is a **roadmap** item in the
report and was deliberately **not** built in this task.

---

## 2. Files changed

| File | Change |
| --- | --- |
| `surgical_splicer.py` | Locator rewritten (qualified-path resolution, ambiguity detection, `locate_target_ex`, `body_indentation`); splicer rewritten (semantic candidate classification, nested-scope preservation, stale-target / missing-target / invalid-replacement rejection, target-survival check, header-safe helper insertion). |
| `relay_orchestrator.py` | Added `PreparedCandidate`, `prepare_candidate()`, `evaluate_candidate()`; `run_cascade` now evaluates the complete spliced source, records `outcome_status` / `splice_status` / `attempts`, and delivers the verified artefact. |
| `edit_orchestrator.py` | Rewritten around explicit metric provenance and status vocabulary; hard-coded latencies, fake cache-hit tokens and forced success removed; identical measurement definitions for all three strategies. |
| `sandbox.py` | Isolation level documented and exposed via `describe_isolation()`; harness docstring corrected. |
| `app.py` | Dashboard made truthful (Phase 2 tab, relay tab, macro-benchmark tab, isolation panel). |
| `benchmarks/benchmark/adapters/itms_splice.py` | Docstring only: the "known limitation" note now states the defect has been fixed and that the historical runs are preserved. Adapter logic unchanged. |
| `test_surgical_splicer.py` | +12 focused splicer regression tests; the tri-way metrics test rewritten to assert provenance instead of a fabricated 2x speed-up; +3 honest-metric tests. |
| `test_benchmark_safety.py` | The "known limitation" characterisation test rewritten as a regression guard for the fixed behaviour. |
| `benchmarks/tests/test_phase1_adapters.py` | The "limitation is preserved" test rewritten as a regression guard for the fixed behaviour. |
| `test_itms_phase2_e2e.py` | **New.** Deterministic end-to-end repair test through the real pipeline using the `HE-EDIT-009` benchmark fixture and a mock dispatcher (no paid call). |
| `pytest.ini` | `test_itms_phase2_e2e.py` added to `testpaths` so the root-level `pytest` run collects it. |

**Not touched:** `pilot_runs/`, `results/`, `benchmarks/data/`,
`experiments/**/runs/`, every historical report, and `.env`.

**Regression risk checked:** the new ambiguity rejection (a name that exists in
two places is refused rather than guessed) cannot affect the 20-task benchmark
dataset — an AST scan of every `benchmarks/data/tasks/HE-EDIT-*/mutated_source.py`
shows **no duplicate definition names** in any file, so every task's target
still resolves unambiguously.

---

## 3. Tests run, and their actual results

All commands were run from `/Users/noshinebnatadrita/Documents/499B` with the
project virtualenv (`.venv/bin/python`).

| Command | Result |
| --- | --- |
| `.venv/bin/python -m pytest -q` | **471 passed, 23 subtests passed in 60.43s** |
| `.venv/bin/python -m pytest test_surgical_splicer.py -q` | **30 passed in 1.69s** |
| `.venv/bin/python -m pytest test_surgical_splicer.py test_itms_phase2_e2e.py -q` | **36 passed in 3.14s** |
| `.venv/bin/python -m pytest test_itms_phase2_e2e.py -q` | **6 passed in 1.64s** |
| `.venv/bin/python -m pytest test_milestones_2_3.py -q` | **19 passed in 2.20s** (the 5-phase cascade, self-repair, escalation) |
| `.venv/bin/python -m pytest test_benchmark_safety.py benchmarks/tests/test_phase1_adapters.py -q` | **49 passed in 9.58s** |
| `.venv/bin/python -m pytest test_milestones_2_3.py test_surgical_splicer.py -q` | **35 passed in 3.46s** (run after the splicer rewrite, before the metric rewrite) |

**Final verification run (2026-10-22, after all changes and after both
documents were written):**

| Command | Result |
| --- | --- |
| `.venv/bin/python -m pytest -q` | **471 passed, 23 subtests passed in 70.85s — exit code 0** |
| `git diff --check` | **exit code 0** (no whitespace errors / conflict markers) |
| `git status --short` | only the 10 intended source/test files modified + 3 new untracked files (`test_itms_phase2_e2e.py`, `PHASE2_FINAL_ACCEPTANCE_MATRIX.md`, `PHASE2_FINAL_HANDOVER.md`); no historical artefact touched |
| `.venv/bin/python experiments/itms_aider_cline_v1/integrity.py --run-dir …/itms_aider_cline_v1_live_20261021T171639Z --output <tmp>` | **32/32 checks passed, 0 failed, 300/300 recorded attempts, 0 missing cells** (the report was written to a temp path so the archived `BENCHMARK_INTEGRITY_REPORT.md` was not overwritten) |
| Six e2e tests re-run with `socket.connect` / `create_connection` / `getaddrinfo` blocked and `OPENROUTER_API_KEY` / `ANTHROPIC_API_KEY` removed | **6 ran, 0 failures, 0 errors** — proof that the e2e path uses a mock provider and makes no network call |

Dashboard verification (headless, `streamlit.testing.v1.AppTest`):

| Check | Result |
| --- | --- |
| `AppTest.from_file("app.py").run()` | **0 exceptions, 8 tabs** |
| Same, after clicking *"Run Comparative Edit Benchmark"* | **0 exceptions**; the rendered matrix showed `passed` / `1/1 passed` / `(local tokenizer estimate)` / `token source: local_estimate` / `cached tokens: unavailable` |

Pipeline behaviour observed end-to-end on the `HE-EDIT-009` fixture (mock
dispatcher, 0 paid calls):

| Model output | `outcome_status` | `final_success` | `splice_status` | dispatcher calls |
| --- | --- | --- | --- | --- |
| gold fix (fenced) | `passed` | True | `spliced` | 1 |
| unchanged buggy code | `failed` | False | `spliced` | 3 (tier1 + 1 repair + tier2) |
| syntactically invalid | `failed` | False | `splice_failed` | 4 |
| empty response | `missing_candidate` | False | `no_candidate` | 4 |
| infinite loop | `timeout` | False | `spliced` | (bounded, harness timeout 0.5 s) |

**End-to-end test: PASSED.** `test_itms_phase2_e2e.py` runs the real
`RelayOrchestrator` → `ASTNodeLocator` → `ASTSurgicalSplicer` →
`ExecutionHarness` path on a real benchmark task, and asserts that a correct
repair passes both the syntax validation and the official evaluator, while an
invalid edit, a timeout and a missing candidate can never be reported as a
successful repair.

---

## 4. Which metrics are measured, estimated, mocked or unavailable

| Metric | In the Phase 2 tab (`edit_orchestrator.py`) | In the relay tab (`relay_orchestrator.py`) | In the ITMS-vs-Aider-vs-Cline tab (`comparative_viewer.py`) |
| --- | --- | --- | --- |
| Output / input tokens | **local tiktoken estimate** (`token_source = "local_estimate"`) | provider-reported when the provider reports it; mock values in mock mode | provider-reported (ITMS/Aider) and tool-reported (Cline); `null` where the provider did not report, never `0` |
| Cached tokens | **unavailable** (no provider request → no cache hit to report) | `itms_prompt_data["static_token_count"]` (a *prompt construction* count, i.e. the size of the cacheable prefix, not an observed cache hit) | `cache_read_tokens` where the provider reported it, else `null` |
| Cost | **estimate** = local token estimate × catalogue price (`cost_source = "estimated_from_local_token_estimate"`) | modelled from the catalogue price profile; **not billed spend** | provider-reported cost where available, else `null` ("unavailable") |
| Latency | **measured** with `time.perf_counter()`, local processing + evaluation only; **no provider request**, so no model latency and **no speedup factor is reported** | **measured** end-to-end (`total_duration_ms`) with the monotonic clock | **measured** per attempt (wall clock around the system run) |
| Success | decided **only** by the shared evaluator; no evaluator → `unevaluated`, never `passed` | decided only by the execution gate; `passed` / `failed` / `timeout` / `missing_candidate` / `unevaluated` | decided by the shared evaluator; unevaluated attempts are excluded from the pass-rate denominator |
| Failure categories | `invalid_edit` recorded separately | `splice_failed` / `no_candidate` recorded separately | `no_candidate`, `execution_failed`, `timeout`, `patch_not_applied`, `syntax_error` kept separate |

**Mock vs real:** every mock-produced result is labelled as such
(`is_mock`, `contains_mock_results`, "MOCK RUN - not a live measurement" in
the macro-benchmark tab). The Phase 2 tab is a **simulation** and says so.

---

## 5. Remaining report-to-code gaps

1. **The Phase 2 dashboard tab is a simulation, not a benchmark.** It compares
   the three *edit mechanisms* on candidate code the user supplies. It cannot
   support an end-to-end claim about which method is faster, cheaper or more
   accurate. A live tri-way benchmark would cost money and was not authorised.
2. **The Docker sandbox hardening is still not implemented.** The report lists
   it as a roadmap item; the gate remains a process boundary. It must not be
   exposed to untrusted code.
3. **No new accuracy claim is possible from the current dataset.** In the live
   300-attempt run, all three systems passed 100 % of the attempts they
   actually evaluated, so the data supports an *efficiency* comparison only.
4. **The splicer fix is not reflected in any historical run.** The archived
   runs were measured against the unfixed splicer; they are preserved
   unchanged, and any ratio quoted from them describes that dataset only.
5. **The report's headline numbers** ("94 % output-token reduction", "8× faster",
   "54 tests", "100 % pass rate in 2.38 s") came from the hard-coded
   measurements that were removed in this task. The in-repo Phase 2 report
   (`PHASE_2_COMPREHENSIVE_REPORT.md`) is a historical document and was
   deliberately **not** rewritten; treat its numbers as superseded by
   `PHASE2_FINAL_ACCEPTANCE_MATRIX.md`.
6. **`499B PU2 Report.pdf` is not in the repository.** The matrix was written
   against `PHASE_2_COMPREHENSIVE_REPORT.md` (§2.1–2.5). If the PDF is a
   different document, the matrix must be re-checked against it.
7. **The macro-benchmark suite's cost figures are still modelled** from the
   catalogue price profile; only token counts differ between mock and live.

---

## 6. Exact commands to run next

```bash
cd /Users/noshinebnatadrita/Documents/499B

# 1. Full suite (471 tests, ~60 s, no network, no paid calls)
.venv/bin/python -m pytest -q

# 2. The two modules changed most heavily
.venv/bin/python -m pytest test_surgical_splicer.py test_itms_phase2_e2e.py -q

# 3. The cascade / self-repair / escalation suite
.venv/bin/python -m pytest test_milestones_2_3.py -q

# 4. The rewritten characterisation tests
.venv/bin/python -m pytest test_benchmark_safety.py benchmarks/tests/test_phase1_adapters.py -q

# 5. Run the dashboard and check the Phase 2 tab
.venv/bin/python -m streamlit run app.py
#   then open http://localhost:8501/ -> "Surgical Code Modification (Phase 2)"

# 6. Optional: verify the dashboard renders headless
.venv/bin/python -c "
from streamlit.testing.v1 import AppTest
at = AppTest.from_file('app.py', default_timeout=300); at.run()
print('exceptions:', len(at.exception), 'tabs:', len(at.tabs))
"
```

If a **live** tri-way benchmark is wanted later, it needs explicit approval
because it spends API credit; the runner already enforces
`max_spend_usd` and a `require_free_model` guard
(`experiments/itms_aider_cline_v1/`).
