# Phase 2 Final Acceptance Matrix

**Repository:** `/Users/noshinebnatadrita/Documents/499B`
**Date of this matrix:** 2026-10-22
**Baseline commit:** `3b0ffce` ("Add the ITMS vs Aider vs Cline experiment, its live run and the comparative dashboard")

---

## 0. Source of the requirements

The file `499B PU2 Report.pdf` **does not exist** in the repository, in
`~/Documents`, or anywhere reachable from the project root (`find` over the
repo and over `~/Documents` for `*.pdf` / `*PU2*` returned nothing). The
authoritative Phase 2 requirement set that is actually present in the
repository is **`PHASE_2_COMPREHENSIVE_REPORT.md` §"499B Phase 2 Architecture:
AST-Guided Surgical Modification & Speculative Edit Cascade"**, which enumerates
the four/five Phase 2 layers 2.1 – 2.5. This matrix is written against that
document, and the user's prioritised list (items 1 – 8) is mapped onto the
same requirement set in §2 below.

Everything in the "Test and actual result" column was executed in this
session; the exact commands are listed in `PHASE2_FINAL_HANDOVER.md` §3.

**Status legend**

| Status | Meaning |
| --- | --- |
| PASS | Implementation evidence *and* relevant passing tests (including at least one end-to-end path where the requirement is an integration requirement) |
| PARTIAL | Implemented and tested for part of the requirement; a stated part is missing or unverified |
| FAIL | Not implemented, or implemented in a way the evidence contradicts |
| BLOCKED | Cannot be completed in this scope (e.g. requires infrastructure / spend that was not authorised) |

---

## 1. Phase 2 requirements from the report (2.1 – 2.5)

### 2.1 AST Node Locator & Coordinate Mapping

