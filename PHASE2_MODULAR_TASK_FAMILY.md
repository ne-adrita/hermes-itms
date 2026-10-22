# Phase 2 — A Representative Task Family for ITMS Code-Editing

**Status: DESIGN + OFFLINE VALIDATION COMPLETE. NO LIVE BENCHMARK WAS RUN.**

The existing 20-task HumanEval-derived family cannot exercise the Phase 2
hypothesis. This document explains exactly why, describes a new task family
that satisfies the pre-registered representative-task rule, reports the
offline validation results, and proposes the next experiment together with
the request budget and the approval it needs.

- New dataset root: `benchmarks/data/modular/` (the existing family in
  `benchmarks/data/` is **untouched**).
- Builder: `benchmarks/phase0/build_modular_tasks.py`
- Validator: `benchmarks/phase0/validate_modular_tasks.py`
- Tests: `benchmarks/tests/test_modular_task_family.py`
- Validation report: `benchmarks/data/modular/validation_report.json`

---

## 1. The dataset problem

### 1.1 The symptom

The pre-registered selection rule lives in
`benchmarks/phase2/task_selection.py:DEFAULT_CRITERIA`:

| Criterion | Value |
|---|---|
| `min_file_lines` | 200 |
| `max_target_ratio` | 0.20 |
| `min_function_count` | 5 |
| `min_class_count` | 1 |

Applied to the 20 approved tasks, **0 of 20 qualify**. Every one of them
fails all four criteria at once:

| Task family | `file_lines` | `target_ratio` | `function_count` | `class_count` | Qualifies |
|---|---|---|---|---|---|
| `HE-EDIT-001…020` (existing) | 10 – 28 | 0.923 – 0.990 | 1 | 0 | **0 / 20** |
| `MOD-EDIT-001…020` (new) | 204 – 277 | 0.057 – 0.150 | 15 – 17 | 1 | **20 / 20** |

### 1.2 The exact cause, file by file

The cause is **not** a defect in the rule and **not** a defect in the
mutation engine. It is the *shape of the source file* that the Phase 0
builder composes.

**(a) `benchmarks/phase0/dataset.py:171-182` — `build_source_file()`**

```python
def build_source_file(problem: Dict[str, Any]) -> str:
    prompt = problem["prompt"].rstrip("\n")
    body = problem["canonical_solution"].strip("\n")
    return f"{prompt}\n{body}\n"
```

The reference file is *nothing but* the HumanEval prompt (imports +
signature + docstring) concatenated with the canonical solution body. A
HumanEval solution is a **single short function**, so the resulting module
is 10–28 lines long, defines exactly one function and no class — and the
target function *is* essentially the whole file. `target_ratio` is therefore
`≈ 0.96` **by construction**, not by accident.

**(b) `benchmarks/phase0/build_tasks.py:81-91` — the repair instruction**

