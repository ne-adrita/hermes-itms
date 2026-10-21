# Phase 0 Task Validation Report — ITMS Editing Benchmark

**Scope:** Phase 0 only — offline sourcing, mutation and validation of
synthetic single-file Python function-repair tasks derived from HumanEval.

**This is not a HumanEval benchmark result.** The tasks are *synthetic*
repair tasks derived from HumanEval problems; no model was run against them
in Phase 0 and no HumanEval pass rate is reported.

- Validation report JSON: `benchmarks/data/validation_report.json`
- Task data: `benchmarks/data/humaneval_edit_tasks.json`
- Gold solutions (never sent to a model): `benchmarks/data/gold_solutions.json`
- Model-facing payload: `benchmarks/data/model_facing_tasks.json`
- Mutation catalogue: `benchmarks/data/mutation_catalogue.json`
- Manifest: `benchmarks/data/manifest.json`
- Build log: `benchmarks/data/build_log.json`

## 1. Source provenance and revision

| Field | Value |
|---|---|
| Repository | `https://github.com/openai/human-eval` |
| Pinned revision (git commit SHA) | `463c980b59e818ace59f6f9803cd92c749ceae61` |
| Revision verification | GitHub API /repos/openai/human-eval/commits?path=data/HumanEval.jsonl.gz |
| Dataset path in repository | `data/HumanEval.jsonl.gz` |
| sha256 of `HumanEval.jsonl.gz` | `b796127e635a67f93fb35c04f4cb03cf06f38c8072ee7cee8833d7bee06979ef` |
| sha256 of decompressed `HumanEval.jsonl` | `1d49078ba3e2b196b9344535bef34a43021f038fad9561d6ee7c53450609a6a2` |
| Problems in the pinned dataset | 164 |
| License | MIT (`LICENSE`, sha256 `bcba3de214851cce46ed5af42d6698044616eeace887c3231bc7a20474ab639e`) |
| Repository HEAD observed while preparing Phase 0 | `b9453e8f1865df0a0057a1383767caa400acfbcf` |
| Vendored subset | `benchmarks/data/source/humaneval_subset.jsonl` (sha256 `c1a6c29173f8050f9124324fc1dfa5fd503869324a271a9bfe2aff21158c7bb9`) |
| Vendored licence text | `benchmarks/data/source/humaneval_LICENSE.txt` (sha256 `bcba3de214851cce46ed5af42d6698044616eeace887c3231bc7a20474ab639e`) |

**Verified facts**

- The pinned revision is the *only* commit in the upstream repository that
  touches `data/HumanEval.jsonl.gz`; it was confirmed through the
  GitHub commits API, so the dataset content is immutable and independently
  verifiable by anyone.
- The compressed and decompressed dataset files were downloaded and their
  SHA-256 digests were checked against the values above before any task was
  derived from them. A digest mismatch aborts the build.

**Unresolved / noted**

- The upstream dataset file data/HumanEval.jsonl.gz has exactly one commit in the repository history, which is the pinned revision above.
- The upstream repository has been marked deprecated and unmaintained by OpenAI (see repository README at repo_head_observed).
- Only the subset of problems used by the accepted tasks is vendored here; the full dataset is not redistributed.

## 2. Mutation catalogue

Catalogue version `1.0.0`, deterministic: yes.

Selection policy: For a given (source, target function) the candidate mutations are enumerated in a fixed order: rules in the order listed below, and within a rule, occurrences in ascending source order. A mutation is accepted for a task only if it keeps the module syntactically valid and makes the official HumanEval check() fail.

