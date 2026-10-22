"""
Phase 0 offline validator for the *modular* task family.

The validator re-derives every fact about the family **from the files on
disk**.  It makes no network request, contacts no model and does not write
into the task directory (only into the report file it is asked for).

It runs the same core checks as the HumanEval validator
(:mod:`benchmarks.phase0.validate_tasks`) — the mutation must reproduce
byte-for-byte, the docstring must survive, the reference module must pass
the official check, the defective module must fail it, the reference must
not leak, the task must be self-contained, and the task must be solvable by
all three editing methods — and adds the checks that only make sense for
this family:

*   the task satisfies the **pre-registered** representative-task rule of
    :mod:`benchmarks.phase2.task_selection` (file size, target ratio,
    function count, class count),
*   the defect is **byte-local** (one changed line), so the repair really is
    a small, localised edit,
*   the official check contains several assertions, so a *partially* correct
    repair fails it,
*   the worked example in the docstring does **not** leak into the official
    check, so the task cannot be solved by copying the example,
*   the worked examples in the docstring are **correct**,
*   the target name is unique in the module, so the mutation engine and the
    AST splicer cannot become ambiguous.

Usage::

    python benchmarks/phase0/validate_modular_tasks.py
    python benchmarks/phase0/validate_modular_tasks.py --verify-manifest \
        --json benchmarks/data/modular/validation_report.json
"""

from __future__ import annotations

import argparse
import ast
import doctest
import importlib.util
import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from eligibility import check_self_containment, docstring_of  # noqa: E402
from mutation_engine import apply_mutation  # noqa: E402
from runner import run_official_test  # noqa: E402
from validate_tasks import _ast_splice_check, _search_replace_check  # noqa: E402

DATA_DIR = os.path.join(_ROOT, "benchmarks", "data", "modular")
TASKS_FILE = os.path.join(DATA_DIR, "modular_edit_tasks.json")
GOLD_FILE = os.path.join(DATA_DIR, "gold_solutions.json")
MODEL_FACING_FILE = os.path.join(DATA_DIR, "model_facing_tasks.json")
MANIFEST_FILE = os.path.join(DATA_DIR, "manifest.json")
BUILD_LOG_FILE = os.path.join(DATA_DIR, "build_log.json")

TEST_TIMEOUT = 25.0

#: The number of assertions an official check must contain.  A check with a
#: single assertion cannot distinguish a partially correct repair.
MINIMUM_ASSERTIONS = 3


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _collect_scalars(value: Any) -> Set[Any]:
    """Collect the string and float scalars inside a literal value."""
    found: Set[Any] = set()
    if isinstance(value, bool):
        return found
    if isinstance(value, (str, float)):
        found.add(value)
        return found
    if isinstance(value, dict):
        for key, item in value.items():
            found |= _collect_scalars(key)
            found |= _collect_scalars(item)
        return found
    if isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            found |= _collect_scalars(item)
        return found
    return found


def docstring_expected_scalars(docstring: str) -> Set[Any]:
    """
    Return the string/float scalars that the worked examples of a docstring
    show as *results*.

    Only the expected-output lines of a doctest are considered, not the
    input line, so a number that merely appears as an argument is not
    treated as a leak.
    """
    scalars: Set[Any] = set()
    lines = docstring.splitlines()
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped.startswith(">>>"):
            index += 1
            continue
        index += 1
        expected: List[str] = []
        while index < len(lines):
            nxt = lines[index].strip()
            if not nxt or nxt.startswith(">>>"):
                break
            expected.append(nxt)
            index += 1
        for expression in expected:
            try:
                value = ast.literal_eval(expression)
            except (ValueError, SyntaxError):
                continue
            scalars |= _collect_scalars(value)
    return scalars


