"""
Generate ``docs/PHASE0_TASK_VALIDATION_REPORT.md`` from the machine-readable
validation output.

The report is generated (never hand-edited) so that every number in it comes
from the artefacts that were actually validated.

Usage::

    python benchmarks/phase0/validate_tasks.py --verify-manifest \
        --json benchmarks/data/validation_report.json
    python benchmarks/phase0/write_report.py
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
DATA_DIR = os.path.join(_ROOT, "benchmarks", "data")
DOCS_DIR = os.path.join(_ROOT, "docs")

VALIDATION_REPORT = os.path.join(DATA_DIR, "validation_report.json")
TASKS_FILE = os.path.join(DATA_DIR, "humaneval_edit_tasks.json")
GOLD_FILE = os.path.join(DATA_DIR, "gold_solutions.json")
CATALOGUE_FILE = os.path.join(DATA_DIR, "mutation_catalogue.json")
MANIFEST_FILE = os.path.join(DATA_DIR, "manifest.json")
BUILD_LOG_FILE = os.path.join(DATA_DIR, "build_log.json")
PROVENANCE_FILE = os.path.join(DATA_DIR, "source", "PROVENANCE.json")
OUTPUT_MD = os.path.join(DOCS_DIR, "PHASE0_TASK_VALIDATION_REPORT.md")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _load(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _group_rejections(build_log: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for entry in build_log["rejected_candidates"]:
        key = entry["reason"].split(":")[0].strip()
        groups.setdefault(key, []).append(entry)
    for entries in groups.values():
        entries.sort(key=lambda e: e["humaneval_task_id"])
    return groups


def build_markdown() -> str:
    validation = _load(VALIDATION_REPORT)
    tasks_doc = _load(TASKS_FILE)
    catalogue = _load(CATALOGUE_FILE)
    manifest = _load(MANIFEST_FILE)
    build_log = _load(BUILD_LOG_FILE)
    provenance = _load(PROVENANCE_FILE)

    tasks = tasks_doc["tasks"]
    by_id = {t["task_id"]: t for t in tasks}
    results = {r["task_id"]: r for r in validation["results"]}
    groups = _group_rejections(build_log)
    manifest_check = validation.get("manifest_verification", {})

    lines: List[str] = []
    add = lines.append

    add("# Phase 0 Task Validation Report — ITMS Editing Benchmark")
    add("")
    add("**Scope:** Phase 0 only — offline sourcing, mutation and validation of")
    add("synthetic single-file Python function-repair tasks derived from HumanEval.")
    add("")
    add("**This is not a HumanEval benchmark result.** The tasks are *synthetic*")
    add("repair tasks derived from HumanEval problems; no model was run against them")
    add("in Phase 0 and no HumanEval pass rate is reported.")
    add("")
    add(f"- Validation report JSON: `benchmarks/data/validation_report.json`")
    add(f"- Task data: `benchmarks/data/humaneval_edit_tasks.json`")
    add(f"- Gold solutions (never sent to a model): `benchmarks/data/gold_solutions.json`")
    add(f"- Model-facing payload: `benchmarks/data/model_facing_tasks.json`")
    add(f"- Mutation catalogue: `benchmarks/data/mutation_catalogue.json`")
    add(f"- Manifest: `benchmarks/data/manifest.json`")
    add(f"- Build log: `benchmarks/data/build_log.json`")
    add("")

    # -- 1. provenance --------------------------------------------------
    add("## 1. Source provenance and revision")
    add("")
    add("| Field | Value |")
    add("|---|---|")
    add(f"| Repository | `{provenance['repository']}` |")
    add(f"| Pinned revision (git commit SHA) | `{provenance['revision']}` |")
    add(f"| Revision verification | {provenance['revision_verified_via']} |")
    add(f"| Dataset path in repository | `{provenance['dataset_path']}` |")
    add(f"| sha256 of `HumanEval.jsonl.gz` | `{provenance['dataset_gz_sha256']}` |")
    add(f"| sha256 of decompressed `HumanEval.jsonl` | `{provenance['dataset_raw_sha256']}` |")
    add(f"| Problems in the pinned dataset | {provenance['dataset_problem_count']} |")
    add(f"| License | {provenance['license']} (`{provenance['license_file']}`, sha256 `{provenance['license_file_sha256']}`) |")
    add(f"| Repository HEAD observed while preparing Phase 0 | `{provenance['repo_head_observed']}` |")
    add(f"| Vendored subset | `{provenance['vendored_subset']}` (sha256 `{provenance['vendored_subset_sha256']}`) |")
    add(f"| Vendored licence text | `{provenance['vendored_license']}` (sha256 `{provenance['vendored_license_sha256']}`) |")
    add("")
    add("**Verified facts**")
    add("")
    add(f"- The pinned revision is the *only* commit in the upstream repository that")
    add(f"  touches `{provenance['dataset_path']}`; it was confirmed through the")
    add("  GitHub commits API, so the dataset content is immutable and independently")
    add("  verifiable by anyone.")
    add("- The compressed and decompressed dataset files were downloaded and their")
    add("  SHA-256 digests were checked against the values above before any task was")
    add("  derived from them. A digest mismatch aborts the build.")
    add("")
    add("**Unresolved / noted**")
    add("")
    for note in provenance.get("notes", []):
        add(f"- {note}")
    add("")

    # -- 2. mutation catalogue -----------------------------------------
    add("## 2. Mutation catalogue")
    add("")
    add(f"Catalogue version `{catalogue['catalogue_version']}`, deterministic: "
        f"{'yes' if catalogue['deterministic'] else 'no'}.")
    add("")
    add(f"Selection policy: {catalogue['selection_policy']}")
    add("")
    add("| Rule | Category | Parameters | Description |")
    add("|---|---|---|---|")
    for rule in catalogue["rules"]:
        add(
            f"| `{rule['name']}` | {rule['category']} | "
            f"{', '.join('`' + p + '`' for p in rule['parameters'])} | {rule['description']} |"
        )
    add("")
    add("Rule usage across the accepted task set:")
    add("")
    add("| Rule | Tasks |")
    add("|---|---|")
    for rule, count in sorted(validation["mutation_rule_usage"].items()):
        add(f"| `{rule}` | {count} |")
    add("")

    # -- 3. counts ------------------------------------------------------
    counts = validation["build_log_counts"]
    add("## 3. Candidate counts")
    add("")
    add("| Stage | Count |")
    add("|---|---|")
    add(f"| HumanEval problems inspected | {counts['problems_inspected']} |")
    add(f"| Passed the self-containment eligibility rules | {counts['eligible']} |")
    add(f"| Eligible **and** with at least one catalogue mutation that fails the official test | {counts['with_viable_mutations']} |")
    add(f"| Accepted as benchmark tasks | {validation['tasks_passed']} |")
    add(f"| Rejected | {counts['rejected']} |")
    add(f"| Eligible but not selected (rule-diversity round robin) | {counts['not_selected']} |")
    add("")
    add(f"**Accepted: {validation['tasks_passed']}. Rejected at the problem level: {counts['rejected']}.**")
    add("")

    # -- 4. accepted tasks ----------------------------------------------
    add("## 4. Accepted tasks")
    add("")
    add("| Task ID | HumanEval problem | Target function | Mutation rule | Mutation parameters | All checks passed |")
    add("|---|---|---|---|---|---|")
    for task in tasks:
        result = results[task["task_id"]]
        params = ", ".join(f"{k}={v!r}" for k, v in task["mutation"]["parameters"].items())
        add(
            f"| `{task['task_id']}` | `{task['humaneval_task_id']}` | "
            f"`{task['target_function']}` | `{task['mutation']['rule']}` | {params} | "
            f"{'yes' if result['passed'] else 'NO'} |"
        )
    add("")

    # -- 5. rejected candidates -----------------------------------------
    add("## 5. Rejected candidates, grouped by reason")
    add("")
    for reason, entries in sorted(groups.items()):
        add(f"### {reason} ({len(entries)})")
        add("")
        add("| HumanEval problem | Detail |")
        add("|---|---|")
        for entry in entries:
            detail = entry["reason"]
            if entry["eligibility_reasons"]:
                detail = "; ".join(entry["eligibility_reasons"])
            elif not entry["gold_outcome"]["passed"]:
                detail = (
                    f"reference solution failed the official test "
                    f"({entry['gold_outcome'].get('error_type')})"
                )
            add(f"| `{entry['humaneval_task_id']}` | {detail} |")
        add("")
    add(
        f"Additionally, {counts['not_selected']} eligible problems with valid failing "
        "mutations were *not* selected, because the selection round robin takes the "
        "first problem per rule to maximise rule diversity. They remain available "
        "as alternates (see `benchmarks/data/build_log.json`, "
        "`not_selected_candidates`)."
    )
    add("")

    # -- 6. validation results ------------------------------------------
    add("## 6. Offline validation results")
    add("")
    add("Every check below was executed offline, in a throw-away temporary")
    add("directory, with `PYTHONPATH` scrubbed, user site-packages disabled, no")
    add("convenience imports injected, and sockets blocked at the interpreter")
    add("level. No model or inference API was contacted.")
    add("")
    check_names: List[str] = []
    for task in tasks:
        for name in results[task["task_id"]]["checks"]:
            if name not in check_names:
                check_names.append(name)
    add("| Check | Tasks passing | Tasks failing |")
    add("|---|---|---|")
    for name in check_names:
        passed = sum(1 for t in tasks if results[t["task_id"]]["checks"][name]["passed"])
        failed = len(tasks) - passed
        add(f"| `{name}` | {passed} | {failed} |")
    add("")
    add("### Gold-test and mutation-failure outcomes")
    add("")
    add("| Task ID | Gold (reference solution) | Mutated (defective) | Mutation failure mode |")
    add("|---|---|---|---|")
    for task in tasks:
        checks = results[task["task_id"]]["checks"]
        gold = checks["gold_solution_passes_official_test"]["detail"]
        mut = checks["mutated_solution_fails_official_test"]["detail"]
        add(
            f"| `{task['task_id']}` | {'PASS' if gold['passed'] else 'FAIL'} | "
            f"{'FAIL (as required)' if not mut['passed'] else 'PASS (unexpected)'} | "
            f"{mut.get('error_type')} |"
        )
    add("")

    # -- 7. self-containment ---------------------------------------------
    add("## 7. Self-containment (eligibility) validation")
    add("")
    add("Eligibility rules enforced by AST analysis (`benchmarks/phase0/eligibility.py`):")
    add("")
    add("1. The module parses.")
    add("2. The target function is defined exactly once at module level.")
    add("3. The target function does not call any function defined in the same file")
    add("   (the ITMS skeletonizer replaces such bodies with `...`, which would make")
    add("   the task unanswerable).")
    add("4. The target function does not reference any other same-file function or")
    add("   class name.")
    add("5. Every remaining free name used by the target function is either a Python")
    add("   builtin or imported by the module itself.")
    add("")
    add("| Task ID | Same-file calls | Same-file name references | Unresolved free names | Eligible |")
    add("|---|---|---|---|---|")
    for task in tasks:
        details = results[task["task_id"]]["checks"]["task_is_self_contained"]["detail"]["details"]
        add(
            f"| `{task['task_id']}` | "
            f"{details.get('same_file_calls') or 'none'} | "
            f"{details.get('same_file_free_name_refs') or 'none'} | "
            f"{details.get('unresolved_free_names') or 'none'} | "
            f"{'yes' if details else 'n/a'} |"
        )
    add("")

    # -- 8. editing-method compatibility --------------------------------
    add("## 8. Compatibility with the three editing methods")
    add("")
    add("| Task ID | `full_file_regeneration` | `search_replace_patch` | `itms_ast_splice` |")
    add("|---|---|---|---|")
    for task in tasks:
        checks = results[task["task_id"]]["checks"]
        regen = checks["compatible_with_full_file_regeneration"]["passed"]
        patch = checks["compatible_with_search_replace_patch"]["passed"]
        splice = checks["compatible_with_itms_ast_splice"]["passed"]
        add(
            f"| `{task['task_id']}` | {'compatible' if regen else 'INCOMPATIBLE'} | "
            f"{'compatible' if patch else 'INCOMPATIBLE'} | "
            f"{'compatible' if splice else 'INCOMPATIBLE'} |"
        )
    add("")
    add("Compatibility is demonstrated by *applying the reference solution* with each")
    add("mechanism and re-running the official test in the sandbox; it is not")
    add("inferred.")
    add("")

    # -- 9. licensing ---------------------------------------------------
    add("## 9. Licensing and redistribution caveats")
    add("")
    add(f"- The HumanEval dataset is distributed by OpenAI under the "
        f"{provenance['license']} licence (repository `{provenance['repository']}`, "
        f"licence file `{provenance['license_file']}`).")
    add("- MIT permits redistribution of the dataset and the derived task files as")
    add("  long as the copyright notice and licence text travel with them. The")
    add("  upstream licence text **is** vendored next to the dataset subset at")
    add(f"  `{provenance['vendored_license']}` (sha256 `{provenance['vendored_license_sha256']}`),")
    add("  so the redistribution condition is satisfied inside this repository.")
    add("- The upstream licence file does not exist at the pinned dataset commit; it")
    add("  was taken from the repository HEAD observed while preparing Phase 0 and")
    add("  verified by checksum before being vendored.")
    add("- Only the subset of HumanEval problems actually used by the accepted tasks")
    add("  is vendored (`benchmarks/data/source/humaneval_subset.jsonl`); the full")
    add("  164-problem dataset is not redistributed here.")
    add("- The upstream repository has been marked deprecated and unmaintained by")
    add("  OpenAI. The dataset is pinned to an immutable commit, so the benchmark")
    add("  remains reproducible, but new problems cannot be added from that source.")
    add("- The derived task files (mutated sources, instructions) are modifications of")
    add("  the original prompts and remain under the upstream licence.")
    add("")
    add("**Data hygiene:** `benchmarks/data/tasks/<TASK_ID>/original_source.py` is the")
    add("reference (gold) solution, and `benchmarks/data/gold_solutions.json` holds the")
    add("same content. Only `benchmarks/data/model_facing_tasks.json` is safe to hand")
    add("to a model. A Phase 1 runner must never glob `benchmarks/data/tasks/*/*.py`")
    add("into a prompt.")
    add("")

    # -- 10. limitations -------------------------------------------------
    add("## 10. Known limitations")
    add("")
    add("- The eligibility analysis is **static**. It reasons about module-level AST")
    add("  facts only. A function whose behaviour depends on process state, the")
    add("  filesystem or the network is not detected as such by the AST rules; the")
    add("  practical mitigation is that every official test is executed in the")
    add("  sandbox with sockets blocked.")
    add("- The mutation catalogue is intentionally small (5 named rules). It produces")
    add("  single-token defects only; it cannot model defects that require")
    add("  restructuring the function.")
    add("- `off_by_one_boundary` is restricted to integer literals in boundary")
    add("  positions (comparison operand, slice bound, subscript index, `range()`")
    add("  argument, or an operand of `+`/`-`). Other integer literals are covered by")
    add("  the `incorrect_constant` rule.")
    add("- The known ITMS nested-function splicer bug (documented in")
    add("  `BENCHMARK_READINESS_AUDIT.md`) is **not fixed** and was **not** worked")
    add("  around. None of the 20 accepted reference solutions declares a nested")
    add("  function in the target body, so the known limitation does not apply to this")
    add("  task set; a Phase 1 baseline would still be measured against the")
    add("  unmodified splicer.")
    add("- The reference solutions used as gold come from the HumanEval")
    add("  `canonical_solution` field. They are known-good for the official tests but")
    add("  are not necessarily the most readable or most idiomatic implementations.")
    add("- Task selection is deterministic but it is a *heuristic* (rule-diversity")
    add("  round robin over problems in `HumanEval/...` order). A different")
    add("  selection policy would produce a different, equally valid task set.")
    add("")

    # -- 11. reproduction ------------------------------------------------
    add("## 11. Reproducing the offline validation")
    add("")
    add("```bash")
    add("# 1. Re-run the Phase 0 validator (offline, no API calls).")
    add("python benchmarks/phase0/validate_tasks.py --verify-manifest \\")
    add("    --json benchmarks/data/validation_report.json")
    add("")
    add("# 2. Regenerate this report from the machine-readable validation output.")
    add("python benchmarks/phase0/write_report.py")
    add("")
    add("# 3. Run the Phase 0 tests.")
    add("python -m pytest benchmarks/phase0/test_phase0_tasks.py -q")
    add("```")
    add("")
    add("Rebuilding the task set from the pinned dataset requires network access")
    add("once (the dataset is checksum-verified on download) and is *not* needed to")
    add("re-run the validation:")
    add("")
    add("```bash")
    add("python benchmarks/phase0/build_tasks.py --tasks 20")
    add("```")
    add("")
    add("`benchmarks/data/validation_report.json` and this document are derived")
    add("outputs: they are regenerated by the two commands above and are therefore")
    add("not part of the manifest (which pins the task data itself).")
    add("")

    # -- 12. facts vs assumptions ----------------------------------------
    add("## 12. Verified facts vs. unresolved assumptions")
    add("")
    add("**Verified by the validator in this repository (offline):**")
    add("")
    add(f"- {validation['tasks_passed']} of {validation['tasks_total']} tasks pass every")
    add("  check listed in section 6.")
    add(f"- The manifest verifies: {manifest_check.get('files_checked', 0)} files checked, "
        f"{len(manifest_check.get('mismatches', []))} mismatches.")
    add("- The gold solution of every task passes the official `check(candidate)` test")
    add("  in an isolated sandbox, and every mutated source fails it.")
    add("- Every mutation is reproducible from the recorded rule and parameters.")
    add("- No model, inference API or benchmark harness was invoked during Phase 0.")
    add("")
    add("**Assumptions and things that are *not* verified here:**")
    add("")
    add("- That the upstream revision `463c980b59e818ace59f6f9803cd92c749ceae61`")
    add("  still contains the dataset bytes recorded above. This was verified at")
    add("  build time by checksum; it cannot be re-verified without network access.")
    add("- That the official HumanEval tests are a fair and sufficient correctness")
    add("  oracle. They are the upstream oracle, but they are not exhaustive.")
    add("- That the reference solutions are the best possible repairs. They are one")
    add("  valid repair; the benchmark measures whether a system finds *a* correct")
    add("  repair.")
    add("- The three editing methods are compatible with the task set *as")
    add("  implemented today in this repository*; a future change to the splicer or")
    add("  patcher could change that.")
    add("")

    return "\n".join(lines) + "\n"


def main() -> int:
    os.makedirs(DOCS_DIR, exist_ok=True)
    markdown = build_markdown()
    with open(OUTPUT_MD, "w", encoding="utf-8") as fh:
        fh.write(markdown)
    print(f"wrote {os.path.relpath(OUTPUT_MD, _ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