| Rule | Category | Parameters | Description |
|---|---|---|---|
| `comparison_operator_swap` | logic | `from_op`, `to_op`, `occurrence` | Replace the comparison operator of a comparison expression with a different, logically adjacent operator (e.g. '>' becomes '>='). |
| `off_by_one_boundary` | boundary | `delta`, `occurrence` | Shift by +/- 1 an integer literal that sits in a boundary position (a comparison operand, a slice bound, a subscript index, a range() argument, or an operand of '+'/'-'), producing an off-by-one error. |
| `incorrect_constant` | constant | `new_value`, `occurrence` | Replace a numeric literal with a different numeric literal, changing a hard-coded constant. |
| `arithmetic_operator_swap` | arithmetic | `from_op`, `to_op`, `occurrence` | Replace the arithmetic operator of a binary expression with a different operator (e.g. '+' becomes '-'). |
| `boolean_operator_swap` | logic | `from_op`, `to_op`, `occurrence` | Replace a boolean connective ('and'/'or') with its counterpart, changing the short-circuit logic of a condition. |

Rule usage across the accepted task set:

| Rule | Tasks |
|---|---|
| `arithmetic_operator_swap` | 4 |
| `boolean_operator_swap` | 4 |
| `comparison_operator_swap` | 4 |
| `incorrect_constant` | 4 |
| `off_by_one_boundary` | 4 |

## 3. Candidate counts

| Stage | Count |
|---|---|
| HumanEval problems inspected | 164 |
| Passed the self-containment eligibility rules | 149 |
| Eligible **and** with at least one catalogue mutation that fails the official test | 136 |
| Accepted as benchmark tasks | 20 |
| Rejected | 28 |
| Eligible but not selected (rule-diversity round robin) | 116 |

**Accepted: 20. Rejected at the problem level: 28.**

## 4. Accepted tasks

| Task ID | HumanEval problem | Target function | Mutation rule | Mutation parameters | All checks passed |
|---|---|---|---|---|---|
| `HE-EDIT-001` | `HumanEval/0` | `has_close_elements` | `comparison_operator_swap` | from_op='!=', to_op='==', occurrence=0, op_index=0 | yes |
| `HE-EDIT-002` | `HumanEval/1` | `separate_paren_groups` | `off_by_one_boundary` | delta=1, occurrence=0 | yes |
| `HE-EDIT-003` | `HumanEval/2` | `truncate_number` | `incorrect_constant` | new_value=2.0, occurrence=0 | yes |
| `HE-EDIT-004` | `HumanEval/4` | `mean_absolute_deviation` | `arithmetic_operator_swap` | from_op='/', to_op='*', occurrence=0 | yes |
| `HE-EDIT-005` | `HumanEval/36` | `fizz_buzz` | `boolean_operator_swap` | from_op='or', to_op='and', occurrence=0 | yes |
| `HE-EDIT-006` | `HumanEval/3` | `below_zero` | `comparison_operator_swap` | from_op='<', to_op='<=', occurrence=0, op_index=0 | yes |
| `HE-EDIT-007` | `HumanEval/14` | `all_prefixes` | `off_by_one_boundary` | delta=1, occurrence=0 | yes |
| `HE-EDIT-008` | `HumanEval/5` | `intersperse` | `incorrect_constant` | new_value=2, occurrence=0 | yes |
| `HE-EDIT-009` | `HumanEval/13` | `greatest_common_divisor` | `arithmetic_operator_swap` | from_op='%', to_op='//', occurrence=0 | yes |
| `HE-EDIT-010` | `HumanEval/57` | `monotonic` | `boolean_operator_swap` | from_op='or', to_op='and', occurrence=0 | yes |
| `HE-EDIT-011` | `HumanEval/7` | `filter_by_substring` | `comparison_operator_swap` | from_op='in', to_op='not in', occurrence=0, op_index=0 | yes |
| `HE-EDIT-012` | `HumanEval/15` | `string_sequence` | `off_by_one_boundary` | delta=1, occurrence=0 | yes |
| `HE-EDIT-013` | `HumanEval/8` | `sum_product` | `incorrect_constant` | new_value=1, occurrence=0 | yes |
| `HE-EDIT-014` | `HumanEval/18` | `how_many_times` | `arithmetic_operator_swap` | from_op='+', to_op='-', occurrence=0 | yes |
| `HE-EDIT-015` | `HumanEval/64` | `vowels_count` | `boolean_operator_swap` | from_op='or', to_op='and', occurrence=0 | yes |
| `HE-EDIT-016` | `HumanEval/9` | `rolling_max` | `comparison_operator_swap` | from_op='is', to_op='is not', occurrence=0, op_index=0 | yes |
| `HE-EDIT-017` | `HumanEval/24` | `largest_divisor` | `off_by_one_boundary` | delta=1, occurrence=0 | yes |
| `HE-EDIT-018` | `HumanEval/17` | `parse_music` | `incorrect_constant` | new_value=5, occurrence=0 | yes |
| `HE-EDIT-019` | `HumanEval/20` | `find_closest_elements` | `arithmetic_operator_swap` | from_op='-', to_op='+', occurrence=1 | yes |
| `HE-EDIT-020` | `HumanEval/71` | `triangle_area` | `boolean_operator_swap` | from_op='or', to_op='and', occurrence=0 | yes |