The instruction is written for that shape ("the module below contains the
function `X` … return only the repaired function (or the repaired module)").
It was reused verbatim for the new family, so the *instruction is
byte-identical across both families and across all three editing methods*.

**(c) `benchmarks/phase0/mutation_engine.py:260-272` — the mutation target**

`_target_function_node()` locates the target by name anywhere in the AST, so
the engine itself is shape-agnostic. It is **not** the cause. (The one real
shape constraint it imposes is that the target name must be unique in the
file; see §5.2.)

**(d) `benchmarks/phase0/eligibility.py:141-236` — `check_self_containment()`**

The target must not call or reference any other function of the same file,
because the ITMS skeletonizer replaces every non-target body with `...`.
This is a *fairness* rule, not a size rule, and it is applied unchanged to
the new family.

**(e) `benchmarks/phase2/task_selection.py:163-203` — `compute_task_properties()`**

The rule measures the **mutated source file**, which for the old family is
the single-function module. The rule is applied unchanged to the new family
and now selects 20/20.

### 1.3 Why this is fatal for the research question

The Phase 2 thesis is *"ITMS wins because it edits a small region of a large
file"*. On a 10–28 line file whose target is 96 % of the content there is
**no small region and no large file**: the three methods necessarily emit
almost the same number of tokens, and a capable model solves essentially
every task. The historical runs therefore measured the harness, not the
methods — which is exactly what the measured data showed (all three methods
at the same official-evaluator pass rate).

---

## 2. What the new family must achieve

1. **Pre-registered rule** — `file_lines ≥ 200`, `target_ratio ≤ 0.20`,
   `function_count ≥ 5`, `class_count ≥ 1`. The rule is **not** changed.
2. **A meaningful, localised edit** — the defect is a single, realistic
   logic error (an operator, a comparison boundary, a constant), confined to
   one line of one small function.
3. **Unambiguous expected behaviour** — the spec is the target function's
   own docstring; the official check asserts **exact** values.
4. **A discriminating check** — a partially correct repair must fail.
5. **Reuse** — the existing Phase 0 mutation engine, the eligibility gate,
   the **official evaluator** and the task/manifest schema are used
   unchanged.
6. **Separation** — the existing dataset and every historical result stay
   byte-for-byte untouched.

---

## 3. The new family

### 3.1 Five authored modules, twenty targets

| Module | Domain | Top-level functions | Classes | Lines | Targets |
|---|---|---|---|---|---|
| `text_metrics.py` | text measurement | 8 | `TextProfile` | 276 | `word_frequencies`, `longest_word`, `vowel_ratio`, `split_on_whitespace_trimmed` |
| `interval_math.py` | time-interval arithmetic | 8 | `Schedule` | 239 | `overlap_length`, `merge_intervals`, `total_covered_length`, `gap_between` |
| `inventory_math.py` | stock planning | 8 | `InventoryItem` | 217 | `reorder_point`, `economic_order_quantity`, `stock_valuation`, `turnover_ratio` |
| `grid_geometry.py` | 2-D geometry | 8 | `Rectangle` | 232 | `manhattan_distance`, `rectangle_area`, `point_in_rectangle`, `bounding_box` |
| `rate_limiting.py` | token-bucket arithmetic | 7 | `TokenBucket` | 204 | `tokens_to_refill`, `is_request_allowed`, `retry_after_seconds`, `window_utilisation` |

The modules were written for this project. They are **not** HumanEval tasks
and no HumanEval score can be derived from them.

### 3.2 Design rules that make the family fair

* **The target is self-contained.** It uses only builtins and stdlib modules
  imported by the file, never another function of the same file. This is
  required by the ITMS skeletonizer (which strips every other body to
  `...`) and it means the task is answerable by *all three* methods.
* **The surrounding code is real, not filler.** The other functions and the
  class are genuine, working code. The target is a genuinely small part of
  a genuinely large module — which is the geometry the thesis needs.
* **The spec is the docstring.** The model receives the target's docstring
  verbatim. The docstring fully specifies the behaviour that the check
  asserts, including the edge cases.
* **The example does not leak.** The worked example in the docstring and the
  values asserted by the official check are **disjoint** (validated
  automatically, §4.3), so a model cannot pass the check by hard-coding the
  example.
* **Identical instruction.** The repair instruction is byte-identical to the
  one the HumanEval family uses, and identical across the three methods.

### 3.3 The defect

The defect is produced by the **existing** mutation engine
(`benchmarks/phase0/mutation_engine.py`), so it is deterministic, AST-level
and reproducible from `(source, target, rule, parameters)`. Only mutations
that make the official check **fail** (and do not hang it) are kept.

| Mutation rule | Tasks |
|---|---|
| `comparison_operator_swap` | 5 |
| `off_by_one_boundary` | 4 |
| `incorrect_constant` | 4 |
| `arithmetic_operator_swap` | 4 |
| `boolean_operator_swap` | 3 |

Every task's defect is confined to **exactly one line** (validated for all
20). The exact line and the before/after text are recorded per task in
`benchmarks/data/modular/modular_edit_tasks.json` under
`mutation.expected_change`, and the affected line numbers are
39, 44, 46, 53, 58, 64, 65, 66, 71, 79, 81, 83, 86, 97, 100, 101, 102,
115, 128, 149.

### 3.4 Reuse map

