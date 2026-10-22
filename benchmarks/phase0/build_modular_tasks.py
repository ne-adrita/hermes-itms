"""
Phase 0 builder for the *modular* task family.

Why a second family
-------------------
The HumanEval-derived family that Phase 0 built consists of **single-function
modules**: ``benchmarks/phase0/dataset.py::build_source_file`` composes the
reference file as ``HumanEval prompt + canonical solution``, so the file is
the target function and almost nothing else.  Such a file is 10-30 lines
long, contains one function and no class, and its target function occupies
~96 % of it.  No task of that family can satisfy the pre-registered
representative-task rule in :mod:`benchmarks.phase2.task_selection`
(``file_lines >= 200``, ``target_ratio <= 0.20``, ``function_count >= 5``,
``class_count >= 1``), which is why 0 of 20 qualify.

This builder creates a **second, independent** task family from
*authored* multi-function modules.  It reuses the existing Phase 0
machinery unchanged:

*   :mod:`benchmarks.phase0.mutation_engine` produces the defect,
*   :mod:`benchmarks.phase0.eligibility` decides whether a target may be
    repaired in isolation,
*   :mod:`benchmarks.phase0.runner` is the **official evaluator** that
    decides whether the repair is correct,
*   the task layout, the manifest and the model-facing payload use the same
    schema as the HumanEval family, so
    :class:`benchmarks.benchmark.tasks.TaskRepository` loads either family
    without a code change.

The existing HumanEval family is **not** touched: this builder writes into
``benchmarks/data/modular/`` only.

The builder is completely offline.  It makes no network request and no
model call.

Usage::

    python benchmarks/phase0/build_modular_tasks.py --tasks 20
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from eligibility import check_self_containment, docstring_of  # noqa: E402
from mutation_engine import (  # noqa: E402
    MUTATION_RULES,
    apply_mutation,
    enumerate_candidate_mutations,
)
from runner import run_official_test  # noqa: E402

DATA_DIR = os.path.join(_ROOT, "benchmarks", "data", "modular")
SOURCE_DIR = os.path.join(DATA_DIR, "source")
CHECKS_DIR = os.path.join(DATA_DIR, "checks")
TASKS_DIR = os.path.join(DATA_DIR, "tasks")

TASKS_FILE = os.path.join(DATA_DIR, "modular_edit_tasks.json")
GOLD_FILE = os.path.join(DATA_DIR, "gold_solutions.json")
MODEL_FACING_FILE = os.path.join(DATA_DIR, "model_facing_tasks.json")
CATALOGUE_FILE = os.path.join(DATA_DIR, "mutation_catalogue.json")
MANIFEST_FILE = os.path.join(DATA_DIR, "manifest.json")
BUILD_LOG_FILE = os.path.join(DATA_DIR, "build_log.json")

#: The instruction is byte-identical to the one used by the HumanEval family,
#: so every editing method receives exactly the same instruction.
REPAIR_INSTRUCTION = (
    "The Python module below contains the function `{target}`, whose docstring "
    "specifies its intended behaviour. The current implementation of `{target}` "
    "is defective: it does not satisfy the specification. Repair the function "
    "so that it behaves exactly as specified.\n"
    "Constraints:\n"
    "- Keep the function name and its signature unchanged.\n"
    "- Do not modify the docstring.\n"
    "- Do not change the behaviour of any other part of the module.\n"
    "- Return only the repaired function (or the repaired module)."
)

MAX_OCCURRENCES = 3
TEST_TIMEOUT = 25.0

#: The task-id prefix of the new family.
TASK_ID_PREFIX = "MOD-EDIT"


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# Authored inputs
# --------------------------------------------------------------------------

@dataclass
class TargetCandidate:
    """One authored (module, target function) pair."""

    module: str
    target_function: str
    original_source: str
    official_test: str
    problem_prompt: str
    eligible: bool
    eligibility_reasons: List[str] = field(default_factory=list)
    gold_passes: bool = False
    gold_outcome: Dict[str, Any] = field(default_factory=dict)
    viable_mutations: List[Dict[str, Any]] = field(default_factory=list)
    mutation_stats: Dict[str, int] = field(default_factory=dict)
    rejection_reason: Optional[str] = None

    @property
    def key(self) -> str:
        return f"{self.module}.{self.target_function}"


def _official_test_for(check_source: str, target_function: str) -> Optional[str]:
    """
    Return the official test of one target.

    The check file is self-contained and defines ``check_<target>``.  The
    official test is that file with the one function renamed to ``check``,
    which is the name the official evaluator calls.
    """
    try:
        tree = ast.parse(check_source)
    except SyntaxError:
        return None
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name != f"check_{target_function}":
            continue
        segment = ast.get_source_segment(check_source, node)
        if segment is None:
            return None
        old = f"def check_{target_function}(candidate: Any) -> None:"
        new = "def check(candidate):"
        if old in segment:
            segment = segment.replace(old, new)
        else:
            # The signature may have been written differently; rename the
            # definition itself and keep the rest of the signature.
            segment = segment.replace(
                f"def check_{target_function}(", "def check(", 1
            )
        return segment
    return None


def _module_targets(source: str) -> List[str]:
    """Return the top-level function names of a module, in source order."""
    tree = ast.parse(source)
    return [
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _collect_candidates() -> List[TargetCandidate]:
    """Read the authored modules and their checks, and evaluate every target."""
    candidates: List[TargetCandidate] = []
    for module in sorted(
        f[:-3] for f in os.listdir(SOURCE_DIR) if f.endswith(".py")
    ):
        original_source = _read(os.path.join(SOURCE_DIR, f"{module}.py"))
        check_path = os.path.join(CHECKS_DIR, f"{module}.py")
        if not os.path.exists(check_path):
            raise SystemExit(f"missing check file for module {module}: {check_path}")
        check_source = _read(check_path)

        for target_function in _module_targets(original_source):
            official_test = _official_test_for(check_source, target_function)
            if official_test is None:
                # The module has no check for this function: it is context,
                # not a target.
                continue
            eligibility = check_self_containment(original_source, target_function)
            gold_outcome = run_official_test(
                original_source, official_test, target_function, timeout=TEST_TIMEOUT
            )
            tree = ast.parse(original_source)
            prompt = docstring_of(tree, target_function) or ""
            candidates.append(
                TargetCandidate(
                    module=module,
                    target_function=target_function,
                    original_source=original_source,
                    official_test=official_test,
                    problem_prompt=prompt,
                    eligible=eligibility.eligible,
                    eligibility_reasons=list(eligibility.reasons),
                    gold_passes=gold_outcome.passed,
                    gold_outcome=gold_outcome.to_dict(),
                )
            )
    return candidates


def _explore_mutations(candidate: TargetCandidate) -> TargetCandidate:
    """Keep the mutations that actually make the official check fail."""
    specs = enumerate_candidate_mutations(
        candidate.original_source,
        candidate.target_function,
        max_occurrences=MAX_OCCURRENCES,
    )
    stats: Dict[str, int] = {}
    for spec in specs:
        stats[spec.rule] = stats.get(spec.rule, 0) + 1
        try:
            mutated, applied = apply_mutation(
                candidate.original_source,
                candidate.target_function,
                spec.rule,
                spec.params,
            )
        except Exception:
            continue
        outcome = run_official_test(
            mutated, candidate.official_test, candidate.target_function, timeout=TEST_TIMEOUT
        )
        if outcome.passed:
            # The mutation is behaviour-preserving for this task: it would
            # not be a defect at all.
            continue
        if outcome.timed_out:
            # A mutation that makes the check hang is not a deterministic
            # repair target.
            continue
        candidate.viable_mutations.append(
            {
                "spec": spec.to_dict(),
                "mutated_source": mutated,
                "mutation_outcome": outcome.to_dict(),
                "expected_change": describe_change(
                    candidate.original_source, mutated
                ),
            }
        )
    candidate.mutation_stats = stats
    return candidate


def describe_change(original: str, mutated: str) -> Dict[str, Any]:
    """Describe the byte-local difference between the original and the mutant."""
    original_lines = original.splitlines()
    mutated_lines = mutated.splitlines()
    if len(original_lines) != len(mutated_lines):
        return {
            "line": None,
            "before": None,
            "after": None,
            "changed_line_count": None,
            "note": "the mutation changed the number of lines",
        }
    changed = [
        index
        for index, (left, right) in enumerate(zip(original_lines, mutated_lines))
        if left != right
    ]
    if not changed:
        return {
            "line": None,
            "before": None,
            "after": None,
            "changed_line_count": 0,
            "note": "the mutation changed no line",
        }
    first = changed[0]
    return {
        "line": first + 1,
        "before": original_lines[first],
        "after": mutated_lines[first],
        "changed_line_count": len(changed),
        "note": "the defect is confined to the listed line",
    }


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------

def select_candidates(
    candidates: Sequence[TargetCandidate],
    target_count: int,
) -> Tuple[List[Tuple[TargetCandidate, Dict[str, Any]]], List[Dict[str, Any]]]:
    """
    Select the tasks deterministically, maximising mutation-rule diversity.

    The policy is the same round-robin the HumanEval builder uses: for each
    mutation rule, in the fixed order of :data:`MUTATION_RULES`, the first
    not-yet-used target with a viable mutation under that rule is taken.
    The rule order is fixed in version-controlled code and the candidate
    order is the sorted (module, target) order, so the selection depends on
    no model result, no pass rate and no timing.
    """
    usable = [
        c
        for c in candidates
        if c.eligible and c.gold_passes and c.viable_mutations
    ]
    usable.sort(key=lambda c: c.key)

    by_rule: Dict[str, List[TargetCandidate]] = {r.name: [] for r in MUTATION_RULES}
    for candidate in usable:
        seen_rules = set()
        for mutation in candidate.viable_mutations:
            rule = mutation["spec"]["rule"]
            if rule in seen_rules:
                continue
            seen_rules.add(rule)
            by_rule[rule].append(candidate)

    selected: List[Tuple[TargetCandidate, Dict[str, Any]]] = []
    used: set = set()
    exhausted: set = set()

    while len(selected) < target_count:
        progressed = False
        for rule in MUTATION_RULES:
            if len(selected) >= target_count:
                break
            if rule.name in exhausted:
                continue
            chosen = None
            for candidate in by_rule[rule.name]:
                if candidate.key in used:
                    continue
                mutation = next(
                    m
                    for m in candidate.viable_mutations
                    if m["spec"]["rule"] == rule.name
                )
                chosen = (candidate, mutation)
                break
            if chosen is None:
                exhausted.add(rule.name)
                continue
            used.add(chosen[0].key)
            selected.append(chosen)
            progressed = True
        if not progressed:
            break

    skipped = []
    for candidate in usable:
        if candidate.key in used:
            continue
        skipped.append(
            {
                "module": candidate.module,
                "target_function": candidate.target_function,
                "reason": "not_selected_by_rule_diversity_round_robin",
                "viable_mutations_found": len(candidate.viable_mutations),
            }
        )
    return selected, skipped


# --------------------------------------------------------------------------
# Artefact writing
# --------------------------------------------------------------------------

def _write_task_files(task_id: str, record: Dict[str, Any]) -> Dict[str, str]:
    task_dir = os.path.join(TASKS_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)
    paths = {
        "prompt": os.path.join(task_dir, "prompt.txt"),
        "official_test": os.path.join(task_dir, "official_test.py"),
        "original_source": os.path.join(task_dir, "original_source.py"),
        "mutated_source": os.path.join(task_dir, "mutated_source.py"),
    }
    with open(paths["prompt"], "w", encoding="utf-8") as fh:
        fh.write(record["problem_prompt"])
    with open(paths["official_test"], "w", encoding="utf-8") as fh:
        fh.write(record["official_test_source"])
    with open(paths["original_source"], "w", encoding="utf-8") as fh:
        fh.write(record["original_source"])
    with open(paths["mutated_source"], "w", encoding="utf-8") as fh:
        fh.write(record["mutated_source"])
    return {key: os.path.relpath(path, _ROOT) for key, path in paths.items()}


def _model_facing_view(record: Dict[str, Any]) -> Dict[str, Any]:
    """Project a task record onto the fields a model is allowed to see."""
    return {
        "task_id": record["task_id"],
        "humaneval_task_id": record["humaneval_task_id"],
        "target_function": record["target_function"],
        "problem_prompt": record["problem_prompt"],
        "repair_instruction": record["repair_instruction"],
        "mutated_source": record["mutated_source"],
        "source": record["source"],
        "checksums": {
            "mutated_source_sha256": record["checksums"]["mutated_source_sha256"],
            "problem_prompt_sha256": record["checksums"]["problem_prompt_sha256"],
        },
    }


def build(target_count: int = 20) -> Dict[str, Any]:
    """Build the modular task family.  Returns a summary dictionary."""
    candidates = _collect_candidates()
    explored = [_explore_mutations(c) for c in candidates]
    explored.sort(key=lambda c: c.key)

    selected, skipped = select_candidates(explored, target_count)

    records: List[Dict[str, Any]] = []
    golds: Dict[str, str] = {}
    for index, (candidate, mutation) in enumerate(selected, start=1):
        task_id = f"{TASK_ID_PREFIX}-{index:03d}"
        spec = mutation["spec"]
        record = {
            "task_id": task_id,
            # This family is not derived from HumanEval.  The field keeps the
            # schema of the existing family so that the task repository can
            # load both; the value names the authored module.
            "humaneval_task_id": f"modular/{candidate.module}",
            "target_function": candidate.target_function,
            "module": candidate.module,
            "source": {
                "origin": "authored_for_this_project",
                "module": candidate.module,
                "module_path": os.path.relpath(
                    os.path.join(SOURCE_DIR, f"{candidate.module}.py"), _ROOT
                ),
                "license": "project-internal",
                "note": (
                    "This module was written for this project. It is not part "
                    "of the HumanEval dataset and no HumanEval score can be "
                    "derived from it."
                ),
            },
            "problem_prompt": candidate.problem_prompt,
            "official_test_source": candidate.official_test,
            "official_test_hidden_from_model": True,
            "original_source": candidate.original_source,
            "mutated_source": mutation["mutated_source"],
            "mutation": {
                "rule": spec["rule"],
                "parameters": spec["parameters"],
                "description": next(
                    r.description for r in MUTATION_RULES if r.name == spec["rule"]
                ),
                "validation": mutation["mutation_outcome"],
                "expected_change": mutation["expected_change"],
            },
            "repair_instruction": REPAIR_INSTRUCTION.format(
                target=candidate.target_function
            ),
            "gold_solution_reference": "benchmarks/data/modular/gold_solutions.json",
            "checksums": {
                "problem_prompt_sha256": _sha256_text(candidate.problem_prompt),
                "official_test_sha256": _sha256_text(candidate.official_test),
                "original_source_sha256": _sha256_text(candidate.original_source),
                "mutated_source_sha256": _sha256_text(mutation["mutated_source"]),
            },
            "eligibility": {
                "eligible": candidate.eligible,
                "reasons": candidate.eligibility_reasons,
            },
        }
        record["files"] = _write_task_files(task_id, record)
        records.append(record)
        golds[task_id] = candidate.original_source

    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.isdir(TASKS_DIR):
        shutil.rmtree(TASKS_DIR)
    for record in records:
        _write_task_files(record["task_id"], record)

    with open(TASKS_FILE, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "schema_version": "1.0",
                "task_set": "modular_edit",
                "description": (
                    "Synthetic repair tasks on authored multi-function modules. "
                    "Each task is a module of at least 200 lines with at least "
                    "five functions and one class, in which a single small "
                    "function carries a deterministic defect. These are NOT "
                    "HumanEval tasks and must not be reported as HumanEval "
                    "scores."
                ),
                "tasks": records,
            },
            fh,
            indent=2,
            ensure_ascii=False,
        )
        fh.write("\n")

    with open(GOLD_FILE, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "schema_version": "1.0",
                "warning": (
                    "REFERENCE SOLUTIONS. Never expose these to a model, and "
                    "never include this file in a model prompt."
                ),
                "gold_solutions": golds,
            },
            fh,
            indent=2,
            ensure_ascii=False,
        )
        fh.write("\n")

    with open(MODEL_FACING_FILE, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "schema_version": "1.0",
                "description": (
                    "The exact payload that may be shown to a model. It "
                    "contains no gold solution and no mutation metadata."
                ),
                "tasks": [_model_facing_view(r) for r in records],
            },
            fh,
            indent=2,
            ensure_ascii=False,
        )
        fh.write("\n")

    with open(CATALOGUE_FILE, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "schema_version": "1.0",
                "catalogue_version": "1.0.0",
                "deterministic": True,
                "selection_policy": (
                    "For a given (source, target function) the candidate "
                    "mutations are enumerated in a fixed order: rules in the "
                    "order listed below, and within a rule, occurrences in "
                    "ascending source order. A mutation is accepted for a "
                    "task only if it keeps the module syntactically valid and "
                    "makes the official check() fail."
                ),
                "rules": [r.to_dict() for r in MUTATION_RULES],
            },
            fh,
            indent=2,
            ensure_ascii=False,
        )
        fh.write("\n")

    rejected = []
    for candidate in explored:
        if candidate.eligible and candidate.gold_passes and candidate.viable_mutations:
            continue
        if not candidate.eligible:
            reason = "; ".join(candidate.eligibility_reasons)
        elif not candidate.gold_passes:
            reason = (
                "reference_module_failed_the_official_check: "
                f"{candidate.gold_outcome.get('error_type')}"
            )
        else:
            reason = "no_mutation_in_the_catalogue_made_the_official_check_fail"
        rejected.append(
            {
                "module": candidate.module,
                "target_function": candidate.target_function,
                "reason": reason,
                "eligibility_reasons": candidate.eligibility_reasons,
                "gold_outcome": candidate.gold_outcome,
            }
        )

    rule_usage: Dict[str, int] = {}
    for record in records:
        rule = record["mutation"]["rule"]
        rule_usage[rule] = rule_usage.get(rule, 0) + 1

    build_log = {
        "schema_version": "1.0",
        "generated_by": "benchmarks/phase0/build_modular_tasks.py",
        "modules": sorted({c.module for c in explored}),
        "targets_inspected": len(explored),
        "eligible": sum(1 for c in explored if c.eligible),
        "gold_passed": sum(1 for c in explored if c.gold_passes),
        "with_viable_mutations": sum(
            1
            for c in explored
            if c.eligible and c.gold_passes and c.viable_mutations
        ),
        "accepted": len(records),
        "rejected": len(rejected),
        "not_selected": len(skipped),
        "mutation_rule_usage": rule_usage,
        "rejected_candidates": rejected,
        "not_selected_candidates": skipped,
        "per_target": [
            {
                "module": c.module,
                "target_function": c.target_function,
                "eligible": c.eligible,
                "eligibility_reasons": c.eligibility_reasons,
                "gold_passes_official_check": c.gold_passes,
                "viable_mutations_found": len(c.viable_mutations),
                "mutation_candidates_evaluated": c.mutation_stats,
            }
            for c in explored
        ],
    }
    with open(BUILD_LOG_FILE, "w", encoding="utf-8") as fh:
        json.dump(build_log, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    _write_manifest(records)

    return {
        "accepted": len(records),
        "rejected": len(rejected),
        "not_selected": len(skipped),
        "targets_inspected": len(explored),
        "mutation_rule_usage": rule_usage,
    }


def _write_manifest(records: List[Dict[str, Any]]) -> None:
    """Write the manifest that pins every artefact by checksum."""
    import datetime

    files = {}
    for path in (
        TASKS_FILE,
        GOLD_FILE,
        MODEL_FACING_FILE,
        CATALOGUE_FILE,
        BUILD_LOG_FILE,
    ):
        files[os.path.relpath(path, _ROOT)] = {
            "sha256": _sha256_text(_read(path)),
            "bytes": os.path.getsize(path),
        }

    task_files = {}
    for record in records:
        task_files[record["task_id"]] = {}
        for key, rel in record["files"].items():
            full = os.path.join(_ROOT, rel)
            task_files[record["task_id"]][key] = {
                "path": rel,
                "sha256": _sha256_text(_read(full)),
            }

    manifest = {
        "schema_version": "1.0",
        "manifest_version": "1.0.0",
        "task_set": "modular_edit",
        "generated_at_utc": datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "generator": "benchmarks/phase0/build_modular_tasks.py",
        "python_version": sys.version.split()[0],
        "counts": {
            "tasks": len(records),
            "mutation_rules": len(MUTATION_RULES),
        },
        "files": files,
        "task_files": task_files,
        "task_checksums": {r["task_id"]: r["checksums"] for r in records},
        "verify_command": (
            "python benchmarks/phase0/validate_modular_tasks.py "
            "--verify-manifest --json benchmarks/data/modular/validation_report.json"
        ),
    }
    with open(MANIFEST_FILE, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the modular (multi-function) Phase 0 task family"
    )
    parser.add_argument("--tasks", type=int, default=20)
    args = parser.parse_args(argv)
    summary = build(target_count=args.tasks)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