## 5. Rejected candidates, grouped by reason

### calls_function_defined_in_same_file (15)

| HumanEval problem | Detail |
|---|---|
| `HumanEval/10` | calls_function_defined_in_same_file: ['is_palindrome']; references_name_defined_in_same_file: ['is_palindrome'] |
| `HumanEval/107` | calls_function_defined_in_same_file: ['is_palindrome'] |
| `HumanEval/108` | calls_function_defined_in_same_file: ['digits_sum'] |
| `HumanEval/11` | calls_function_defined_in_same_file: ['xor'] |
| `HumanEval/119` | calls_function_defined_in_same_file: ['check'] |
| `HumanEval/127` | calls_function_defined_in_same_file: ['is_prime'] |
| `HumanEval/32` | calls_function_defined_in_same_file: ['poly']; references_name_defined_in_same_file: ['poly'] |
| `HumanEval/38` | calls_function_defined_in_same_file: ['encode_cyclic']; references_name_defined_in_same_file: ['encode_cyclic'] |
| `HumanEval/39` | calls_function_defined_in_same_file: ['is_prime'] |
| `HumanEval/55` | calls_function_defined_in_same_file: ['fib'] |
| `HumanEval/59` | calls_function_defined_in_same_file: ['is_prime'] |
| `HumanEval/6` | calls_function_defined_in_same_file: ['parse_paren_group'] |
| `HumanEval/63` | calls_function_defined_in_same_file: ['fibfib'] |
| `HumanEval/75` | calls_function_defined_in_same_file: ['is_prime'] |
| `HumanEval/94` | calls_function_defined_in_same_file: ['isPrime'] |

### no_mutation_in_catalogue_made_the_official_test_fail (13)

| HumanEval problem | Detail |
|---|---|
| `HumanEval/116` | no_mutation_in_catalogue_made_the_official_test_fail |
| `HumanEval/137` | no_mutation_in_catalogue_made_the_official_test_fail |
| `HumanEval/16` | no_mutation_in_catalogue_made_the_official_test_fail |
| `HumanEval/162` | no_mutation_in_catalogue_made_the_official_test_fail |
| `HumanEval/22` | no_mutation_in_catalogue_made_the_official_test_fail |
| `HumanEval/23` | no_mutation_in_catalogue_made_the_official_test_fail |
| `HumanEval/27` | no_mutation_in_catalogue_made_the_official_test_fail |
| `HumanEval/28` | no_mutation_in_catalogue_made_the_official_test_fail |
| `HumanEval/29` | no_mutation_in_catalogue_made_the_official_test_fail |
| `HumanEval/34` | no_mutation_in_catalogue_made_the_official_test_fail |
| `HumanEval/35` | no_mutation_in_catalogue_made_the_official_test_fail |
| `HumanEval/86` | no_mutation_in_catalogue_made_the_official_test_fail |
| `HumanEval/92` | no_mutation_in_catalogue_made_the_official_test_fail |