| Component | Reused as-is? |
|---|---|
| `benchmarks/phase0/mutation_engine.py` | **Yes** — unchanged |
| `benchmarks/phase0/eligibility.py` | **Yes** — unchanged |
| `benchmarks/phase0/runner.py` (the **official evaluator**) | **Yes** — unchanged |
| `benchmarks/phase0/validate_tasks.py` (checks 13: three-method solvability) | **Yes** — imported by the new validator |
| `benchmarks/phase2/task_selection.py` (the **pre-registered rule**) | **Yes** — unchanged, applied to the new family |
| `benchmarks/benchmark/tasks.py` (`TaskRepository`) | **Yes** — the new root has the same layout, so the same loader reads it |
| `benchmarks/benchmark/adapters/*` (the three methods) | **Yes** — unchanged |
| `benchmarks/phase0/build_tasks.py` | Reused by *pattern*, not by import: it is HumanEval-specific |
| `benchmarks/phase0/validate_tasks.py::_body_only` | **One bug fixed** — see §6.1 |

---

## 4. Validation procedure

`benchmarks/phase0/validate_modular_tasks.py` re-derives every fact from the
files on disk. It makes **no network request** and **no model call**, and it
writes only into the report file it is asked for.

### 4.1 Per-task checks (18, all run for all 20 tasks → 360 executions)

| # | Check | What it proves |
|---|---|---|
| 1 | `mutation_reproduces_recorded_source` | the defect is reproducible byte-for-byte from the recorded parameters |
| 2 | `original_and_mutated_parse` | both files are valid Python |
| 3 | `docstring_preserved_byte_for_byte` | the spec was not tampered with; the problem prompt *is* the docstring |
| 4 | `reference_module_passes_official_check` | the official evaluator accepts the correct module |
| 5 | `defective_module_fails_official_check` | the official evaluator rejects the defect (and does not hang) |
| 6 | `official_check_has_several_assertions` | ≥ 3 assertions, so a partial repair fails |
| 7 | `defect_is_confined_to_one_line` | the edit is genuinely localised |
| 8 | `worked_example_does_not_leak_into_the_official_check` | the task cannot be solved by copying the example |
| 9 | `worked_examples_in_the_module_are_correct` | every doctest in the module is right |
| 10 | `reference_module_not_revealed` | the reference module is not in the test or in the instruction |
| 11 | `task_is_self_contained` | the target can be repaired in isolation (eligibility gate) |
| 12 | `target_name_is_unique_in_the_module` | the mutation engine and the AST splicer cannot be ambiguous |
| 13 | `module_text_is_not_credential_shaped` | the saved candidate stays byte-identical to the evaluated candidate |
| 14 | `official_check_is_not_credential_shaped` | the official test is stored verbatim |
| 15 | `compatible_with_full_file_regeneration` | the whole reference module passes the check |
| 16 | `compatible_with_search_replace_patch` | the **real** `TextDiffPatcher` can apply the gold patch and the result passes |
| 17 | `compatible_with_itms_ast_splice` | the **real** `ASTSurgicalSplicer` can splice the gold body and the result passes |
| 18 | `task_present_in_model_facing_payload` | the task is in the model-facing payload |

Check 13 deserves a note. The artefact store
(`benchmarks/benchmark/artifacts.py:232-243`) redacts anything that *looks*
like a credential before it writes a candidate, and records the digest of the
**evaluated** bytes separately. If the module text tripped that heuristic,
the stored candidate would no longer be byte-identical to the bytes the
official evaluator judged. The first draft of the family used a local
variable named `token`, which the heuristic reads as `token = <secret>`;
the variable was renamed (`token` → `piece`) and the check now guards
against a recurrence. This is a correctness requirement, not a security
nicety.

### 4.2 Family-level checks

* the **pre-registered rule** is applied to the whole family and must select
  all 20 tasks (`validate_selection()`);
* the **manifest** checksums must verify (`--verify-manifest`);
* the builder's **build log** is cross-checked (targets inspected, eligible,
  gold passed, viable mutations, rejected, not selected);
* the **distribution** is reported: modules, distinct targets, mutation-rule
  usage.

### 4.3 The "no copy-shortcut" rule, precisely

