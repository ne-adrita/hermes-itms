"""
Phase 0 task builder.

Builds the offline task set for the ITMS editing benchmark from the pinned
HumanEval dataset.  For every candidate problem the builder:

1.  composes the reference source file (prompt + canonical solution),
2.  applies the AST eligibility rules (:mod:`eligibility`),
3.  runs the official ``check`` test against the reference solution,
4.  enumerates the deterministic mutation catalogue
    (:mod:`mutation_engine`) and keeps the mutations that actually make the
    official test fail,
5.  selects a deterministic, rule-diverse subset of tasks,
6.  writes the Phase 0 artefacts and the manifest.

The builder is offline with respect to models: it never contacts an inference
API.  It does need network access *once* to fetch the pinned dataset, and it
verifies the pinned checksums before using anything.

Usage::

    python benchmarks/phase0/build_tasks.py --tasks 20
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from dataset import (  # noqa: E402
    DATASET_GZ_SHA256,
    DATASET_PATH,
    DATASET_PROBLEM_COUNT,
    DATASET_RAW_SHA256,
    LICENSE_FILE_PATH,
    LICENSE_FILE_SHA256,
    LICENSE_SPDX,
    SOURCE_REPO_HEAD,
    SOURCE_REPO_URL,
    SOURCE_REVISION,
    ProvenanceError,
    build_source_file,
    sha256_bytes,
    fetch_license,
    fetch_pinned_dataset,
    load_problems,
    problem_to_dict,
    sha256_text,
)
from eligibility import check_self_containment  # noqa: E402
from mutation_engine import MUTATION_RULES, MutationSpec, apply_mutation  # noqa: E402
from runner import run_official_test  # noqa: E402

DATA_DIR = os.path.join(_ROOT, "benchmarks", "data")
SOURCE_DIR = os.path.join(DATA_DIR, "source")
TASKS_DIR = os.path.join(DATA_DIR, "tasks")
REPORT_DIR = os.path.join(_ROOT, "docs")

TASKS_FILE = os.path.join(DATA_DIR, "humaneval_edit_tasks.json")
GOLD_FILE = os.path.join(DATA_DIR, "gold_solutions.json")
MODEL_FACING_FILE = os.path.join(DATA_DIR, "model_facing_tasks.json")
CATALOGUE_FILE = os.path.join(DATA_DIR, "mutation_catalogue.json")
MANIFEST_FILE = os.path.join(DATA_DIR, "manifest.json")
BUILD_LOG_FILE = os.path.join(DATA_DIR, "build_log.json")
SUBSET_FILE = os.path.join(SOURCE_DIR, "humaneval_subset.jsonl")
PROVENANCE_FILE = os.path.join(SOURCE_DIR, "PROVENANCE.json")
LICENSE_FILE = os.path.join(SOURCE_DIR, "humaneval_LICENSE.txt")

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


# --------------------------------------------------------------------------
# Candidate exploration
# --------------------------------------------------------------------------

@dataclass
class ProblemCandidate:
    """A single HumanEval problem considered during Phase 0."""

    task_id: str
    entry_point: str
    prompt: str
    official_test: str
    canonical_solution: str
    original_source: str
    eligible: bool
    eligibility_reasons: List[str]
    eligibility_details: Dict[str, Any]
    gold_passes: bool
    gold_outcome: Dict[str, Any]
    viable_mutations: List[Dict[str, Any]] = field(default_factory=list)
    mutation_stats: Dict[str, int] = field(default_factory=dict)
    rejection_reason: Optional[str] = None

    def to_log_dict(self) -> Dict[str, Any]:
        return {
            "humaneval_task_id": self.task_id,
            "entry_point": self.entry_point,
            "eligible": self.eligible,
            "eligibility_reasons": self.eligibility_reasons,
            "gold_passes_official_test": self.gold_passes,
            "gold_outcome": self.gold_outcome,
            "viable_mutations_found": len(self.viable_mutations),
            "mutation_candidates_evaluated": self.mutation_stats,
            "rejection_reason": self.rejection_reason,
        }


def _evaluate_problem(problem: Dict[str, Any]) -> ProblemCandidate:
    source = build_source_file(problem)
    eligibility = check_self_containment(source, problem["entry_point"])

    gold_outcome = run_official_test(
        source,
        problem["test"],
        problem["entry_point"],
        timeout=TEST_TIMEOUT,
    )

    candidate = ProblemCandidate(
        task_id=problem["task_id"],
        entry_point=problem["entry_point"],
        prompt=problem["prompt"],
        official_test=problem["test"],
        canonical_solution=problem["canonical_solution"],
        original_source=source,
        eligible=eligibility.eligible,
        eligibility_reasons=list(eligibility.reasons),
        eligibility_details=eligibility.details,
        gold_passes=gold_outcome.passed,
        gold_outcome=gold_outcome.to_dict(),
    )
    return candidate


def _explore_mutations(candidate: ProblemCandidate) -> ProblemCandidate:
    """Apply the mutation catalogue to one candidate and keep what fails."""
    from mutation_engine import enumerate_candidate_mutations

    specs = enumerate_candidate_mutations(
        candidate.original_source, candidate.entry_point, max_occurrences=MAX_OCCURRENCES
    )
    stats: Dict[str, int] = {}
    for spec in specs:
        stats[spec.rule] = stats.get(spec.rule, 0) + 1
        try:
            mutated, applied = apply_mutation(
                candidate.original_source, candidate.entry_point, spec.rule, spec.params
            )
        except Exception:
            continue
        outcome = run_official_test(
            mutated, candidate.official_test, candidate.entry_point, timeout=TEST_TIMEOUT
        )
        if outcome.passed:
            continue
        if outcome.timed_out:
            # A mutation that makes the official test hang is not a
            # deterministic repair target; skip it.
            continue
        candidate.viable_mutations.append(
            {
                "spec": spec.to_dict(),
                "mutated_source": mutated,
                "mutation_outcome": outcome.to_dict(),
            }
        )
    candidate.mutation_stats = stats
    return candidate


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------

def _select_tasks(
    candidates: List[ProblemCandidate],
    target_count: int,
) -> Tuple[List[Tuple[ProblemCandidate, Dict[str, Any]]], List[Dict[str, Any]]]:
    """Select tasks deterministically, maximising mutation-rule diversity.

    The selection is a round-robin over the mutation rules: for each rule the
    first (in ``task_id`` order) not-yet-used problem with a viable mutation
    under that rule is taken.  This is fully deterministic and independent of
    execution timing.
    """
    usable = [c for c in candidates if c.eligible and c.gold_passes and c.viable_mutations]
    usable.sort(key=lambda c: _humaneval_sort_key(c.task_id))

    by_rule: Dict[str, List[ProblemCandidate]] = {r.name: [] for r in MUTATION_RULES}
    for cand in usable:
        seen_rules = set()
        for mutation in cand.viable_mutations:
            rule = mutation["spec"]["rule"]
            if rule in seen_rules:
                continue
            seen_rules.add(rule)
            by_rule[rule].append(cand)

    selected: List[Tuple[ProblemCandidate, Dict[str, Any]]] = []
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
            for cand in by_rule[rule.name]:
                if cand.task_id in used:
                    continue
                mutation = next(
                    m for m in cand.viable_mutations if m["spec"]["rule"] == rule.name
                )
                chosen = (cand, mutation)
                break
            if chosen is None:
                exhausted.add(rule.name)
                continue
            used.add(chosen[0].task_id)
            selected.append(chosen)
            progressed = True
        if not progressed:
            break

    skipped = []
    for cand in usable:
        if cand.task_id in used:
            continue
        skipped.append(
            {
                "humaneval_task_id": cand.task_id,
                "reason": "not_selected_by_rule_diversity_round_robin",
                "viable_mutations_found": len(cand.viable_mutations),
            }
        )
    return selected, skipped


def _humaneval_sort_key(task_id: str) -> Tuple[int, str]:
    # "HumanEval/12" -> (12, "HumanEval/12")
    tail = task_id.split("/")[-1]
    try:
        return (int(tail), task_id)
    except ValueError:
        return (10**9, task_id)


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
    return {k: os.path.relpath(v, _ROOT) for k, v in paths.items()}


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


def build(target_count: int = 20, jobs: int = 8, dataset_path: Optional[str] = None) -> Dict[str, Any]:
    """Build the Phase 0 artefacts.  Returns a summary dictionary."""
    tmp_dir = tempfile.mkdtemp(prefix="itms_phase0_build_")
    try:
        if dataset_path is None:
            dataset_path = os.path.join(tmp_dir, "HumanEval.jsonl.gz")
            fetch_pinned_dataset(dataset_path)
        try:
            license_info = fetch_license(LICENSE_FILE)
        except ProvenanceError:
            if not os.path.exists(LICENSE_FILE):
                raise
            with open(LICENSE_FILE, "rb") as fh:
                license_info = {
                    "url": "cached",
                    "sha256": sha256_bytes(fh.read()),
                }
        problems = load_problems(dataset_path)
        if len(problems) != DATASET_PROBLEM_COUNT:
            raise ProvenanceError(
                f"unexpected problem count {len(problems)} != {DATASET_PROBLEM_COUNT}"
            )

        # 1. eligibility + gold test (parallel over processes)
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            candidates = list(pool.map(_evaluate_problem, problems))
        candidates.sort(key=lambda c: _humaneval_sort_key(c.task_id))

        # 2. mutation exploration (only for eligible candidates with a passing gold)
        explore_targets = [c for c in candidates if c.eligible and c.gold_passes]
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            explored = list(pool.map(_explore_mutations, explore_targets))
        explored_by_id = {c.task_id: c for c in explored}
        for cand in candidates:
            if cand.task_id in explored_by_id:
                cand.viable_mutations = explored_by_id[cand.task_id].viable_mutations
                cand.mutation_stats = explored_by_id[cand.task_id].mutation_stats

        # 3. selection
        selected, skipped = _select_tasks(candidates, target_count)

        # 4. assemble records
        records: List[Dict[str, Any]] = []
        golds: Dict[str, str] = {}
        for index, (cand, mutation) in enumerate(selected, start=1):
            task_id = f"HE-EDIT-{index:03d}"
            spec = mutation["spec"]
            record = {
                "task_id": task_id,
                "humaneval_task_id": cand.task_id,
                "target_function": cand.entry_point,
                "source": {
                    "repository": SOURCE_REPO_URL,
                    "revision": SOURCE_REVISION,
                    "revision_type": "git_commit_sha",
                    "dataset_path": DATASET_PATH,
                    "dataset_gz_sha256": DATASET_GZ_SHA256,
                    "dataset_raw_sha256": DATASET_RAW_SHA256,
                    "license": LICENSE_SPDX,
                    "license_file": LICENSE_FILE_PATH,
                    "license_file_sha256": LICENSE_FILE_SHA256,
                    "repo_head_observed": SOURCE_REPO_HEAD,
                },
                "problem_prompt": cand.prompt,
                "official_test_source": cand.official_test,
                "official_test_hidden_from_model": True,
                "original_source": cand.original_source,
                "mutated_source": mutation["mutated_source"],
                "mutation": {
                    "rule": spec["rule"],
                    "parameters": spec["parameters"],
                    "description": next(
                        r.description for r in MUTATION_RULES if r.name == spec["rule"]
                    ),
                    "validation": mutation["mutation_outcome"],
                },
                "repair_instruction": REPAIR_INSTRUCTION.format(target=cand.entry_point),
                "gold_solution_reference": "benchmarks/data/gold_solutions.json",
                "checksums": {
                    "problem_prompt_sha256": sha256_text(cand.prompt),
                    "official_test_sha256": sha256_text(cand.official_test),
                    "original_source_sha256": sha256_text(cand.original_source),
                    "mutated_source_sha256": sha256_text(mutation["mutated_source"]),
                },
                "eligibility": {
                    "eligible": cand.eligible,
                    "reasons": cand.eligibility_reasons,
                    "details": cand.eligibility_details,
                },
            }
            record["files"] = _write_task_files(task_id, record)
            records.append(record)
            golds[task_id] = cand.original_source

        # 5. write artefacts
        os.makedirs(DATA_DIR, exist_ok=True)
        os.makedirs(SOURCE_DIR, exist_ok=True)
        if os.path.isdir(TASKS_DIR):
            shutil.rmtree(TASKS_DIR)
        for record in records:
            _write_task_files(record["task_id"], record)

        with open(TASKS_FILE, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "schema_version": "1.0",
                    "task_set": "humaneval_edit",
                    "description": (
                        "Synthetic single-file function-repair tasks derived from "
                        "HumanEval. These are NOT the original HumanEval benchmark "
                        "tasks and must not be reported as HumanEval scores."
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
                        "makes the official HumanEval check() fail."
                    ),
                    "rules": [r.to_dict() for r in MUTATION_RULES],
                },
                fh,
                indent=2,
                ensure_ascii=False,
            )
            fh.write("\n")

        # vendored source subset (only the problems that are actually used)
        used_ids = {r["humaneval_task_id"] for r in records}
        with open(SUBSET_FILE, "w", encoding="utf-8") as fh:
            for problem in problems:
                if problem["task_id"] in used_ids:
                    fh.write(
                        json.dumps(problem_to_dict(problem), ensure_ascii=False) + "\n"
                    )

        provenance = {
            "schema_version": "1.0",
            "repository": SOURCE_REPO_URL,
            "revision": SOURCE_REVISION,
            "revision_type": "git_commit_sha",
            "revision_verified_via": "GitHub API /repos/openai/human-eval/commits?path=data/HumanEval.jsonl.gz",
            "repo_head_observed": SOURCE_REPO_HEAD,
            "dataset_path": DATASET_PATH,
            "dataset_gz_sha256": DATASET_GZ_SHA256,
            "dataset_raw_sha256": DATASET_RAW_SHA256,
            "dataset_problem_count": DATASET_PROBLEM_COUNT,
            "license": LICENSE_SPDX,
            "license_file": LICENSE_FILE_PATH,
            "license_file_sha256": LICENSE_FILE_SHA256,
            "vendored_subset": os.path.relpath(SUBSET_FILE, _ROOT),
            "vendored_license": os.path.relpath(LICENSE_FILE, _ROOT),
            "vendored_license_sha256": license_info["sha256"],
            "vendored_license_source": license_info["url"],
            "vendored_subset_sha256": sha256_text(_read(SUBSET_FILE)),
            "notes": [
                "The upstream dataset file data/HumanEval.jsonl.gz has exactly "
                "one commit in the repository history, which is the pinned "
                "revision above.",
                "The upstream repository has been marked deprecated and "
                "unmaintained by OpenAI (see repository README at repo_head_observed).",
                "Only the subset of problems used by the accepted tasks is "
                "vendored here; the full dataset is not redistributed.",
            ],
        }
        with open(PROVENANCE_FILE, "w", encoding="utf-8") as fh:
            json.dump(provenance, fh, indent=2, ensure_ascii=False)
            fh.write("\n")

        rejected = []
        for cand in candidates:
            if cand.eligible and cand.gold_passes and cand.viable_mutations:
                continue
            if not cand.eligible:
                reason = "; ".join(cand.eligibility_reasons)
            elif not cand.gold_passes:
                reason = (
                    "reference_solution_failed_official_test: "
                    f"{cand.gold_outcome.get('error_type')}"
                )
            else:
                reason = "no_mutation_in_catalogue_made_the_official_test_fail"
            rejected.append(
                {
                    "humaneval_task_id": cand.task_id,
                    "reason": reason,
                    "eligibility_reasons": cand.eligibility_reasons,
                    "gold_outcome": cand.gold_outcome,
                }
            )

        build_log = {
            "schema_version": "1.0",
            "generated_by": "benchmarks/phase0/build_tasks.py",
            "dataset_revision": SOURCE_REVISION,
            "problems_inspected": len(candidates),
            "eligible": sum(1 for c in candidates if c.eligible),
            "gold_passed": sum(1 for c in candidates if c.gold_passes),
            "with_viable_mutations": sum(
                1 for c in candidates if c.eligible and c.gold_passes and c.viable_mutations
            ),
            "accepted": len(records),
            "rejected": len(rejected),
            "not_selected": len(skipped),
            "rejected_candidates": rejected,
            "not_selected_candidates": skipped,
            "per_problem": [c.to_log_dict() for c in candidates],
        }
        with open(BUILD_LOG_FILE, "w", encoding="utf-8") as fh:
            json.dump(build_log, fh, indent=2, ensure_ascii=False)
            fh.write("\n")

        _write_manifest(records)

        return {
            "accepted": len(records),
            "rejected": len(rejected),
            "problems_inspected": len(candidates),
            "eligible": build_log["eligible"],
            "with_viable_mutations": build_log["with_viable_mutations"],
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _write_manifest(records: List[Dict[str, Any]]) -> None:
    """Write the manifest that pins every Phase 0 artefact by checksum."""
    import datetime

    files = {}
    for path in (
        TASKS_FILE,
        GOLD_FILE,
        MODEL_FACING_FILE,
        CATALOGUE_FILE,
        BUILD_LOG_FILE,
        SUBSET_FILE,
        PROVENANCE_FILE,
        LICENSE_FILE,
    ):
        files[os.path.relpath(path, _ROOT)] = {
            "sha256": sha256_text(_read(path)),
            "bytes": os.path.getsize(path),
        }

    task_files = {}
    for record in records:
        task_files[record["task_id"]] = {}
        for key, rel in record["files"].items():
            full = os.path.join(_ROOT, rel)
            task_files[record["task_id"]][key] = {
                "path": rel,
                "sha256": sha256_text(_read(full)),
            }

    manifest = {
        "schema_version": "1.0",
        "manifest_version": "1.0.0",
        "generated_at_utc": datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "generator": "benchmarks/phase0/build_tasks.py",
        "python_version": sys.version.split()[0],
        "source": {
            "repository": SOURCE_REPO_URL,
            "revision": SOURCE_REVISION,
            "dataset_path": DATASET_PATH,
            "dataset_gz_sha256": DATASET_GZ_SHA256,
            "license": LICENSE_SPDX,
        },
        "counts": {
            "tasks": len(records),
            "mutation_rules": len(MUTATION_RULES),
        },
        "files": files,
        "task_files": task_files,
        "task_checksums": {
            r["task_id"]: r["checksums"] for r in records
        },
        "verify_command": (
            "python benchmarks/phase0/validate_tasks.py --verify-manifest "
            "--json benchmarks/data/validation_report.json"
        ),
    }
    with open(MANIFEST_FILE, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build Phase 0 HumanEval edit tasks")
    parser.add_argument("--tasks", type=int, default=20)
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--dataset", default=None, help="path to a local HumanEval.jsonl.gz")
    args = parser.parse_args(argv)

    summary = build(target_count=args.tasks, jobs=args.jobs, dataset_path=args.dataset)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