Additionally, 116 eligible problems with valid failing mutations were *not* selected, because the selection round robin takes the first problem per rule to maximise rule diversity. They remain available as alternates (see `benchmarks/data/build_log.json`, `not_selected_candidates`).

## 6. Offline validation results

Every check below was executed offline, in a throw-away temporary
directory, with `PYTHONPATH` scrubbed, user site-packages disabled, no
convenience imports injected, and sockets blocked at the interpreter
level. No model or inference API was contacted.

| Check | Tasks passing | Tasks failing |
|---|---|---|
| `source_matches_pinned_dataset` | 20 | 0 |
| `mutation_reproduces_recorded_source` | 20 | 0 |
| `docstring_and_prompt_preserved_byte_for_byte` | 20 | 0 |
| `original_and_mutated_parse` | 20 | 0 |
| `gold_solution_passes_official_test` | 20 | 0 |
| `mutated_solution_fails_official_test` | 20 | 0 |
| `reference_solution_not_revealed` | 20 | 0 |
| `task_is_self_contained` | 20 | 0 |
| `compatible_with_full_file_regeneration` | 20 | 0 |
| `compatible_with_search_replace_patch` | 20 | 0 |
| `compatible_with_itms_ast_splice` | 20 | 0 |
| `model_facing_payload_excludes_gold_and_mutation_metadata` | 20 | 0 |

### Gold-test and mutation-failure outcomes

| Task ID | Gold (reference solution) | Mutated (defective) | Mutation failure mode |
|---|---|---|---|
| `HE-EDIT-001` | PASS | FAIL (as required) | AssertionError |
| `HE-EDIT-002` | PASS | FAIL (as required) | AssertionError |
| `HE-EDIT-003` | PASS | FAIL (as required) | AssertionError |
| `HE-EDIT-004` | PASS | FAIL (as required) | AssertionError |
| `HE-EDIT-005` | PASS | FAIL (as required) | AssertionError |
| `HE-EDIT-006` | PASS | FAIL (as required) | AssertionError |
| `HE-EDIT-007` | PASS | FAIL (as required) | AssertionError |
| `HE-EDIT-008` | PASS | FAIL (as required) | AssertionError |
| `HE-EDIT-009` | PASS | FAIL (as required) | AssertionError |
| `HE-EDIT-010` | PASS | FAIL (as required) | AssertionError |
| `HE-EDIT-011` | PASS | FAIL (as required) | AssertionError |
| `HE-EDIT-012` | PASS | FAIL (as required) | AssertionError |
| `HE-EDIT-013` | PASS | FAIL (as required) | AssertionError |
| `HE-EDIT-014` | PASS | FAIL (as required) | AssertionError |
| `HE-EDIT-015` | PASS | FAIL (as required) | AssertionError |
| `HE-EDIT-016` | PASS | FAIL (as required) | ExecutionError |
| `HE-EDIT-017` | PASS | FAIL (as required) | AssertionError |
| `HE-EDIT-018` | PASS | FAIL (as required) | AssertionError |
| `HE-EDIT-019` | PASS | FAIL (as required) | AssertionError |
| `HE-EDIT-020` | PASS | FAIL (as required) | ExecutionError |

## 7. Self-containment (eligibility) validation

Eligibility rules enforced by AST analysis (`benchmarks/phase0/eligibility.py`):

1. The module parses.
2. The target function is defined exactly once at module level.
3. The target function does not call any function defined in the same file
   (the ITMS skeletonizer replaces such bodies with `...`, which would make
   the task unanswerable).
4. The target function does not reference any other same-file function or
   class name.
5. Every remaining free name used by the target function is either a Python
   builtin or imported by the module itself.

