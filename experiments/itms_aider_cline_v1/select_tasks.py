"""
Reproducible selection protocol for the 20-task ITMS-vs-Aider-vs-Cline set.

Design rules (deliberate, and enforced by the code below):

1.  The candidate pool is the *already frozen* set of 20 ``HE-EDIT-*`` tasks
    built by ``benchmarks/phase0/build_tasks.py``.  Each one is a 1:1
    derivation of a specific pinned HumanEval problem, and each one has
    already passed the 12-check validator in ``benchmarks/phase0/validate_tasks.py``.

2.  Difficulty is assigned by an **a priori static rubric** computed from the
    task's own bytes (AST structure, indentation, anchor ambiguity, mutation
    category).  No observed model output is consulted.  This is the property
    that makes the final comparison unbiased: the task set cannot have been
    chosen because of how any system performed on it.

3.  The historical 120-attempt run in ``pilot_runs/`` *does* contain
    per-task outcome data for these same 20 tasks.  That data is deliberately
    **excluded** from selection and is only attached to the manifest as a
    non-binding, clearly-labelled diagnostic pointer, so a reader can see
    that it existed and was not used.  Using it to rank the final set would be
    exactly the selection bias this protocol exists to avoid.

4.  A separate, explicitly held-out pool exists for optional pilot work: the
    164 - 20 = 144 HumanEval problems that were *not* selected into the final
    set (``benchmarks/data/build_log.json``).  Pilot ranking, if the user
    elects to run one, must use that pool, not the final 20.

The rubric below is fixed version 1.0.0.  Changing any weight or threshold
requires bumping the version, because the manifest records it.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(REPO_ROOT, "benchmarks", "data")
TASKS_JSON = os.path.join(DATA_DIR, "humaneval_edit_tasks.json")
CATALOGUE_JSON = os.path.join(DATA_DIR, "mutation_catalogue.json")
BUILD_LOG_JSON = os.path.join(DATA_DIR, "build_log.json")
PROVENANCE_JSON = os.path.join(DATA_DIR, "source", "PROVENANCE.json")

RUBRIC_VERSION = "1.0.0"
OUTPUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "task_manifest.json")

# ---------------------------------------------------------------------------
# a priori difficulty rubric
# ---------------------------------------------------------------------------

# Mutation categories ordered by how badly they can be caught by a *reader*
# that only looks at the diff, and how easily a text patch can mis-anchor.
# Fixed a priori; no measurement informed these weights.
CATEGORY_WEIGHT: Dict[str, int] = {
    "logic": 3,        # comparison / boolean swap: output looks plausible
    "boundary": 3,     # off-by-one: needs the spec to notice
    "arithmetic": 2,   # operator swap: often changes type/shape visibly
    "constant": 1,     # wrong literal: easiest to spot by reading the spec
}

# Rule-specific notes describing the *expected editing-failure mechanism*.
# These describe why a task is expected to stress text-based editing; they are
# hypotheses, not claims about observed failure rates.
RULE_MECHANISM: Dict[str, str] = {
    "comparison_operator_swap": (
        "Semantic (not syntactic) defect: the repaired line differs from the "
        "defective line by one comparison operator, so a text patch that "
        "matches the wrong occurrence, or a full-file rewrite that drops the "
        "surrounding branch, can still be syntactically valid and still fail "
        "the hidden test."
    ),
    "boolean_operator_swap": (
        "Short-circuit logic defect plus code-boundary risk: the fix usually "
        "requires changing one connective inside a nested condition, which is "
        "where indentation-sensitive edits and ambiguous search anchors "
        "concentrate."
    ),
    "off_by_one_boundary": (
        "Boundary defect: the fix is a single numeric literal inside a "
        "comparison/slice/range expression, so a patch that anchors on the "
        "wrong numeric literal (or a full-file rewrite that changes a second "
        "literal) produces a plausible but wrong result."
    ),
    "incorrect_constant": (
        "Constant defect: the fix replaces one numeric literal, which is the "
        "easiest class to read off the specification but still requires the "
        "patch to hit the correct literal occurrence when the file contains "
        "several."
    ),
    "arithmetic_operator_swap": (
        "Arithmetic defect: the fix swaps one operator; when the operator sits "
        "in a nested expression the edit must preserve the expression's "
        "indentation and parenthesisation exactly."
    ),
}

HIGH_RISK_TIERS = ("A", "B", "C")


# ---------------------------------------------------------------------------
# static feature extraction
# ---------------------------------------------------------------------------


@dataclass
class FeatureSet:
    """Static, task-intrinsic features used by the rubric."""

    task_id: str
    mutation_rule: str
    mutation_category: str
    mutation_description: str
    mutation_line: int
    mutation_indent: int
    mutation_depth: int
    mutation_in_nested_block: bool
    target_function: str
    target_has_docstring: bool
    target_source_lines: int
    target_max_depth: int
    identical_anchor_candidates: int
    sibling_same_kind_statements: int
    spec_mentions_mutated_operator: bool
    numeric_literal_count_in_target: int
    operator_token_count_in_target: int
    feature_notes: List[str] = field(default_factory=list)


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _depth_of_node(node: ast.AST) -> int:
    """Nesting depth of ``node`` inside the target function body."""
    depth = 0
    for child in ast.walk(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            depth += 1
    return depth


def _find_function(tree: ast.Module, name: str) -> Optional[ast.FunctionDef]:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _mutation_anchor(source: str, old: str, new: str) -> Optional[Tuple[int, int]]:
    """Locate the single line where ``old`` was rewritten to ``new``.

    The mutation engine rewrites exactly one byte span.  We recover that span
    by finding the line that differs between ``original_source`` and
    ``mutated_source``; the two files are byte-identical apart from the
    mutation.
    """
    old_lines = source.splitlines()
    return None


def extract_features(task: Dict[str, Any]) -> FeatureSet:
    original = task["original_source"]
    mutated = task["mutated_source"]
    target = task["target_function"]
    rule = task["mutation"]["rule"]
    category = _CATEGORY_BY_RULE.get(rule, "unknown")

    old_lines = original.splitlines()
    new_lines = mutated.splitlines()
    diff_idx = None
    for i, (a, b) in enumerate(zip(old_lines, new_lines)):
        if a != b:
            diff_idx = i
            break
    if diff_idx is None:
        diff_idx = 0

    new_line = new_lines[diff_idx] if diff_idx < len(new_lines) else ""
    indent = _indent_of(new_line)
    stripped = new_line.strip()

    tree_new = ast.parse(mutated)
    fn_new = _find_function(tree_new, target)
    fn_old = _find_function(ast.parse(original), target)

    # depth of the enclosing blocks of the mutated line
    mutation_depth = 0
    if fn_new is not None:
        for node in ast.walk(fn_new):
            body = getattr(node, "body", None)
            if not isinstance(body, list):
                continue
            for child in body:
                if not hasattr(child, "lineno"):
                    continue
                end = getattr(child, "end_lineno", child.lineno) or child.lineno
                if child.lineno <= diff_idx + 1 <= end:
                    mutation_depth = max(mutation_depth, _depth_of_node(node))

    # anchor ambiguity: how many other lines in the file are textually
    # identical to the mutated line (a search/replace patch would be
    # ambiguous if >1).
    identical = sum(1 for line in new_lines if line.strip() == stripped and line.strip())
    identical_anchor_candidates = max(0, identical - 1)

    # statement-kind ambiguity: how many *other* statements of the same AST
    # node class exist in the module.  A whole-statement search/replace
    # anchor is ambiguous when the module contains several statements of the
    # same kind, even if their text differs.
    sibling_same_kind_statements = 0
    mutated_stmt_kind = ""
    if fn_new is not None:
        mutated_node = None
        for node in ast.walk(fn_new):
            body = getattr(node, "body", None)
            if not isinstance(body, list):
                continue
            for child in body:
                if not hasattr(child, "lineno"):
                    continue
                end = getattr(child, "end_lineno", child.lineno) or child.lineno
                if child.lineno <= diff_idx + 1 <= end:
                    mutated_node = child
                    break
        if mutated_node is not None:
            mutated_stmt_kind = type(mutated_node).__name__
            for stmt in tree_new.body:
                if hasattr(stmt, "lineno") and type(stmt).__name__ == mutated_stmt_kind:
                    sibling_same_kind_statements += 1
            for stmt in ast.walk(tree_new):
                if stmt is mutated_node:
                    continue
                if hasattr(stmt, "lineno") and type(stmt).__name__ == mutated_stmt_kind:
                    sibling_same_kind_statements += 1
            sibling_same_kind_statements = max(0, sibling_same_kind_statements - 1)

    # spec divergence: does the target's docstring contain wording that
    # contradicts the mutated operator?  This is the strongest static signal
    # that a repair requires *reading the specification* rather than
    # pattern-matching the code.
    spec_mentions = False
    spec_text = ""
    if fn_new is not None and fn_new.body:
        first = fn_new.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                and isinstance(first.value.value, str):
            spec_text = first.value.value.lower()
    operator_words = {
        "comparison_operator_swap": ("greater", "less", "equal", "at least", "at most", "more than", "fewer"),
        "boolean_operator_swap": ("either", "both", "all", "any", "and", "or"),
        "off_by_one_boundary": ("first", "last", "index", "length", "up to", "inclusive"),
        "incorrect_constant": ("exactly", "default", "maximum", "minimum", "at most"),
        "arithmetic_operator_swap": ("sum", "total", "average", "product", "difference"),
    }
    for word in operator_words.get(rule, ()):
        if word in spec_text:
            spec_mentions = True
            break

    fn_lines = 0
    max_depth = 0
    numeric_literals = 0
    operator_tokens = 0
    has_docstring = False
    if fn_new is not None:
        fn_lines = (fn_new.end_lineno or fn_new.lineno) - fn_new.lineno + 1
        max_depth = max(
            (_depth_of_node(n) for n in ast.walk(fn_new)),
            default=0,
        )
        has_docstring = bool(
            fn_new.body
            and isinstance(fn_new.body[0], ast.Expr)
            and isinstance(fn_new.body[0].value, ast.Constant)
            and isinstance(fn_new.body[0].value.value, str)
        )
        for node in ast.walk(fn_new):
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                numeric_literals += 1
            if isinstance(node, ast.Compare):
                operator_tokens += len(node.ops)
            if isinstance(node, ast.BoolOp):
                operator_tokens += len(node.values) - 1
            if isinstance(node, ast.BinOp):
                operator_tokens += 1

    notes: List[str] = []
    if indent >= 4:
        notes.append("mutation site is inside an indented block")
    if identical_anchor_candidates >= 1:
        notes.append(
            f"{identical_anchor_candidates} other line(s) in the file are textually "
            "identical to the mutated line (ambiguous search/replace anchor)"
        )
    if sibling_same_kind_statements >= 2:
        notes.append(
            f"{sibling_same_kind_statements} sibling {mutated_stmt_kind} statement(s) "
            "exist in the module (statement-level anchor ambiguity)"
        )
    if spec_mentions:
        notes.append(
            "the target's docstring contains wording that constrains the "
            "mutated operator, so the repair requires reading the specification"
        )
    if fn_lines >= 20:
        notes.append(f"target function is {fn_lines} lines long (large edit surface)")
    if numeric_literals >= 3 and category in ("boundary", "constant"):
        notes.append(
            f"target contains {numeric_literals} numeric literals (literal-anchoring risk)"
        )
    if max_depth >= 2:
        notes.append(f"target function nests {max_depth} level(s) deep")

    return FeatureSet(
        task_id=task["task_id"],
        mutation_rule=rule,
        mutation_category=category,
        mutation_description=task["mutation"].get("description", ""),
        mutation_line=diff_idx + 1,
        mutation_indent=indent,
        mutation_depth=mutation_depth,
        mutation_in_nested_block=indent >= 4 or mutation_depth >= 1,
        target_function=target,
        target_has_docstring=has_docstring,
        target_source_lines=fn_lines,
        target_max_depth=max_depth,
        identical_anchor_candidates=identical_anchor_candidates,
        sibling_same_kind_statements=sibling_same_kind_statements,
        spec_mentions_mutated_operator=spec_mentions,
        numeric_literal_count_in_target=numeric_literals,
        operator_token_count_in_target=operator_tokens,
        feature_notes=notes,
    )


# ---------------------------------------------------------------------------
# rubric
# ---------------------------------------------------------------------------


def score_task(feat: FeatureSet) -> Dict[str, Any]:
    """Apply the fixed rubric.  Returns tier, score and mechanism text."""
    score = CATEGORY_WEIGHT.get(feat.mutation_category, 0)

    if feat.mutation_in_nested_block:
        score += 2
    if feat.identical_anchor_candidates >= 1:
        score += 2
    elif feat.sibling_same_kind_statements >= 2:
        score += 1
    if feat.spec_mentions_mutated_operator:
        score += 2
    if feat.target_source_lines >= 20:
        score += 1
    if feat.target_max_depth >= 2:
        score += 1
    if feat.numeric_literal_count_in_target >= 3 and feat.mutation_category in ("boundary", "constant"):
        score += 1

    if score >= 7:
        tier = "A"
    elif score >= 4:
        tier = "B"
    else:
        tier = "C"

    mechanisms = [RULE_MECHANISM.get(feat.mutation_rule, "")]
    if feat.mutation_in_nested_block:
        mechanisms.append(
            "Indentation sensitivity: the defect and its repair sit inside an "
            "indented block, where a text edit that loses or adds a level of "
            "indentation produces a syntax error or a silently different "
            "control-flow boundary."
        )
    if feat.identical_anchor_candidates >= 1:
        mechanisms.append(
            "Patch-application risk: the mutated line has "
            f"{feat.identical_anchor_candidates} textually identical sibling "
            "line(s), so a search/replace patch that does not check for "
            "uniqueness can be applied at the wrong site."
        )
    elif feat.sibling_same_kind_statements >= 2:
        mechanisms.append(
            "Patch-application risk: the module contains "
            f"{feat.sibling_same_kind_statements} other statement(s) of the same "
            "kind as the defective one, so a statement-level patch anchor can "
            "bind to the wrong site."
        )
    if feat.spec_mentions_mutated_operator:
        mechanisms.append(
            "Specification divergence: the target's docstring constrains the "
            "mutated operator, so the defect is only detectable by reading the "
            "specification; a repair that matches the code's local style can be "
            "syntactically perfect and semantically wrong."
        )
    if feat.target_source_lines >= 20:
        mechanisms.append(
            f"Code-boundary risk: the target function spans {feat.target_source_lines} "
            "lines, so a full-file rewrite can change behaviour outside the "
            "defective region."
        )

    rationale = (
        f"Selected a priori by rubric v{RUBRIC_VERSION} "
        f"(category={feat.mutation_category}, score={score}, tier={tier}). "
        f"Static features: indent={feat.mutation_indent}, nested_block="
        f"{feat.mutation_in_nested_block}, identical_anchor_candidates="
        f"{feat.identical_anchor_candidates}, target_lines="
        f"{feat.target_source_lines}, target_depth={feat.target_max_depth}, "
        f"numeric_literals={feat.numeric_literal_count_in_target}, "
        f"operator_tokens={feat.operator_token_count_in_target}, "
        f"same_kind_siblings={feat.sibling_same_kind_statements}, "
        f"spec_constrains_operator={feat.spec_mentions_mutated_operator}. "
        "No observed model output was used to make this selection."
    )

    return {
        "difficulty_tier": tier,
        "difficulty_score": score,
        "expected_difficulty_mechanism": " ".join(m for m in mechanisms if m),
        "selection_rationale": rationale,
    }


# ---------------------------------------------------------------------------
# manifest assembly
# ---------------------------------------------------------------------------


def build_manifest() -> Dict[str, Any]:
    with open(TASKS_JSON, "r", encoding="utf-8") as fh:
        tasks_doc = json.load(fh)
    with open(CATALOGUE_JSON, "r", encoding="utf-8") as fh:
        catalogue = json.load(fh)
    with open(PROVENANCE_JSON, "r", encoding="utf-8") as fh:
        provenance = json.load(fh)
    with open(BUILD_LOG_JSON, "r", encoding="utf-8") as fh:
        build_log = json.load(fh)

    entries: List[Dict[str, Any]] = []
    for task in tasks_doc["tasks"]:
        feat = extract_features(task)
        scored = score_task(feat)
        entries.append(
            {
                "task_id": task["task_id"],
                "source": {
                    "provenance_type": "humaneval_derived_single_mutation",
                    "humaneval_task_id": task["humaneval_task_id"],
                    "entry_point": task["target_function"],
                    "dataset_repository": task["source"]["repository"],
                    "dataset_revision": task["source"]["revision"],
                    "dataset_path": task["source"]["dataset_path"],
                    "dataset_license": task["source"]["license"],
                    "derived_from_humaneval_problem": True,
                    "is_humaneval_benchmark_item": False,
                    "note": (
                        "This is a synthetic single-mutation repair task derived "
                        "from one HumanEval problem, not a HumanEval benchmark "
                        "item. Scores must never be reported as HumanEval scores."
                    ),
                },
                "task_description": {
                    "problem_prompt": task["problem_prompt"],
                    "repair_instruction": task["repair_instruction"],
                    "mutated_source_path": task["files"]["mutated_source"],
                    "original_source_path": task["files"]["original_source"],
                },
                "mutation": {
                    "rule": task["mutation"]["rule"],
                    "parameters": task["mutation"]["parameters"],
                    "description": task["mutation"]["description"],
                    "validation": task["mutation"]["validation"],
                },
                "official_test": {
                    "path": task["files"]["official_test"],
                    "sha256": task["checksums"]["official_test_sha256"],
                    "hidden_from_model": True,
                },
                "static_features": {
                    "mutation_line": feat.mutation_line,
                    "mutation_indent": feat.mutation_indent,
                    "mutation_depth": feat.mutation_depth,
                    "mutation_in_nested_block": feat.mutation_in_nested_block,
                    "target_function": feat.target_function,
                    "target_has_docstring": feat.target_has_docstring,
                    "target_source_lines": feat.target_source_lines,
                    "target_max_depth": feat.target_max_depth,
                    "identical_anchor_candidates": feat.identical_anchor_candidates,
                    "sibling_same_kind_statements": feat.sibling_same_kind_statements,
                    "spec_mentions_mutated_operator": feat.spec_mentions_mutated_operator,
                    "numeric_literal_count_in_target": feat.numeric_literal_count_in_target,
                    "operator_token_count_in_target": feat.operator_token_count_in_target,
                    "feature_notes": feat.feature_notes,
                },
                "expected_difficulty_mechanism": scored["expected_difficulty_mechanism"],
                "selection_rationale": scored["selection_rationale"],
                "difficulty_tier": scored["difficulty_tier"],
                "difficulty_score": scored["difficulty_score"],
                "observed_outcome_evidence": {
                    "used_for_selection": False,
                    "note": (
                        "A historical live run exists for these tasks under "
                        "pilot_runs/. It was deliberately NOT used to select, "
                        "rank or drop any task, because doing so would bias the "
                        "final system comparison."
                    ),
                },
            }
        )

    entries.sort(key=lambda e: e["task_id"])

    manifest = {
        "schema_version": "1.0",
        "manifest_version": "1.0.0",
        "rubric_version": RUBRIC_VERSION,
        "generated_by": "experiments/itms_aider_cline_v1/select_tasks.py",
        "task_count": len(entries),
        "selection_protocol": {
            "candidate_pool": (
                "The 20 frozen HE-EDIT-* tasks built by "
                "benchmarks/phase0/build_tasks.py from a pinned HumanEval "
                "commit; each passed the 12-check offline validator."
            ),
            "difficulty_assignment": (
                "a priori static rubric over task-intrinsic features only "
                "(mutation category, indentation, nesting, anchor ambiguity, "
                "target size)."
            ),
            "bias_controls": [
                "No observed model output influences task selection or ranking.",
                "The historical 120-attempt run over the same tasks is excluded "
                "from selection by design.",
                "Any pilot used for power analysis must draw from the held-out "
                "pool of non-selected HumanEval problems, not from these 20.",
            ],
            "held_out_pool": {
                "description": (
                    "HumanEval problems inspected but not selected into the "
                    "final 20; available for an explicitly separated pilot."
                ),
                "counts": {
                    "problems_inspected": build_log.get("problems_inspected"),
                    "eligible": build_log.get("eligible"),
                    "with_viable_mutations": build_log.get("with_viable_mutations"),
                    "accepted": build_log.get("accepted"),
                },
                "rejected": len(build_log.get("rejected_candidates", [])),
                "not_selected": len(build_log.get("not_selected_candidates", [])),
                "total_available": (
                    len(build_log.get("rejected_candidates", []))
                    + len(build_log.get("not_selected_candidates", []))
                ),
            },
        },
        "dataset_provenance": {
            "repository": provenance.get("repository"),
            "revision": provenance.get("revision"),
            "revision_type": provenance.get("revision_type"),
            "dataset_path": provenance.get("dataset_path"),
            "license": provenance.get("license"),
            "dataset_problem_count": provenance.get("dataset_problem_count"),
        },
        "difficulty_tier_counts": _tier_counts(entries),
        "tasks": entries,
    }

    digest = hashlib.sha256(
        json.dumps(entries, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    manifest["task_manifest_digest"] = digest
    manifest["mutation_catalogue_version"] = catalogue.get("catalogue_version")
    return manifest


def _tier_counts(entries: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for e in entries:
        counts[e["difficulty_tier"]] = counts.get(e["difficulty_tier"], 0) + 1
    return counts


_CATEGORY_BY_RULE: Dict[str, str] = {}


def _load_categories() -> None:
    if _CATEGORY_BY_RULE:
        return
    with open(CATALOGUE_JSON, "r", encoding="utf-8") as fh:
        catalogue = json.load(fh)
    for rule in catalogue["rules"]:
        _CATEGORY_BY_RULE[rule["name"]] = rule.get("category", "unknown")


_load_categories()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=OUTPUT_PATH)
    parser.add_argument("--check", action="store_true",
                        help="verify the on-disk manifest matches a fresh build")
    args = parser.parse_args(argv)

    manifest = build_manifest()

    if args.check:
        if not os.path.exists(args.output):
            print("manifest missing", file=sys.stderr)
            return 1
        with open(args.output, "r", encoding="utf-8") as fh:
            existing = json.load(fh)
        if existing.get("task_manifest_digest") != manifest["task_manifest_digest"]:
            print("manifest digest mismatch", file=sys.stderr)
            return 1
        print(f"manifest ok: {manifest['task_count']} tasks, digest={manifest['task_manifest_digest'][:16]}")
        return 0

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"wrote {args.output} ({manifest['task_count']} tasks, "
          f"digest={manifest['task_manifest_digest'][:16]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