The expected-output lines of the target's doctest are parsed with
`ast.literal_eval`; the string and float scalars inside them are collected.
The string and float literals asserted by the official check are collected
from its `assert` statements. The two sets must be **disjoint**. Integers
are excluded on purpose — small integers are not a usable shortcut, and the
check has several assertions with different inputs, so hard-coding cannot
work.

### 4.4 Reproducibility

* The build is deterministic: the same authored inputs always produce
  byte-identical artefacts (the manifest pins every file by SHA-256).
* The selection policy is fixed in version-controlled code *before* any
  model run and is outcome-independent: a round-robin over the mutation
  rules in their fixed order, taking the first not-yet-used
  `(module, target)` pair that has a viable mutation under that rule. No
  model result, pass rate or timing enters the decision.
* `python benchmarks/phase0/build_modular_tasks.py --tasks 20` rebuilds the
  family; `python benchmarks/phase0/validate_modular_tasks.py
  --verify-manifest` re-verifies it.

---

## 5. Validation results (measured)

```
$ python benchmarks/phase0/validate_modular_tasks.py --verify-manifest
Phase 0 modular task family (offline validation)
tasks            : 20
passed           : 20
failed           : 0
modules          : 5
distinct targets : 20
mutation rules   : {'comparison_operator_swap': 5, 'off_by_one_boundary': 4,
                    'incorrect_constant': 4, 'arithmetic_operator_swap': 4,
                    'boolean_operator_swap': 3}
pre-registered rule: 20/20 qualify, criteria={'min_file_lines': 200,
                    'max_target_ratio': 0.2, 'min_function_count': 5,
                    'min_class_count': 1}
manifest: OK (85 files)
```

### 5.1 Task-set properties

| Quantity | min | median | max |
|---|---|---|---|
| `file_lines` | 204 | 232 | 277 |
| `target_ratio` | 0.057 | 0.084 | 0.150 |
| `function_count` | 15 | 16 | 17 |
| `class_count` | 1 | 1 | 1 |

### 5.2 Diversity

* 5 distinct modules, 4 tasks each (4 different target functions per
  module) → 20 distinct targets.
* All 5 mutation rules are represented, with a 5/4/4/4/3 split.
* The defect is byte-local in 20/20 tasks.
* The official check has ≥ 3 assertions in 20/20 tasks.

### 5.3 Pipeline smoke test (offline, mock engine, no provider call)

```
$ python benchmarks/benchmark_runner.py --mode mock \
      --data-dir benchmarks/data/modular --reps 1 --output-dir /tmp/modular_mock_smoke
recorded attempts : 60
mode              : mock
WARNING          : MOCK RUN — pipeline test only, not a measurement of model quality.
```

60 attempts (20 tasks × 3 methods), every attempt produced a candidate, was
parsed, applied and evaluated by the official evaluator. The mock never
produces a correct repair by design, so all 60 are `repair_failed` /
`test_failed` — the expected outcome of a pipeline test.

### 5.4 Prompt sizes (fairness datapoint, measured on `MOD-EDIT-001`)

| Method | Prompt lines | Prompt characters |
|---|---|---|
| `full_file_regeneration` | 266 | 8 054 |
| `search_replace_patch` | 272 | 8 114 |
| `itms_ast_splice` | 58 | 2 312 |

The ITMS prompt is smaller because the ITMS adapter builds a **pruned
skeleton** (`prune_unreachable=True`): it shows the target node and the
module header, not the whole file. This is ITMS's real, existing behaviour
— but it means the three methods do **not** receive the same amount of input
context. §7 treats this as a first-class risk.

---

## 6. Deviations, fixes and limitations

### 6.1 One shared-code bug fix (`validate_tasks.py::_body_only`)

The helper that extracts a function body for the ITMS splice check assumed
a **single-line** `def` header. A function whose header spans several lines
produced an invalid body and the check failed with
`SyntaxError in replacement code: unmatched ')'`. Five of the twenty new
tasks hit it.

`_body_only` now locates the body with the AST (the first statement of the
function) instead of by scanning for the first indented line.

* This is **not** a weakening: it changes no check, skips no check and does
  not touch the official evaluator (`benchmarks/phase0/runner.py`).