| Task ID | Same-file calls | Same-file name references | Unresolved free names | Eligible |
|---|---|---|---|---|
| `HE-EDIT-001` | none | none | none | yes |
| `HE-EDIT-002` | none | none | none | yes |
| `HE-EDIT-003` | none | none | none | yes |
| `HE-EDIT-004` | none | none | none | yes |
| `HE-EDIT-005` | none | none | none | yes |
| `HE-EDIT-006` | none | none | none | yes |
| `HE-EDIT-007` | none | none | none | yes |
| `HE-EDIT-008` | none | none | none | yes |
| `HE-EDIT-009` | none | none | none | yes |
| `HE-EDIT-010` | none | none | none | yes |
| `HE-EDIT-011` | none | none | none | yes |
| `HE-EDIT-012` | none | none | none | yes |
| `HE-EDIT-013` | none | none | none | yes |
| `HE-EDIT-014` | none | none | none | yes |
| `HE-EDIT-015` | none | none | none | yes |
| `HE-EDIT-016` | none | none | none | yes |
| `HE-EDIT-017` | none | none | none | yes |
| `HE-EDIT-018` | none | none | none | yes |
| `HE-EDIT-019` | none | none | none | yes |
| `HE-EDIT-020` | none | none | none | yes |

## 8. Compatibility with the three editing methods

| Task ID | `full_file_regeneration` | `search_replace_patch` | `itms_ast_splice` |
|---|---|---|---|
| `HE-EDIT-001` | compatible | compatible | compatible |
| `HE-EDIT-002` | compatible | compatible | compatible |
| `HE-EDIT-003` | compatible | compatible | compatible |
| `HE-EDIT-004` | compatible | compatible | compatible |
| `HE-EDIT-005` | compatible | compatible | compatible |
| `HE-EDIT-006` | compatible | compatible | compatible |
| `HE-EDIT-007` | compatible | compatible | compatible |
| `HE-EDIT-008` | compatible | compatible | compatible |
| `HE-EDIT-009` | compatible | compatible | compatible |
| `HE-EDIT-010` | compatible | compatible | compatible |
| `HE-EDIT-011` | compatible | compatible | compatible |
| `HE-EDIT-012` | compatible | compatible | compatible |
| `HE-EDIT-013` | compatible | compatible | compatible |
| `HE-EDIT-014` | compatible | compatible | compatible |
| `HE-EDIT-015` | compatible | compatible | compatible |
| `HE-EDIT-016` | compatible | compatible | compatible |
| `HE-EDIT-017` | compatible | compatible | compatible |
| `HE-EDIT-018` | compatible | compatible | compatible |
| `HE-EDIT-019` | compatible | compatible | compatible |
| `HE-EDIT-020` | compatible | compatible | compatible |

Compatibility is demonstrated by *applying the reference solution* with each
mechanism and re-running the official test in the sandbox; it is not
inferred.

## 9. Licensing and redistribution caveats

- The HumanEval dataset is distributed by OpenAI under the MIT licence (repository `https://github.com/openai/human-eval`, licence file `LICENSE`).
- MIT permits redistribution of the dataset and the derived task files as
  long as the copyright notice and licence text travel with them. The
  upstream licence text **is** vendored next to the dataset subset at
  `benchmarks/data/source/humaneval_LICENSE.txt` (sha256 `bcba3de214851cce46ed5af42d6698044616eeace887c3231bc7a20474ab639e`),
  so the redistribution condition is satisfied inside this repository.
- The upstream licence file does not exist at the pinned dataset commit; it
  was taken from the repository HEAD observed while preparing Phase 0 and
  verified by checksum before being vendored.
- Only the subset of HumanEval problems actually used by the accepted tasks
  is vendored (`benchmarks/data/source/humaneval_subset.jsonl`); the full
  164-problem dataset is not redistributed here.