| Field | Value |
| --- | --- |
| **Requirement** | Locate the target function / async method / class method by name or qualified name; extract exact 1-indexed line coordinates, body statements and the base indentation level. |
| **Relevant file / function** | `surgical_splicer.py` → `ASTNodeLocator.locate_target`, `ASTNodeLocator.find_matches`, `ASTNodeLocator.locate_target_ex` |
| **Original gap** | The locator resolved a *simple* name by returning the **first** match it happened to find (`search_scope` recursed into classes in file order). A name that exists in two classes could therefore silently resolve to the wrong function, and the caller had no way to distinguish "not found" from "ambiguous" — both were collapsed into `None`. Qualified names were resolved by ad-hoc recursion rather than by an explicit path, so `Outer.Inner.method` and a module-level `foo` were not resolved by a single, checkable rule. |
| **Change made** | Rewrote the locator around an explicit path model. Every function/class is collected with its **qualified path** and its **nearest enclosing class**. Resolution is now: (1) exact qualified-path match wins; (2) otherwise, for a single-segment name, all nodes with that name are collected. If more than one exists the name is **ambiguous** and `locate_target` returns `None` instead of guessing. `locate_target_ex()` returns a precise diagnostic ("not found" vs "ambiguous: matches N definitions") and a source that does not parse is reported as such. `SurgicalTarget` gained `body_indentation` (the real indentation of the target's first body statement, used for correct body-only re-indentation). |
| **Test and actual result** | `test_surgical_splicer.py::TestSurgicalSplicerRegression::test_ambiguous_target_is_rejected` (asserts `locate_target` → `None` and "ambiguous" in the error); `::test_missing_target_is_rejected`; `::test_source_with_syntax_error_is_handled_safely`; `::test_class_method_replacement_keeps_the_class` (parent class resolved as `Other`). `test_surgical_splicer.py` → **30 passed**; combined with the e2e module → **36 passed**. |
| **Status** | **PASS** |
| **Remaining limitation** | A *fully* qualified path is still only as unambiguous as the file: two identically-named methods in the same class cannot exist in valid Python, but two methods with the same name in *different* classes require the qualified form. The dashboard and the benchmark adapter both use the qualified form. |

### 2.2 Mathematical Line-Boundary Slicing & Comment Preservation

| Field | Value |
| --- | --- |
| **Requirement** | Replace only the target's line range; every comment, docstring and code block outside the target must remain byte-for-byte preserved (no `ast.unparse()`). |
| **Relevant file / function** | `surgical_splicer.py` → `ASTSurgicalSplicer.splice`, `ASTSurgicalSplicer.splice_multiple` |
| **Original gap** | The splice could **destroy the target** and still report success (see 2.3). It also accepted a *stale* target: `splice()` never checked that the supplied `SurgicalTarget` still corresponded to the node in the file being edited, so a target from a stale parse would be spliced into the wrong line range. `splice_multiple` fell back to the stale target (`fresh_target or target`) when re-location failed, which is exactly the case where the edit is unsafe. Module-level helper functions extracted from a candidate were prepended to the very top of the file, which would corrupt a shebang / encoding header. |
| **Change made** | The splice is now guarded on three levels: (a) the target is re-located in the source being edited and its line range must match the supplied target, otherwise the edit is rejected as *stale*; (b) the spliced file must parse; (c) the target must **still exist** in the spliced file — an edit that would remove or replace the target with something else is rejected instead of being reported as a success. `splice_multiple` now fails closed when a target cannot be re-located (no stale fallback). Module-level helpers are inserted *after* any shebang/encoding comment block instead of at byte 0. |
| **Test and actual result** | `test_surgical_splicer.py::TestSurgicalSplicerRegression::test_unrelated_source_is_preserved_byte_for_byte` (copyright header, author header, `import math`, `CONSTANT = 42`, `unrelated_helper`, `sibling`, `class Other:` all preserved; line count changed by exactly the target's 5 lines → 2 lines); `::test_stale_target_is_rejected` (rejected, original source returned unchanged); `::test_full_node_replacement_replaces_only_the_target`. `test_surgical_splicer.py` → **30 passed**. |
| **Status** | **PASS** |
| **Remaining limitation** | Comments *inside* the target's line range are removed by design (the range is replaced). Docstrings inside the target are replaced with the candidate's. |

### 2.3 Auto-Indentation Normalization & preservation of Python scope

| Field | Value |
| --- | --- |
| **Requirement** | Re-indent model output to the target's indentation level so a candidate emitted at column 0 does not raise `IndentationError`; preserve Python scope. |
| **Relevant file / function** | `surgical_splicer.py` → `ASTSurgicalSplicer._classify_candidate`, `._indent_block`, `._parse_candidate`; `SurgicalTarget.body_indentation` |
| **Original gap** | **This was the confirmed correctness bug.** The candidate was classified as "body-only" purely by looking at its *first line*: if the first line started with `def `, `class ` or `@`, the candidate was treated as a **full-node replacement**. A body-only replacement whose first statement was a nested `def` was therefore classified wrongly: the target's `def` header was overwritten and the nested function was emitted at the target's base indentation — i.e. **hoisted out of the method body** — and `splice()` still returned `success=True`. The file often stayed syntactically valid, so the corruption was invisible to the syntax gate and only the evaluator could catch it (if one ran at all). The defect was explicitly pinned by two characterisation tests (`TestKnownSplicerLimitation`, `test_known_nested_function_limitation_is_preserved`) that asserted the *bug* was still present, so it could not be fixed without changing the baseline. |
| **Change made** | Candidate classification is now **semantic**, not textual: the candidate is parsed (as a module, and if that fails, as a function body, so a bare `return ...` is legal) and then classified as `full_node` (it defines the target's own name), `body_only` (it does not) or rejected. A body-only candidate keeps the target's header and is re-indented to the target's **body** indentation (`target.body_indentation`, taken from the original file rather than assumed to be 4 spaces), so a nested `def`/`class` stays **nested** at the correct level. A candidate that wraps the target in a redundant class is unwrapped to the target definition. A candidate that defines a *different* class for a class target is rejected. The two characterisation tests were rewritten to pin the **correct** behaviour (the historical benchmark runs measured against the unfixed splicer were left byte-identical). |
| **Test and actual result** | `test_surgical_splicer.py::TestSurgicalSplicerRegression::test_nested_function_stays_inside_the_target_body` (the nested `helper` is a child of `method` in the AST and is *not* a module-level `FunctionDef`); `::test_body_only_replacement_keeps_the_header`; `::test_full_node_replacement_replaces_only_the_target`; `test_benchmark_safety.py::TestSplicerNestedFunctionRegression::test_body_only_nested_def_replacement_stays_nested`; `benchmarks/tests/test_phase1_adapters.py::TestItmsSpliceAdapter::test_nested_function_replacement_stays_nested`. Manual reproducer (nested class `Outer.Inner.method`) now yields the correct nested body instead of a hoisted module-level `def`. `test_surgical_splicer.py` → **30 passed**; `test_benchmark_safety.py` + `benchmarks/tests/test_phase1_adapters.py` → **49 passed**. |
| **Status** | **PASS** |
| **Remaining limitation** | The body indentation is taken from the *original* target body. If the original body used tabs, the candidate is indented with tabs — consistent with the file, but the candidate itself is always normalised to 4-space-relative dedent by `textwrap.dedent`, so a mixed tab/space candidate is rejected by the syntax gate rather than silently mis-indented. |

### 2.4 Deterministic Gate & Speculative Edit Orchestrator

| Field | Value |
| --- | --- |
| **Requirement** | Tier 1 produces only the modified target node; the spliced file is verified in an isolated subprocess at $0 API cost; a failed patch is discarded and the isolated error trace is escalated to Tier 2. |
| **Relevant file / function** | `relay_orchestrator.py` → `RelayOrchestrator.prepare_candidate`, `.evaluate_candidate`, `.run_cascade`; `sandbox.py` → `ExecutionHarness.execute_test`; `benchmarks/benchmark/adapters/itms_splice.py` → `ItmsAstSpliceAdapter.parse_and_apply` |
| **Original gap** | The relay orchestrator never called the AST locator or the surgical splicer itself. It handed the raw model response to `ExecutionHarness.execute_test()`, where the splice was a **silent, best-effort** attempt: if the splice failed, the harness silently fell back to *appending* the candidate to the module and evaluating that instead. A candidate whose splice had been rejected could therefore still produce a green evaluation (the appended module-level function can satisfy the test), i.e. an **invalid edit could be reported as a successful repair**. The orchestrator also had no vocabulary to distinguish *passed* from *failed* / *timeout* / *missing candidate* / *unevaluated*: it only had `final_success: bool`, and `verified_code` returned the **raw model text**, not the artefact that was actually verified. |
| **Change made** | `prepare_candidate()` now implements the ITMS contract explicitly: locate → splice → validate. It returns a `PreparedCandidate` with an explicit status (`spliced` / `fallback` / `splice_failed` / `no_candidate`). The **complete spliced source file** is what is handed to the evaluator (`context=""` when the splice was applied, so the harness cannot re-splice or silently fall back). A rejected splice is recorded as a **failed attempt** and can never be reported as a success (`attempt_ok = exec.success and status != SPLICE_FAILED`). A missing candidate is never executed and never counted as a pass. `evaluate_candidate()` synthesises an explicit `NoCandidate` failure instead of executing nothing. `CascadeRunResult` gained `outcome_status` (`passed` / `failed` / `timeout` / `missing_candidate` / `unevaluated`), `evaluated`, `candidate_status`, `splice_status`, `splice_error` and `attempts`. `verified_code` now returns the **verified artefact** (the spliced file) instead of the raw model text. Bounded retries (`max_tier1_retries`) and the single Tier-2 escalation were kept as they were — they already existed and are bounded. |
| **Test and actual result** | `test_itms_phase2_e2e.py` (new, 6 tests, real benchmark fixture `HE-EDIT-009`, mock dispatcher, **no paid call**): `test_successful_repair_passes_syntax_validation_and_the_evaluator` → `splice_status == "spliced"`, delivered artefact contains `a % b` and not `a // b`, `ast.parse` succeeds, evaluator passes, `outcome_status == "passed"`; `test_buggy_candidate_is_not_reported_as_a_successful_repair` → `failed`; `test_invalid_edit_is_not_reported_as_a_successful_repair` → `splice_failed` + "REJECTED" in the trace; `test_missing_candidate_is_not_reported_as_a_successful_repair` → `missing_candidate`; `test_timeout_is_not_reported_as_a_successful_repair` → `timeout`; `test_no_provider_request_is_made`. Existing `test_milestones_2_3.py` (the 5-phase cascade, self-repair, escalation, class-wrapped and multi-block candidates) → **19 passed**. Observed pipeline trace for the fixture: gold → `passed/spliced` (1 call), buggy → `failed` (3 calls), invalid → `failed/splice_failed` (4 calls), empty → `missing_candidate` (4 calls). |
| **Status** | **PASS** |
| **Remaining limitation** | The execution gate is a **subprocess with a scrubbed environment**, not an OS-level sandbox (see requirement 2.4/security, §3 E below). The gate cannot detect a *semantically* wrong edit; it can only detect syntax errors, non-zero exits, timeouts and failing assertions. Tier 2 is invoked at most once per run. |

### 2.5 Tri-Way Comparative Benchmark & UI Integration

| Field | Value |
| --- | --- |
| **Requirement** | An interactive benchmark comparing Full-File Regeneration, Text-Based Search/Replace and ITMS AST-Guided Surgical Splicing **on the same task**, with live telemetry for output-token reduction, dollar cost, latency and patch-application reliability, plus side-by-side code inspection. |
| **Relevant file / function** | `edit_orchestrator.py` → `SurgicalEditOrchestrator.run_benchmark`, `EditStrategyResult`, `TriWayEditBenchmarkReport`; `app.py` → tab *"Surgical Code Modification (Phase 2)"*; `comparative_viewer.py` → the real ITMS-vs-Aider-vs-Cline dataset tab |
| **Original gap** | **The dashboard's headline numbers were fabricated.** `edit_orchestrator.py` hard-coded `full_latency = 1650.0`, `diff_latency = 520.0`, `itms_latency = 185.0` (the comment claimed "Sequential generation of 100+ lines takes ~1.65s" — a constant, not a measurement), hard-coded `itms_cached_tokens = 73` ("Static prefix cached at token 0" — an invented cache-hit figure), and forced `success=True` for the Full-File strategy regardless of the evaluator. The `latency_speedup_factor` derived from these constants was presented as a measured "8x acceleration". The app then rendered `SUCCESS (100%)` and `100% Guaranteed` for ITMS regardless of what happened, and `100% of non-target comments ... are preserved` as a blanket claim. The three strategies were also not measured on the same basis: the Full-File row included the evaluation in its timing while the diff and ITMS rows did not. |
| **Change made** | `edit_orchestrator.py` rewritten around explicit provenance: every metric carries a source label (`token_source`, `cost_source`, `latency_source`) and a status vocabulary (`passed` / `failed` / `timeout` / `missing_candidate` / `unevaluated` / `invalid_edit`). All hard-coded latencies and the invented cache-hit count are gone; latency is measured with `time.perf_counter()` (monotonic) around the *same* work definition for all three strategies (apply the edit mechanism **and** evaluate the resulting complete source). Token counts are local tiktoken **estimates** and are labelled `local_estimate`; the cached-token field is `None` → displayed as `unavailable` rather than guessed. Costs are labelled `estimated_from_local_token_estimate`. The Full-File strategy is now graded by the same evaluator as the other two, so `success=True` can no longer be forced; without a test suite a strategy is reported as `unevaluated`, not as a pass. `latency_speedup_factor` is now `None` (no speedup claim from local processing times) and the report carries `measurement_mode`, `is_measurement=False` and a `notes` string. All three strategies use the same task, the same evaluator, the same repetition policy (`repetitions`, default 1) and the same measurement definitions. In `app.py`: `SUCCESS (100%)` and `100% Guaranteed` are removed; the matrix now shows *Status*, *Evaluated Attempts*, estimated token/cost columns with their source, the local edit time, and the cached-token field as `unavailable`; a prominent warning states that this is a **simulated comparison, not a measured benchmark run**; the relay tab shows the real `outcome_status`, the splice status and the attempt counts, and the macro-benchmark tab states whether the run was a **mock run** (not a live measurement). The real, provider-measured three-way comparison (ITMS vs Aider vs Cline, 300 live attempts) continues to be served read-only by `comparative_viewer.py` and is unchanged. |
| **Test and actual result** | `test_surgical_splicer.py::TestSurgicalEditOrchestrator::test_tri_way_benchmark_metrics` (token/cost deltas still hold **as estimates**; `latency_speedup_factor is None`; every result carries `token_source == "local_estimate"`, `cost_source == "estimated_from_local_token_estimate"`, `cached_input_tokens is None`; ITMS `status == "passed"`, diff `status == "invalid_edit"`); `::test_unevaluated_strategies_are_not_reported_as_success`; `::test_failing_evaluation_is_recorded_as_failure`; `::test_wrong_replacement_is_not_reported_as_success`. Dashboard rendered headless with `streamlit.testing.v1.AppTest`: `app.py` → **0 exceptions, 8 tabs**; the Phase 2 tab after clicking "Run Comparative Edit Benchmark" → **0 exceptions**, and the rendered matrix showed the *estimated* labels, per-strategy status, `1/1 passed`, and `cached tokens: unavailable`. |
| **Status** | **PASS** (dashboard honesty) / **PARTIAL** (this tab remains a *simulation*, not a benchmark) |
| **Remaining limitation** | The Phase 2 tab compares the three edit mechanisms on **candidate code the user supplies**; it therefore measures the *edit mechanism*, not the *model*. It cannot answer "which method is faster/cheaper/more accurate end-to-end" — the live ITMS-vs-Aider-vs-Cline dataset (300 attempts, `comparative_viewer.py`) is the only artefact that does, and it is an **efficiency** result (all three systems passed 100 % of the attempts they actually evaluated). No new paid benchmark was run in this task, so no new end-to-end cost/latency comparison exists. |

---

## 2. Mapping of the user's prioritised requirement list

| # | User's requirement | Report layer | Status | Evidence |
| --- | --- | --- | --- | --- |
| 1 | AST Node Locator | 2.1 | **PASS** | `surgical_splicer.py` rewrite; ambiguity-safe resolution; 30 splicer tests |
| 2 | Boundary slicing & minimal code replacement | 2.2 | **PASS** | stale-target rejection, target-survival check, byte-for-byte preservation test |
| 3 | Correct auto-indentation & preservation of Python scope | 2.3 | **PASS** | semantic candidate classification; nested function stays nested; 2 characterisation tests rewritten |
| 4 | Deterministic validation gate & bounded Tier-2 escalation | 2.4 | **PASS** | `prepare_candidate` / `evaluate_candidate`; statuses `passed`/`failed`/`timeout`/`missing_candidate`/`unevaluated`; bounded retries |
| 5 | Generation → splice → validation/evaluation → retry in the real pipeline | 2.4 | **PASS** | `test_itms_phase2_e2e.py` (6 tests, real fixture, mock provider) |
| 6 | Fair tri-way comparison | 2.5 | **PARTIAL** | same task / evaluator / repetition policy / measurement definition enforced in code; but the Phase 2 tab is a simulation, and no new live tri-way benchmark was authorised |
| 7 | Reliable token / latency / success / failure measurements | 2.5 | **PASS** | hard-coded values removed, monotonic clock, provenance labels, separate status counts; live provider measurements kept in `comparative_viewer.py` |
| 8 | Accurate dashboard and report of benchmark results | 2.5 | **PASS** | `SUCCESS (100%)` / `100% Guaranteed` removed; simulated-vs-measured disclosed; mock runs labelled in the macro tab |

---

## 3. Additional items inspected

### E. Execution sandbox and the security claims

| Field | Value |
| --- | --- |
| **Requirement** | The report describes an "isolated subprocess sandbox" and lists *"Docker Sandbox Hardening: integrate our pluggable DockerSandboxEngine with network air-gapping (--net=none), memory limits (256MB) and read-only filesystems"* as a **roadmap** item. |
| **What was actually found** | `sandbox.py` runs candidate code with `subprocess.run([sys.executable, tmpfile], env=sanitized_env, timeout=...)`. There is **no Docker, no container, no namespace, no seccomp, no cgroup**. The env scrub (`get_sanitized_sandbox_env`) and the wall-clock timeout **are** enforced. The audit in `PHASE2_AUDIT_REPORT.md` §5 independently measured: `outside_read: ALLOWED`, `outside_write: ALLOWED`, `subprocess: allowed`, and a *"child-interpreter socket bypass: NETWORK-BYPASS"*. |
| **Change made** | No new sandbox was built (explicitly out of scope per the task). Instead the limitation is **documented in the code**: `sandbox.py` now defines `ISOLATION_LEVEL = "process_boundary_only"`, `ISOLATION_ENFORCED`, `ISOLATION_NOT_ENFORCED` and `describe_isolation()`; the harness docstring states the same; and the dashboard exposes it in an expander panel on the relay tab ("Execution gate isolation level (read before trusting it with untrusted code)"). The relay tab caption no longer calls the gate an "isolated sandbox" without qualification. |
| **Status** | **BLOCKED** for the Docker hardening itself (explicitly a roadmap item, and the task forbade spending the time budget on a new sandbox); the *claims* are corrected → **PASS** for the documentation requirement. |
| **Remaining limitation** | The execution gate must not be used with untrusted code. The Phase 0/2 audit already shows a network bypass via a child interpreter. |

### F. Historical data integrity

| Field | Value |
| --- | --- |
| **Check** | No historical benchmark run, report or dataset was deleted, renamed, overwritten or modified. |
| **Evidence** | `git status --short` shows changes only in `app.py`, `benchmarks/benchmark/adapters/itms_splice.py` (docstring only), `benchmarks/tests/test_phase1_adapters.py`, `edit_orchestrator.py`, `pytest.ini`, `relay_orchestrator.py`, `sandbox.py`, `surgical_splicer.py`, `test_benchmark_safety.py`, `test_surgical_splicer.py` and the new `test_itms_phase2_e2e.py`. `pilot_runs/`, `results/`, `benchmarks/data/` and `experiments/**/runs/` are untouched. No new paid/live benchmark was executed. `.env` was never read or printed. |

---

## 4. Honest statement of what is *not* established

* The **Phase 2 dashboard tab is a simulation**, not a measured benchmark. It measures the edit mechanism, not a model. It now says so.
* The **only provider-measured** three-way comparison in this repository is the ITMS-vs-Aider-vs-Cline dataset (300 live attempts, `minimax/minimax-m3.1-flash:free`, 20 tasks) served by `comparative_viewer.py`. It is an **efficiency** result; all three systems passed 100 % of the attempts they actually evaluated, so it does **not** establish that ITMS is more accurate.
* The splicer fix changes the behaviour of **future** runs. The historical runs in `pilot_runs/`, `results/` and `experiments/**/runs/` were measured against the unfixed splicer and are preserved as-is; any ratio quoted from them is a ratio **for that dataset**.
* `surgical_splicer.py` cannot *detect* a semantically wrong edit. It guarantees structural integrity (scope, indentation, target survival) and the evaluator provides the semantic verdict.
