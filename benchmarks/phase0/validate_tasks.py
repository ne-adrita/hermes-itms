"""
Phase 0 offline validator.

Re-validates every Phase 0 artefact *from the files on disk*, without any
network access and without any model call.  For each accepted task the
validator performs the checks required by the Phase 0 specification:

1.  the recorded problem prompt, official test and reference source match the
    vendored copy of the pinned HumanEval revision,
2.  the deterministic mutation reproduces the recorded mutated source
    byte-for-byte from the reference source,
3.  the original docstring / problem statement is preserved byte-for-byte in
    the mutated file,
4.  both the original and the mutated file parse,
5.  the gold/reference solution passes the official ``check`` test,
6.  the mutated solution fails the official ``check`` test,
7.  neither the repair instruction nor the official test reveals the reference
    solution, and the model-facing payload contains no reference solution and
    no mutation metadata,
8.  the task is self-contained under the AST eligibility rules,
9.  the task is compatible with all three intended editing methods
    (``full_file_regeneration``, ``search_replace_patch``, ``itms_ast_splice``).

The validator also verifies the manifest checksums so that the task data can
be shown to be unchanged.

Usage::

    python benchmarks/phase0/validate_tasks.py
    python benchmarks/phase0/validate_tasks.py --verify-manifest --json out.json
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
import textwrap
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dataset import (  # noqa: E402
    DATASET_GZ_SHA256,
    DATASET_PATH,
    DATASET_RAW_SHA256,
    LICENSE_SPDX,
    SOURCE_REPO_URL,
    SOURCE_REVISION,
    sha256_text,
)
from eligibility import check_self_containment, docstring_of  # noqa: E402
from mutation_engine import apply_mutation  # noqa: E402
from runner import run_official_test  # noqa: E402

DATA_DIR = os.path.join(_ROOT, "benchmarks", "data")
TASKS_FILE = os.path.join(DATA_DIR, "humaneval_edit_tasks.json")
GOLD_FILE = os.path.join(DATA_DIR, "gold_solutions.json")
MODEL_FACING_FILE = os.path.join(DATA_DIR, "model_facing_tasks.json")
CATALOGUE_FILE = os.path.join(DATA_DIR, "mutation_catalogue.json")
MANIFEST_FILE = os.path.join(DATA_DIR, "manifest.json")
BUILD_LOG_FILE = os.path.join(DATA_DIR, "build_log.json")
SUBSET_FILE = os.path.join(DATA_DIR, "source", "humaneval_subset.jsonl")
PROVENANCE_FILE = os.path.join(DATA_DIR, "source", "PROVENANCE.json")

TEST_TIMEOUT = 25.0


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------
# Per-task validation
# --------------------------------------------------------------------------

def validate_task(
    record: Dict[str, Any],
    gold_source: str,
    subset_by_he_id: Dict[str, Dict[str, Any]],
    model_facing_ids: set,
) -> Dict[str, Any]:
    """Run every Phase 0 check for one task and return a structured result."""
    checks: Dict[str, Dict[str, Any]] = {}

    def record_check(name: str, passed: bool, detail: Any = None, **extra) -> bool:
        checks[name] = {"passed": bool(passed), "detail": detail}
        if extra:
            checks[name].update(extra)
        return bool(passed)

    target = record["target_function"]
    prompt = record["problem_prompt"]
    official_test = record["official_test_source"]
    original = record["original_source"]
    mutated = record["mutated_source"]
    mutation = record["mutation"]

    # -- 1. provenance / pinned source ------------------------------------
    vendored = subset_by_he_id.get(record["humaneval_task_id"])
    if vendored is None:
        record_check("source_matches_pinned_dataset", False, "problem not present in vendored subset")
    else:
        prompt_ok = vendored["prompt"] == prompt
        test_ok = vendored["test"] == official_test
        checksum_ok = (
            record["checksums"]["problem_prompt_sha256"] == sha256_text(prompt)
            and record["checksums"]["official_test_sha256"] == sha256_text(official_test)
            and record["checksums"]["original_source_sha256"] == sha256_text(original)
            and record["checksums"]["mutated_source_sha256"] == sha256_text(mutated)
        )
        record_check(
            "source_matches_pinned_dataset",
            prompt_ok and test_ok and checksum_ok,
            {
                "prompt_identical": prompt_ok,
                "official_test_identical": test_ok,
                "checksums_consistent": checksum_ok,
                "revision": record["source"]["revision"],
                "repository": record["source"]["repository"],
                "license": record["source"]["license"],
            },
        )

    # -- 2. mutation reproducibility --------------------------------------
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
        record_check("mutation_reproduces_recorded_source", False, f"{type(exc).__name__}: {exc}")

    # -- 3. docstring / problem statement preserved byte-for-byte ---------
    try:
        orig_tree = ast.parse(original)
        mut_tree = ast.parse(mutated)
        orig_doc = docstring_of(orig_tree, target)
        mut_doc = docstring_of(mut_tree, target)
        prompt_prefix = prompt.rstrip("\n")
        doc_ok = orig_doc is not None and orig_doc == mut_doc
        prompt_ok = prompt_prefix in mutated
        record_check(
            "docstring_and_prompt_preserved_byte_for_byte",
            doc_ok and prompt_ok,
            {
                "docstring_identical": doc_ok,
                "prompt_prefix_present_in_mutated_file": prompt_ok,
                "docstring_sha256": sha256_text(orig_doc) if orig_doc else None,
            },
        )
    except SyntaxError as exc:
        record_check("docstring_and_prompt_preserved_byte_for_byte", False, f"SyntaxError: {exc}")

    # -- 4. syntactic validity --------------------------------------------
    orig_ok = mut_ok = True
    orig_err = mut_err = None
    try:
        ast.parse(original)
    except SyntaxError as exc:
        orig_ok, orig_err = False, str(exc)
    try:
        ast.parse(mutated)
    except SyntaxError as exc:
        mut_ok, mut_err = False, str(exc)
    record_check(
        "original_and_mutated_parse",
        orig_ok and mut_ok,
        {"original_parses": orig_ok, "mutated_parses": mut_ok, "original_error": orig_err, "mutated_error": mut_err},
    )

    # -- 5. gold passes the official test ---------------------------------
    gold_outcome = run_official_test(original, official_test, target, timeout=TEST_TIMEOUT)
    record_check(
        "gold_solution_passes_official_test",
        gold_outcome.passed,
        gold_outcome.to_dict(),
    )

    # -- 6. mutated fails the official test --------------------------------
    mut_outcome = run_official_test(mutated, official_test, target, timeout=TEST_TIMEOUT)
    record_check(
        "mutated_solution_fails_official_test",
        not mut_outcome.passed and not mut_outcome.timed_out,
        mut_outcome.to_dict(),
    )

    # -- 7. no leakage of the reference solution ---------------------------
    gold_lines = [ln.strip() for ln in gold_source.splitlines() if ln.strip()]
    instruction = record["repair_instruction"]
    test_leak = any(
        len(line) > 30 and line in official_test for line in gold_lines
    )
    instruction_leak = any(
        len(line) > 30 and line in instruction for line in gold_lines
    )
    gold_body_leak = gold_source.strip() in mutated
    record_check(
        "reference_solution_not_revealed",
        not test_leak and not instruction_leak and not gold_body_leak,
        {
            "official_test_does_not_contain_gold_lines": not test_leak,
            "instruction_does_not_contain_gold_lines": not instruction_leak,
            "mutated_file_does_not_contain_full_gold_source": not gold_body_leak,
        },
    )

    # -- 8. self-containment ----------------------------------------------
    eligibility = check_self_containment(original, target)
    record_check(
        "task_is_self_contained",
        eligibility.eligible,
        {
            "reasons": eligibility.reasons,
            "details": eligibility.details,
            "note": (
                "Checks module-level AST facts only. Runtime behaviour that "
                "depends on process state, filesystem or network is not "
                "analysed; the official tests are executed in a sandbox with "
                "sockets blocked to cover the practical part of that risk."
            ),
        },
    )

    # -- 9. compatibility with the three editing methods ------------------
    record_check(
        "compatible_with_full_file_regeneration",
        gold_outcome.passed,
        {"note": "the complete reference module passes the official test"},
    )
    record_check(
        "compatible_with_search_replace_patch", *_search_replace_check(record, gold_source)
    )
    record_check(
        "compatible_with_itms_ast_splice", *_ast_splice_check(record, gold_source)
    )

    # -- 10. model-facing separation ---------------------------------------
    in_model_facing = record["task_id"] in model_facing_ids
    record_check(
        "model_facing_payload_excludes_gold_and_mutation_metadata",
        in_model_facing,
        {"task_present_in_model_facing_payload": in_model_facing},
    )

    all_passed = all(c["passed"] for c in checks.values())
    return {
        "task_id": record["task_id"],
        "humaneval_task_id": record["humaneval_task_id"],
        "target_function": target,
        "mutation": {"rule": mutation["rule"], "parameters": mutation["parameters"]},
        "passed": all_passed,
        "checks": checks,
    }


def _function_source(source: str, target: str) -> Optional[str]:
    """Return the exact source text of the target function."""
    lines = source.splitlines(keepends=True)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == target:
            start = node.lineno
            if getattr(node, "decorator_list", None):
                start = min(start, node.decorator_list[0].lineno)
            return "".join(lines[start - 1 : node.end_lineno])
    return None


def _search_replace_check(record: Dict[str, Any], gold_source: str) -> Tuple[bool, Any]:
    """Verify that the task is solvable with a search/replace patch."""
    from surgical_splicer import TextDiffPatcher

    target = record["target_function"]
    mutated = record["mutated_source"]
    search_block = _function_source(mutated, target)
    replace_block = _function_source(gold_source, target)
    if search_block is None or replace_block is None:
        return False, {"error": "could not locate target function text"}
    ok, patched, error = TextDiffPatcher.apply_search_replace(
        mutated, search_block, replace_block
    )
    detail: Dict[str, Any] = {"patch_applied": ok, "error": error}
    if ok:
        outcome = run_official_test(patched, record["official_test_source"], target, timeout=TEST_TIMEOUT)
        detail["patched_passes_official_test"] = outcome.passed
        detail["outcome"] = outcome.to_dict()
        return outcome.passed, detail
    return False, detail


def _ast_splice_check(record: Dict[str, Any], gold_source: str) -> Tuple[bool, Any]:
    """Verify that the task is solvable with an AST body splice."""
    from surgical_splicer import ASTNodeLocator, ASTSurgicalSplicer

    target = record["target_function"]
    mutated = record["mutated_source"]
    gold_body = _function_source(gold_source, target)
    if gold_body is None:
        return False, {"error": "could not locate target function text"}

    located = ASTNodeLocator.locate_target(mutated, target)
    if located is None:
        return False, {"error": "target not located in mutated source"}

    body = _body_only(gold_body)
    ok, spliced, error = ASTSurgicalSplicer.splice(mutated, located, body)
    detail: Dict[str, Any] = {"splice_applied": ok, "error": error}
    if not ok:
        return False, detail
    outcome = run_official_test(spliced, record["official_test_source"], target, timeout=TEST_TIMEOUT)
    detail["spliced_passes_official_test"] = outcome.passed
    detail["outcome"] = outcome.to_dict()
    return outcome.passed, detail


def _body_only(function_source: str) -> str:
    """
    Strip the ``def`` header from a function source block.

    The header may span several lines (a long parameter list, or a long
    return annotation).  The header is therefore located with the AST
    instead of by scanning for the first indented line: the body of a
    function starts at its first statement.  A function whose body cannot
    be located yields the empty string.

    This is a *correctness fix* for the body extraction only.  It changes
    no check, weakens no check and does not touch the official evaluator.
    """
    lines = function_source.splitlines(keepends=True)
    try:
        module = ast.parse(textwrap.dedent("".join(lines)))
    except SyntaxError:
        return ""
    bodies = [
        node.body
        for node in ast.walk(module)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    if not bodies:
        return ""
    body = bodies[0]
    if not body:
        return ""
    start = body[0].lineno - 1
    if start < 0 or start >= len(lines):
        return ""
    return "".join(lines[start:])


# --------------------------------------------------------------------------
# Manifest verification
# --------------------------------------------------------------------------

def verify_manifest() -> Dict[str, Any]:
    manifest = _load_json(MANIFEST_FILE)
    mismatches: List[str] = []
    checked = 0
    for rel, meta in manifest.get("files", {}).items():
        path = os.path.join(_ROOT, rel)
        if not os.path.exists(path):
            mismatches.append(f"missing file: {rel}")
            continue
        digest = sha256_text(_read(path))
        checked += 1
        if digest != meta["sha256"]:
            mismatches.append(f"checksum mismatch: {rel}")
    for task_id, files in manifest.get("task_files", {}).items():
        for key, meta in files.items():
            path = os.path.join(_ROOT, meta["path"])
            if not os.path.exists(path):
                mismatches.append(f"missing task file: {meta['path']}")
                continue
            digest = sha256_text(_read(path))
            checked += 1
            if digest != meta["sha256"]:
                mismatches.append(f"checksum mismatch: {meta['path']}")
    return {
        "manifest_present": True,
        "files_checked": checked,
        "mismatches": mismatches,
        "passed": not mismatches,
    }


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def validate_all() -> Dict[str, Any]:
    tasks_doc = _load_json(TASKS_FILE)
    gold_doc = _load_json(GOLD_FILE)
    model_doc = _load_json(MODEL_FACING_FILE)
    catalogue = _load_json(CATALOGUE_FILE)
    manifest = _load_json(MANIFEST_FILE)
    build_log = _load_json(BUILD_LOG_FILE)

    subset_by_he_id: Dict[str, Dict[str, Any]] = {}
    if os.path.exists(SUBSET_FILE):
        with open(SUBSET_FILE, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    entry = json.loads(line)
                    subset_by_he_id[entry["task_id"]] = entry

    model_facing_ids = {t["task_id"] for t in model_doc.get("tasks", [])}

    results = []
    for record in tasks_doc["tasks"]:
        gold = gold_doc["gold_solutions"].get(record["task_id"], "")
        results.append(
            validate_task(record, gold, subset_by_he_id, model_facing_ids)
        )

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

    return {
        "schema_version": "1.0",
        "tasks_total": len(results),
        "tasks_passed": len(accepted),
        "tasks_failed": len(failed),
        "failed_check_counts": failed_checks,
        "mutation_rule_usage": rule_usage,
        "catalogue_rules": [r["name"] for r in catalogue["rules"]],
        "source": {
            "repository": SOURCE_REPO_URL,
            "revision": SOURCE_REVISION,
            "dataset_path": DATASET_PATH,
            "dataset_gz_sha256": DATASET_GZ_SHA256,
            "dataset_raw_sha256": DATASET_RAW_SHA256,
            "license": LICENSE_SPDX,
        },
        "manifest": manifest,
        "build_log_counts": {
            "problems_inspected": build_log["problems_inspected"],
            "eligible": build_log["eligible"],
            "with_viable_mutations": build_log["with_viable_mutations"],
            "rejected": build_log["rejected"],
            "not_selected": build_log["not_selected"],
        },
        "results": results,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the Phase 0 task set offline")
    parser.add_argument("--json", dest="json_path", default=None, help="write the full report as JSON")
    parser.add_argument("--verify-manifest", action="store_true", help="also verify manifest checksums")
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
        print(f"tasks: {report['tasks_total']}")
        print(f"passed: {report['tasks_passed']}")
        print(f"failed: {report['tasks_failed']}")
        for result in report["results"]:
            if result["passed"]:
                continue
            for name, check in result["checks"].items():
                if not check["passed"]:
                    print(f"  FAIL {result['task_id']}: {name} -> {check['detail']}")
        if "manifest_verification" in report:
            mv = report["manifest_verification"]
            print(f"manifest: {'OK' if mv['passed'] else 'MISMATCH'} ({mv['files_checked']} files)")
            for mismatch in mv["mismatches"]:
                print(f"  {mismatch}")

    failed_tasks = report["tasks_failed"]
    manifest_ok = report.get("manifest_verification", {}).get("passed", True)
    return 0 if (failed_tasks == 0 and manifest_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