- The upstream repository has been marked deprecated and unmaintained by
  OpenAI. The dataset is pinned to an immutable commit, so the benchmark
  remains reproducible, but new problems cannot be added from that source.
- The derived task files (mutated sources, instructions) are modifications of
  the original prompts and remain under the upstream licence.

**Data hygiene:** `benchmarks/data/tasks/<TASK_ID>/original_source.py` is the
reference (gold) solution, and `benchmarks/data/gold_solutions.json` holds the
same content. Only `benchmarks/data/model_facing_tasks.json` is safe to hand
to a model. A Phase 1 runner must never glob `benchmarks/data/tasks/*/*.py`
into a prompt.

## 10. Known limitations

- The eligibility analysis is **static**. It reasons about module-level AST
  facts only. A function whose behaviour depends on process state, the
  filesystem or the network is not detected as such by the AST rules; the
  practical mitigation is that every official test is executed in the
  sandbox with sockets blocked.
- The mutation catalogue is intentionally small (5 named rules). It produces
  single-token defects only; it cannot model defects that require
  restructuring the function.
- `off_by_one_boundary` is restricted to integer literals in boundary
  positions (comparison operand, slice bound, subscript index, `range()`
  argument, or an operand of `+`/`-`). Other integer literals are covered by
  the `incorrect_constant` rule.
- The known ITMS nested-function splicer bug (documented in
  `BENCHMARK_READINESS_AUDIT.md`) is **not fixed** and was **not** worked
  around. None of the 20 accepted reference solutions declares a nested
  function in the target body, so the known limitation does not apply to this
  task set; a Phase 1 baseline would still be measured against the
  unmodified splicer.
- The reference solutions used as gold come from the HumanEval
  `canonical_solution` field. They are known-good for the official tests but
  are not necessarily the most readable or most idiomatic implementations.
- Task selection is deterministic but it is a *heuristic* (rule-diversity
  round robin over problems in `HumanEval/...` order). A different
  selection policy would produce a different, equally valid task set.

## 11. Reproducing the offline validation

```bash
# 1. Re-run the Phase 0 validator (offline, no API calls).
python benchmarks/phase0/validate_tasks.py --verify-manifest \
    --json benchmarks/data/validation_report.json

# 2. Regenerate this report from the machine-readable validation output.
python benchmarks/phase0/write_report.py

# 3. Run the Phase 0 tests.
python -m pytest benchmarks/phase0/test_phase0_tasks.py -q
```

Rebuilding the task set from the pinned dataset requires network access
once (the dataset is checksum-verified on download) and is *not* needed to
re-run the validation:

```bash
python benchmarks/phase0/build_tasks.py --tasks 20
```

`benchmarks/data/validation_report.json` and this document are derived
outputs: they are regenerated by the two commands above and are therefore
not part of the manifest (which pins the task data itself).

## 12. Verified facts vs. unresolved assumptions

**Verified by the validator in this repository (offline):**

- 20 of 20 tasks pass every
  check listed in section 6.
- The manifest verifies: 88 files checked, 0 mismatches.
- The gold solution of every task passes the official `check(candidate)` test
  in an isolated sandbox, and every mutated source fails it.
- Every mutation is reproducible from the recorded rule and parameters.
- No model, inference API or benchmark harness was invoked during Phase 0.

**Assumptions and things that are *not* verified here:**

- That the upstream revision `463c980b59e818ace59f6f9803cd92c749ceae61`
  still contains the dataset bytes recorded above. This was verified at
  build time by checksum; it cannot be re-verified without network access.
- That the official HumanEval tests are a fair and sufficient correctness
  oracle. They are the upstream oracle, but they are not exhaustive.
- That the reference solutions are the best possible repairs. They are one
  valid repair; the benchmark measures whether a system finds *a* correct
  repair.
- The three editing methods are compatible with the task set *as
  implemented today in this repository*; a future change to the splicer or
  patcher could change that.