* It is **provably neutral for the existing family**: the HumanEval
  validation report was captured before and after the change and is
  identical in every task, every check name and every pass/fail flag
  (20/20 passed before, 20/20 after, zero changed flags).
* A regression guard for the multi-line case is in
  `benchmarks/tests/test_modular_task_family.py::test_the_body_only_splice_handles_a_multi_line_signature`.

### 6.2 Two additive changes to the existing tools

`benchmarks/benchmark_runner.py` gained `--data-dir`. The default is
`None`, which keeps the previous behaviour exactly (the HumanEval family in
`benchmarks/data`). No other runner behaviour changed.

`benchmarks/phase2/task_selection.py` gained `--data-dir` and a `sys.path`
bootstrap, so the pre-registered rule can be applied to either family from
the command line and the script also runs when it is invoked as a file
(it previously only worked as `python -m benchmarks.phase2.task_selection`).
**The rule itself is unchanged.**

### 6.3 Known limitations of the new family

1. **The modules are authored, not mined.** They are clean, idiomatic
   libraries; they do not contain the messy real-world code (dead branches,
   mixed styles, vendored imports) that a production corpus would have. The
   file-size and target-ratio criteria are satisfied, but the *difficulty*
   is not that of an arbitrary real repository.
2. **Five modules is a small corpus.** 20 tasks over 5 modules is enough to
   satisfy the rule and to run the experiment, but a wider corpus would give
   a stronger generalisation argument. The builder already supports more
   modules: dropping another `.py` file into
   `benchmarks/data/modular/source/` and a matching `checks/` file is
   enough.
3. **The target is a top-level function, not a method.** The pre-registered
   rule's `compute_task_properties()` measures a module-level target, and
   the eligibility gate requires a module-level unique definition, so a
   method target would require changing the rule. The rule was not changed.
4. **The input-context asymmetry** described in §5.4 is *not* solved by
   this family; it is measured by it, and §7 proposes how to control it.
5. **No behavioural guarantee about model performance.** This work produced
   **no** benchmark result. Nothing here says which method will win.

---

## 7. Recommendation for the next experiment

> **Update (later session).** A **reduced 100-request** live run on this
> family has since been executed: `results/phase2_modular_100run_2026-10-22/`
> (100 planned, 100 requested, 100 evaluated, 100 passed the official
> evaluator, 0 provider errors, 0 truncations, 0 retries). The full report is
> `results/phase2_modular_100run_2026-10-22/PHASE2_REDUCED_100_REPORT.md`.
> It used an explicit reduced schedule (34 / 33 / 33 across the methods, five
> attempts per task) delivered through a new additive `--schedule-file`
> option on the runner, because the default cross product cannot express a
> 100-cell plan. The 300-attempt design recommended below has **not** been
> run. Its measured finding is that on this task set the three methods are
> indistinguishable in correctness (100 % each) and that **search/replace is
> more output-efficient and faster than ITMS**, so the Phase 2 thesis is
> *not* supported by this measurement. The fourth (unpruned ITMS) arm
> remains the open question, because the ITMS prompt is 3.7× smaller than
> the baselines' (2 378 vs ~8 700 bytes) owing to the adapter's context
> pruning.

### 7.1 What to run

A live 3-method run on the **new** family, with the same protocol as the
completed 300-attempt run:

* 20 tasks × 3 methods × 5 repetitions = **300 attempts**
* provider `openrouter`, pinned model `minimax/minimax-m3.1-flash:free`
* `--max-attempts 300` (hard cap), `--max-retries 0`, `--max-spend-usd 1.00`,
  `--require-free-model`
* `--max-output-tokens 4096`, `--temperature 0.2`
* `--seed 20261023 --seed-base 990000`, `--evaluation-timeout 10`,
  `--request-timeout 60`
* `--data-dir benchmarks/data/modular`
* the official evaluator, the same budget gate and the same analysis tool
  (`benchmarks/analyze_run.py`)

### 7.2 Request budget and the approval needed