def asserted_literals(test_source: str) -> Set[Any]:
    """Return the string/float literals that the official check asserts."""
    literals: Set[Any] = set()
    try:
        tree = ast.parse(test_source)
    except SyntaxError:
        return literals
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        for statement in ast.walk(node.test):
            if isinstance(statement, ast.Constant):
                value = statement.value
                if isinstance(value, bool):
                    continue
                if isinstance(value, str) and len(value) >= 2:
                    literals.add(value)
                elif isinstance(value, float):
                    literals.add(value)
    return literals


def count_assertions(test_source: str) -> int:
    """Return the number of assertions in the official check."""
    try:
        tree = ast.parse(test_source)
    except SyntaxError:
        return 0
    return sum(1 for node in ast.walk(tree) if isinstance(node, ast.Assert))


def function_name_count(source: str, name: str) -> int:
    """Return how often a function name is defined in a module."""
    tree = ast.parse(source)
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )


def changed_lines(original: str, mutated: str) -> List[int]:
    """Return the 1-based numbers of the lines that differ."""
    left = original.splitlines()
    right = mutated.splitlines()
    if len(left) != len(right):
        return list(range(1, max(len(left), len(right)) + 1))
    return [
        index + 1 for index, (a, b) in enumerate(zip(left, right)) if a != b
    ]


# --------------------------------------------------------------------------
# Per-task validation
# --------------------------------------------------------------------------