| Item | Value |
|---|---|
| **Planned attempts** | **300** (20 × 3 × 5) |
| **Provider requests** | **300** — one per attempt, `--max-retries 0`, so a retry cannot silently add a request; the hard cap is 300 |
| Model route | `minimax/minimax-m3.1-flash:free` — a **free** route, so the monetary exposure is bounded by the attempt cap, not by the spend cap |
| Reported-spend cap | `$1.00` (inert, because the provider reports no cost; the attempt cap is the binding guard) |
| **Approval needed** | **explicit written approval of the 300-request budget** before the run starts, plus a **network-stability precondition** (see below) |
| **Not requested** | no continuation run, no extra reps, no other model, no paid route |

**Two preconditions before the run is launched:**

1. **Network stability.** In the completed 300-attempt run, connectivity to
   OpenRouter failed at 01:01 UTC and stayed down; 216 of 300 attempts were
   recorded as `provider_error` and only 84 ever reached the evaluator. A
   second run under the same conditions would be equally uninformative.
   *Recommendation:* run a short connectivity probe (for example 9 live
   requests, the size of the existing pilot) and **only start the 300-attempt
   run if all 9 succeed**; otherwise wait for connectivity to recover. This
   costs at most 9 additional requests and should be part of the approved
   budget, i.e. **309 requests in total**.
2. **Input-context control (recommended, optional).** Because the ITMS
   adapter prunes the context, the three methods do not see the same input.
   To make the efficiency claim airtight, add a **fourth arm**,
   `itms_ast_splice_unpruned` (`prune_unreachable=False`), which shows ITMS
   the whole file. That is 20 tasks × 5 reps = **100 extra attempts**
   (→ 400 attempts, or 409 with the probe). This needs a small, additive
   change to the adapter registry and the runner; it is **not** implemented
   yet, and the 300-attempt run does not depend on it.

### 7.3 What the next run may and may not conclude

**May conclude** (if all 300 attempts are evaluated):

* the official-evaluator pass rate of the three methods **on a task set that
  satisfies the pre-registered representative-task criteria**;
* the measured output-token and latency differences **on that geometry**;
* whether the ITMS advantage that the thesis predicts actually appears when
  the target really is a small region of a large file.

**May not conclude:**

* anything about HumanEval (these are not HumanEval tasks);
* anything about accuracy *in general* — 20 authored tasks over 5 modules
  is a small corpus, and the modules are clean by construction;
* a "secure sandbox" claim (the evaluator is best-effort isolation);
* any cost figure, unless the provider starts reporting one;
* that ITMS is better, if the result is flat. On the old family the honest
  outcome was "indistinguishable"; the new family is what would let that
  question be asked properly.

### 7.4 The order of work

1. **Approve** the 300 (+9 probe) request budget. ⬅ *waiting for this*
2. Run the 9-request connectivity probe.
3. Launch the 300-attempt run into a **new** directory, e.g.
   `results/phase2_modular_300run_<UTC>/`.
4. Run `benchmarks/analyze_run.py` and publish the per-method comparison,
   the paired task-level bootstrap intervals and the figures.
5. Decide afterwards whether the fourth (unpruned ITMS) arm is wanted.

---

## 8. Files

### New — the dataset family

```
benchmarks/data/modular/source/text_metrics.py        authored module
benchmarks/data/modular/source/interval_math.py       authored module
benchmarks/data/modular/source/inventory_math.py      authored module
benchmarks/data/modular/source/grid_geometry.py       authored module
benchmarks/data/modular/source/rate_limiting.py       authored module
benchmarks/data/modular/checks/<module>.py            official checks (5 files)
benchmarks/data/modular/tasks/MOD-EDIT-001…020/       generated artefacts
benchmarks/data/modular/model_facing_tasks.json       model-facing payload
benchmarks/data/modular/modular_edit_tasks.json       full task records
benchmarks/data/modular/gold_solutions.json            reference solutions
benchmarks/data/modular/mutation_catalogue.json        the mutation catalogue
benchmarks/data/modular/manifest.json                  checksum manifest
benchmarks/data/modular/build_log.json                 build log
benchmarks/data/modular/validation_report.json         offline validation report
```

### New — code and tests