def validate_task(record: Dict[str, Any], gold_source: str) -> Dict[str, Any]:
    """Run every check for one task and return a structured result."""
    checks: Dict[str, Dict[str, Any]] = {}

    def record_check(name: str, passed: bool, detail: Any = None) -> bool:
        checks[name] = {"passed": bool(passed), "detail": detail}
        return bool(passed)

    target = record["target_function"]
    prompt = record["problem_prompt"]
    official_test = record["official_test_source"]
    original = record["original_source"]
    mutated = record["mutated_source"]
    mutation = record["mutation"]

    # -- 1. mutation reproducibility --------------------------------------
    try:
        reproduced, _ = apply_mutation(
            original, target, mutation["rule"], mutation["parameters"]
        )
        record_check(
            "mutation_reproduces_recorded_source",
            reproduced == mutated,
            {"rule": mutation["rule"], "parameters": mutation["parameters"]},
        )
    except Exception as exc:
        record_check(
            "mutation_reproduces_recorded_source",
            False,
            f"{type(exc).__name__}: {exc}",
        )

    # -- 2. syntactic validity --------------------------------------------
    original_ok = mutated_ok = True
    original_err = mutated_err = None
    try:
        ast.parse(original)
    except SyntaxError as exc:
        original_ok, original_err = False, str(exc)
    try:
        ast.parse(mutated)
    except SyntaxError as exc:
        mutated_ok, mutated_err = False, str(exc)
    record_check(
        "original_and_mutated_parse",
        original_ok and mutated_ok,
        {
            "original_parses": original_ok,
            "mutated_parses": mutated_ok,
            "original_error": original_err,
            "mutated_error": mutated_err,
        },
    )

    # -- 3. the docstring survives the mutation ----------------------------
    try:
        orig_doc = docstring_of(ast.parse(original), target)
        mut_doc = docstring_of(ast.parse(mutated), target)
        record_check(
            "docstring_preserved_byte_for_byte",
            orig_doc is not None and orig_doc == mut_doc,
            {
                "docstring_identical": orig_doc == mut_doc,
                "docstring_present": orig_doc is not None,
                "problem_prompt_matches_docstring": orig_doc == prompt,
            },
        )
    except SyntaxError as exc:
        record_check("docstring_preserved_byte_for_byte", False, f"SyntaxError: {exc}")

    # -- 4. the reference module passes the official check -----------------
    gold_outcome = run_official_test(original, official_test, target, timeout=TEST_TIMEOUT)
    record_check(
        "reference_module_passes_official_check",
        gold_outcome.passed,
        gold_outcome.to_dict(),
    )

    # -- 5. the defective module fails the official check ------------------
    mut_outcome = run_official_test(mutated, official_test, target, timeout=TEST_TIMEOUT)
    record_check(
        "defective_module_fails_official_check",
        not mut_outcome.passed and not mut_outcome.timed_out,
        mut_outcome.to_dict(),
    )

    # -- 6. the official check is discriminating ---------------------------
    assertions = count_assertions(official_test)
    record_check(
        "official_check_has_several_assertions",
        assertions >= MINIMUM_ASSERTIONS,
        {
            "assertions": assertions,
            "minimum_required": MINIMUM_ASSERTIONS,
            "note": (
                "A check with a single assertion cannot distinguish a "
                "partially correct repair."
            ),
        },
    )

    # -- 7. the defect is byte-local ---------------------------------------
    changed = changed_lines(original, mutated)
    record_check(
        "defect_is_confined_to_one_line",
        len(changed) == 1,
        {
            "changed_lines": changed,
            "changed_line_count": len(changed),
            "expected_change": mutation.get("expected_change"),
        },
    )

    # -- 8. the worked example does not leak into the check -----------------
    try:
        doc = docstring_of(ast.parse(original), target) or ""
    except SyntaxError:
        doc = ""
    example_scalars = docstring_expected_scalars(doc)
    check_literals = asserted_literals(official_test)
    overlap = sorted(
        repr(value) for value in (example_scalars & check_literals)
    )
    record_check(
        "worked_example_does_not_leak_into_the_official_check",
        not overlap,
        {
            "overlapping_values": overlap,
            "example_values": sorted(repr(v) for v in example_scalars),
            "note": (
                "If a value shown as the result of the worked example were "
                "also asserted by the official check, the task could be "
                "solved by hard-coding the example."
            ),
        },
    )

    # -- 9. the worked examples are correct --------------------------------
    module_path = os.path.join(
        _ROOT, "benchmarks", "data", "modular", "source", f"{record['module']}.py"
    )
    example_ok = True
    example_detail: Any = {"checked": False}
    if os.path.exists(module_path):
        spec = importlib.util.spec_from_file_location(
            f"_modular_validate_{record['module']}", module_path
        )
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
            result = doctest.testmod(module, verbose=False)
            example_ok = result.failed == 0
            example_detail = {
                "checked": True,
                "failed": result.failed,
                "attempted": result.attempted,
            }
        except Exception as exc:  # pragma: no cover - defensive
            example_ok = False
            example_detail = {"error": f"{type(exc).__name__}: {exc}"}
    record_check("worked_examples_in_the_module_are_correct", example_ok, example_detail)

    # -- 10. no leakage of the reference module ---------------------------
    gold_lines = [line.strip() for line in gold_source.splitlines() if line.strip()]
    test_leak = any(len(line) > 30 and line in official_test for line in gold_lines)
    instruction_leak = any(
        len(line) > 30 and line in record["repair_instruction"] for line in gold_lines
    )
    record_check(
        "reference_module_not_revealed",
        not test_leak and not instruction_leak,
        {
            "official_test_does_not_contain_module_lines": not test_leak,
            "instruction_does_not_contain_module_lines": not instruction_leak,
        },
    )

    # -- 11. self-containment ---------------------------------------------
    eligibility = check_self_containment(original, target)
    record_check(
        "task_is_self_contained",
        eligibility.eligible,
        {"reasons": eligibility.reasons, "details": eligibility.details},
    )

    # -- 12. the target name is unambiguous --------------------------------
    occurrences = function_name_count(original, target)
    record_check(
        "target_name_is_unique_in_the_module",
        occurrences == 1,
        {"target_function": target, "definitions_found": occurrences},
    )

    # -- 13. the module text is not credential-shaped ------------------------
    # The artefact store redacts anything that *looks* like a credential
    # before it writes a candidate.  If the module text tripped that
    # heuristic, the stored candidate would no longer be byte-identical to
    # the bytes that were handed to the official evaluator.  The check below
    # is therefore a correctness requirement, not a security nicety.
    from benchmarks.benchmark.provider import contains_credential

    record_check(
        "module_text_is_not_credential_shaped",
        not contains_credential(original),
        {
            "note": (
                "A credential-shaped module would be redacted by the artefact "
                "store, so the saved candidate would differ from the evaluated "
                "candidate."
            ),
        },
    )
    record_check(
        "official_check_is_not_credential_shaped",
        not contains_credential(official_test),
        {"note": "the official test is stored verbatim"},
    )

    # -- 14. compatibility with the three editing methods -----------------
    record_check(
        "compatible_with_full_file_regeneration",
        gold_outcome.passed,
        {"note": "the complete reference module passes the official check"},
    )
    record_check(
        "compatible_with_search_replace_patch", *_search_replace_check(record, gold_source)
    )
    record_check(
        "compatible_with_itms_ast_splice", *_ast_splice_check(record, gold_source)
    )

    all_passed = all(check["passed"] for check in checks.values())
    return {
        "task_id": record["task_id"],
        "module": record["module"],
        "target_function": target,
        "mutation": {
            "rule": mutation["rule"],
            "parameters": mutation["parameters"],
            "expected_change": mutation.get("expected_change"),
        },
        "passed": all_passed,
        "checks": checks,
    }