```
benchmarks/phase0/build_modular_tasks.py
benchmarks/phase0/validate_modular_tasks.py
benchmarks/tests/test_modular_task_family.py
PHASE2_MODULAR_TASK_FAMILY.md                          this document
```

### Modified

```
benchmarks/phase0/validate_tasks.py      one bug fix in _body_only (§6.1)
benchmarks/benchmark_runner.py            additive --data-dir option (§6.2)
benchmarks/phase2/task_selection.py      additive --data-dir option and a
                                         sys.path bootstrap so the script
                                         runs as a script as well as a module
                                         (the preregistered rule itself is
                                         unchanged)
```

### Explicitly NOT modified

```
benchmarks/data/tasks/                    the existing 20 tasks
benchmarks/data/manifest.json             the existing manifest
benchmarks/data/validation_report.json    the existing validation report
benchmarks/phase0/runner.py               the official evaluator
benchmarks/phase0/mutation_engine.py      the mutation engine
benchmarks/phase0/eligibility.py          the eligibility gate
benchmarks/phase2/task_selection.py       the pre-registered rule
results/, pilot_runs/                     every historical result
```

---

## 9. Reproduction

```bash
# 1. rebuild the family (offline, deterministic)
python benchmarks/phase0/build_modular_tasks.py --tasks 20

# 2. validate it (offline, no provider call)
python benchmarks/phase0/validate_modular_tasks.py --verify-manifest \
    --json benchmarks/data/modular/validation_report.json

# 3. the offline tests
python -m pytest benchmarks/tests/test_modular_task_family.py -q

# 4. the pre-registered rule applied to both families
python benchmarks/phase2/task_selection.py                          # the old family: 0/20
python benchmarks/phase2/task_selection.py --data-dir benchmarks/data/modular
                                                                  # the new family: 20/20
```

### 9.1 Test results (all local, no provider call)

| Command | Exit code | Result |
|---|---|---|
| `python benchmarks/phase0/build_modular_tasks.py --tasks 20` | 0 | 20 accepted, 0 rejected, 0 not selected; rules 5/4/4/4/3 |
| `python benchmarks/phase0/validate_modular_tasks.py --verify-manifest` | 0 | **20/20 tasks passed**, 360 check executions, 20/20 satisfy the pre-registered rule, manifest OK (85 files) |
| `python benchmarks/phase0/validate_tasks.py --verify-manifest` | 0 | existing family still **20/20**; output byte-identical before and after the `_body_only` fix |
| `python -m pytest benchmarks/tests/test_modular_task_family.py -q` | 0 | **18 passed** |
| `python -m pytest -q` (whole project) | 0 | **528 passed**, 2 warnings, 23 subtests (was 510 before this work; +18 new) |
| `python benchmarks/benchmark_runner.py --mode mock --data-dir benchmarks/data/modular --reps 1` | 0 | 60 attempts, 60 candidates produced, 60 evaluated — pipeline smoke test only |
| `git diff --check` | 0 | no whitespace error |

### 9.2 What was verified unchanged

| Artefact | Verification |
|---|---|
| `benchmarks/data/tasks/` (the existing 20 tasks) | `git diff` empty — not one byte changed |
| `benchmarks/data/manifest.json`, `benchmarks/data/validation_report.json` | `git diff` empty |
| `benchmarks/phase0/runner.py` (the official evaluator) | not modified |
| `benchmarks/phase0/mutation_engine.py`, `eligibility.py` | not modified |
| `benchmarks/phase2/task_selection.py` (the pre-registered rule) | criteria values unchanged; only `--data-dir` and a `sys.path` bootstrap were added |
| `results/phase2_pilot_9req_2026-10-22/` | 0 files modified; newest mtime 2026-10-22T06:32:17 |
| `results/analysis_historical_120run_2026-10-22/` | 0 files modified; newest mtime 2026-10-22T06:29:45 |
| `results/phase2_full_300run_2026-10-22/` | 0 files modified by this work |
| `pilot_runs/` | 0 files modified; newest mtime 2026-10-21T20:34:22 |
| `.env` | not modified (mtime 2026-10-20T01:15:53) |
| Secrets | 0 credential-shaped strings in the 97 new dataset files; 0 in the diff |