# --------------------------------------------------------------------------
# Selection-rule validation
# --------------------------------------------------------------------------

def validate_selection(tasks_doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Check the family against the **pre-registered** representative-task rule.

    The rule is the one in :mod:`benchmarks.phase2.task_selection`; it is
    applied here to the *new* family, without being changed.
    """
    from benchmarks.phase2.task_selection import (
        DEFAULT_CRITERIA,
        SelectionCriteria,
        compute_task_properties,
    )

    class _Task:
        def __init__(self, record: Dict[str, Any]) -> None:
            self.task_id = record["task_id"]
            self.mutated_source = record["mutated_source"]
            self.target_function = record["target_function"]

    criteria: SelectionCriteria = DEFAULT_CRITERIA
    rows: List[Dict[str, Any]] = []
    failures: Dict[str, List[str]] = {}
    for record in tasks_doc["tasks"]:
        properties = compute_task_properties(_Task(record))
        problems = criteria.failures(properties)
        rows.append(properties.to_dict())
        if problems:
            failures[record["task_id"]] = problems
    return {
        "criteria": criteria.to_dict(),
        "tasks_total": len(rows),
        "tasks_qualifying": len(rows) - len(failures),
        "tasks_rejected": len(failures),
        "rejections": failures,
        "properties": rows,
        "all_qualify": not failures,
    }


# --------------------------------------------------------------------------
# Manifest verification
# --------------------------------------------------------------------------

def verify_manifest() -> Dict[str, Any]:
    """Verify the checksums recorded in the manifest."""
    manifest = _load_json(MANIFEST_FILE)
    mismatches: List[str] = []
    checked = 0
    for rel, meta in manifest.get("files", {}).items():
        path = os.path.join(_ROOT, rel)
        if not os.path.exists(path):
            mismatches.append(f"missing file: {rel}")
            continue
        digest = _sha256_text(_read(path))
        checked += 1
        if digest != meta["sha256"]:
            mismatches.append(f"checksum mismatch: {rel}")
    for task_id, files in manifest.get("task_files", {}).items():
        for key, meta in files.items():
            path = os.path.join(_ROOT, meta["path"])
            if not os.path.exists(path):
                mismatches.append(f"missing task file: {meta['path']}")
                continue
            digest = _sha256_text(_read(path))
            checked += 1
            if digest != meta["sha256"]:
                mismatches.append(f"checksum mismatch: {meta['path']}")
    return {
        "manifest_present": True,
        "files_checked": checked,
        "mismatches": mismatches,
        "passed": not mismatches,
    }


def _sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def validate_all() -> Dict[str, Any]:
    tasks_doc = _load_json(TASKS_FILE)
    gold_doc = _load_json(GOLD_FILE)
    model_doc = _load_json(MODEL_FACING_FILE)
    build_log = _load_json(BUILD_LOG_FILE)

    model_facing_ids = {t["task_id"] for t in model_doc.get("tasks", [])}

    results = []
    for record in tasks_doc["tasks"]:
        gold = gold_doc["gold_solutions"].get(record["task_id"], "")
        result = validate_task(record, gold)
        result["checks"]["task_present_in_model_facing_payload"] = {
            "passed": record["task_id"] in model_facing_ids,
            "detail": {"model_facing_task_ids": len(model_facing_ids)},
        }
        result["passed"] = all(c["passed"] for c in result["checks"].values())
        results.append(result)

    accepted = [r for r in results if r["passed"]]
    failed = [r for r in results if not r["passed"]]
    failed_checks: Dict[str, int] = {}
    for r in failed:
        for name, check in r["checks"].items():
            if not check["passed"]:
                failed_checks[name] = failed_checks.get(name, 0) + 1

    rule_usage: Dict[str, int] = {}
    for r in results:
        rule = r["mutation"]["rule"]
        rule_usage[rule] = rule_usage.get(rule, 0) + 1

    module_usage: Dict[str, int] = {}
    for r in results:
        module_usage[r["module"]] = module_usage.get(r["module"], 0) + 1

    selection = validate_selection(tasks_doc)

    return {
        "schema_version": "1.0",
        "task_set": "modular_edit",
        "tasks_total": len(results),
        "tasks_passed": len(accepted),
        "tasks_failed": len(failed),
        "failed_check_counts": failed_checks,
        "mutation_rule_usage": rule_usage,
        "module_usage": module_usage,
        "distinct_modules": len(module_usage),
        "distinct_targets": len({r["target_function"] for r in results}),
        "selection_rule": selection,
        "build_log_counts": {
            "targets_inspected": build_log["targets_inspected"],
            "eligible": build_log["eligible"],
            "gold_passed": build_log["gold_passed"],
            "with_viable_mutations": build_log["with_viable_mutations"],
            "rejected": build_log["rejected"],
            "not_selected": build_log["not_selected"],
        },
        "results": results,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the modular Phase 0 task family offline"
    )
    parser.add_argument("--json", dest="json_path", default=None)
    parser.add_argument("--verify-manifest", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    report = validate_all()
    if args.verify_manifest:
        report["manifest_verification"] = verify_manifest()

    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)
            fh.write("\n")

    if not args.quiet:
        selection = report["selection_rule"]
        print("Phase 0 modular task family (offline validation)")
        print(f"tasks            : {report['tasks_total']}")
        print(f"passed           : {report['tasks_passed']}")
        print(f"failed           : {report['tasks_failed']}")
        print(f"modules          : {report['distinct_modules']}")
        print(f"distinct targets : {report['distinct_targets']}")
        print(f"mutation rules   : {report['mutation_rule_usage']}")
        print(
            "pre-registered rule: "
            f"{selection['tasks_qualifying']}/{selection['tasks_total']} qualify, "
            f"criteria={selection['criteria']}"
        )
        for result in report["results"]:
            if result["passed"]:
                continue
            for name, check in result["checks"].items():
                if not check["passed"]:
                    print(f"  FAIL {result['task_id']}: {name} -> {check['detail']}")
        if "manifest_verification" in report:
            mv = report["manifest_verification"]
            print(
                f"manifest: {'OK' if mv['passed'] else 'MISMATCH'} "
                f"({mv['files_checked']} files)"
            )
            for mismatch in mv["mismatches"]:
                print(f"  {mismatch}")

    failed_tasks = report["tasks_failed"]
    selection_ok = report["selection_rule"]["all_qualify"]
    manifest_ok = report.get("manifest_verification", {}).get("passed", True)
    return 0 if (failed_tasks == 0 and selection_ok and manifest_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
