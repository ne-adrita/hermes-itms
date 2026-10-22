"""
Offline tests for the *modular* task family.

The tests are local only: they never contact a provider, never make a
network request and never write into the task dataset.  They guard the
properties that the new family must have:

*   the family exists, is loadable and has 20 unique tasks,
*   every task satisfies the **pre-registered** representative-task rule,
*   the existing HumanEval family is untouched and still fails that rule,
*   the defects are reproducible, byte-local and detected by the official
    check,
*   the official checks are discriminating and do not leak the worked
    example of the docstring,
*   every task is solvable by all three editing methods,
*   the official evaluator is used unchanged.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from benchmarks.benchmark.tasks import TaskRepository  # noqa: E402
from benchmarks.phase2.task_selection import (  # noqa: E402
    DEFAULT_CRITERIA,
    select_tasks,
)
from benchmarks.phase0.eligibility import check_self_containment  # noqa: E402
from benchmarks.phase0.mutation_engine import apply_mutation  # noqa: E402
from benchmarks.phase0.runner import run_official_test  # noqa: E402

MODULAR_DIR = os.path.join(REPO_ROOT, "benchmarks", "data", "modular")
HUMANEVAL_DIR = os.path.join(REPO_ROOT, "benchmarks", "data")

TEST_TIMEOUT = 25.0


def _load_json(path: str):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.fixture(scope="module")
def modular_repo() -> TaskRepository:
    return TaskRepository(data_dir=MODULAR_DIR)


@pytest.fixture(scope="module")
def modular_tasks() -> list:
    return _load_json(os.path.join(MODULAR_DIR, "modular_edit_tasks.json"))["tasks"]


@pytest.fixture(scope="module")
def humaneval_repo() -> TaskRepository:
    return TaskRepository(data_dir=HUMANEVAL_DIR)


# --------------------------------------------------------------------------
# The family exists and is loadable
# --------------------------------------------------------------------------

def test_the_modular_family_has_twenty_unique_loadable_tasks(modular_repo):
    task_ids = modular_repo.task_ids
    assert len(task_ids) == 20
    assert len(set(task_ids)) == 20
    assert all(task_id.startswith("MOD-EDIT-") for task_id in task_ids)
    for task_id in task_ids:
        task = modular_repo.get(task_id)
        assert task.mutated_source
        assert task.official_test
        assert task.target_function


def test_the_modular_family_uses_a_separate_dataset_root():
    """The HumanEval family must not be mixed into the modular one."""
    assert MODULAR_DIR != HUMANEVAL_DIR
    assert not os.path.exists(os.path.join(MODULAR_DIR, "tasks", "HE-EDIT-001"))
    assert not os.path.exists(os.path.join(HUMANEVAL_DIR, "tasks", "MOD-EDIT-001"))


def test_the_humaneval_family_is_untouched(humaneval_repo):
    """The existing family keeps its 20 tasks and its manifest digest."""
    assert len(humaneval_repo.task_ids) == 20
    assert humaneval_repo.manifest_digest() == (
        "92077bdca33b75b3ca3c7c15a55b237b6c1f5c79d1da20ebb0f9efb4d23d08f1"
    )


def test_the_humaneval_family_still_fails_the_preregistered_rule(humaneval_repo):
    """
    The preregistered rule is not changed to fit the new family.

    The old family still has zero qualifying tasks; the rule itself is the
    one that was fixed in version-controlled code before any run.
    """
    result = select_tasks(humaneval_repo.select(None), DEFAULT_CRITERIA)
    assert result.selected == []
    assert len(result.rejected) == 20


# --------------------------------------------------------------------------
# The pre-registered rule
# --------------------------------------------------------------------------

def test_every_modular_task_satisfies_the_preregistered_rule(modular_repo):
    result = select_tasks(modular_repo.select(None), DEFAULT_CRITERIA)
    assert result.rejected == {}
    assert len(result.selected) == 20
    for properties in result.properties.values():
        assert properties.file_lines >= DEFAULT_CRITERIA.min_file_lines
        assert properties.target_ratio <= DEFAULT_CRITERIA.max_target_ratio
        assert properties.function_count >= DEFAULT_CRITERIA.min_function_count
        assert properties.class_count >= DEFAULT_CRITERIA.min_class_count


def test_the_modular_family_is_much_larger_than_the_target(modular_repo):
    """The target must be a small region of a genuinely larger file."""
    for task in modular_repo.select(None):
        assert len(task.mutated_source.splitlines()) >= 200
        tree = ast.parse(task.mutated_source)
        names = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert task.target_function in names
        assert len(names) >= 5
        assert any(isinstance(node, ast.ClassDef) for node in ast.walk(tree))


# --------------------------------------------------------------------------
# The defect
# --------------------------------------------------------------------------

def test_the_defect_is_reproducible_and_byte_local(modular_tasks):
    for record in modular_tasks:
        original = record["original_source"]
        mutated = record["mutated_source"]
        reproduced, _ = apply_mutation(
            original,
            record["target_function"],
            record["mutation"]["rule"],
            record["mutation"]["parameters"],
        )
        assert reproduced == mutated, record["task_id"]
        original_lines = original.splitlines()
        mutated_lines = mutated.splitlines()
        assert len(original_lines) == len(mutated_lines), record["task_id"]
        changed = [
            index
            for index, (a, b) in enumerate(zip(original_lines, mutated_lines))
            if a != b
        ]
        assert len(changed) == 1, record["task_id"]


def test_the_reference_module_passes_and_the_defect_fails_the_official_check(
    modular_tasks,
):
    for record in modular_tasks:
        gold = run_official_test(
            record["original_source"],
            record["official_test_source"],
            record["target_function"],
            timeout=TEST_TIMEOUT,
        )
        assert gold.passed, (record["task_id"], gold.stderr[-400:])
        broken = run_official_test(
            record["mutated_source"],
            record["official_test_source"],
            record["target_function"],
            timeout=TEST_TIMEOUT,
        )
        assert not broken.passed, record["task_id"]
        assert not broken.timed_out, record["task_id"]


def test_every_target_is_self_contained(modular_tasks):
    for record in modular_tasks:
        result = check_self_containment(
            record["original_source"], record["target_function"]
        )
        assert result.eligible, (record["task_id"], result.reasons)


def test_the_official_check_is_discriminating(modular_tasks):
    """A check needs several assertions to reject a partial repair."""
    for record in modular_tasks:
        tree = ast.parse(record["official_test_source"])
        assertions = sum(
            1 for node in ast.walk(tree) if isinstance(node, ast.Assert)
        )
        assert assertions >= 3, record["task_id"]


def test_the_worked_example_does_not_leak_into_the_official_check(modular_tasks):
    from benchmarks.phase0.validate_modular_tasks import (
        asserted_literals,
        docstring_expected_scalars,
    )
    from benchmarks.phase0.eligibility import docstring_of

    for record in modular_tasks:
        doc = docstring_of(ast.parse(record["original_source"]), record["target_function"])
        assert doc, record["task_id"]
        examples = docstring_expected_scalars(doc)
        literals = asserted_literals(record["official_test_source"])
        assert not (examples & literals), record["task_id"]


def test_the_module_text_is_not_credential_shaped(modular_tasks):
    """
    A credential-shaped module would be redacted by the artefact store, so
    the saved candidate would no longer be the bytes that were evaluated.
    """
    from benchmarks.benchmark.provider import contains_credential

    for record in modular_tasks:
        assert not contains_credential(record["original_source"]), record["task_id"]
        assert not contains_credential(record["mutated_source"]), record["task_id"]
        assert not contains_credential(record["official_test_source"]), record["task_id"]


# --------------------------------------------------------------------------
# Solvability by the three editing methods
# --------------------------------------------------------------------------

def test_every_task_is_solvable_by_all_three_editing_methods(modular_tasks):
    """
    The gold repair must work with the real ITMS splicer and the real
    patcher, and the complete reference module must pass the official check.
    """
    from benchmarks.phase0.validate_tasks import _ast_splice_check, _search_replace_check

    for record in modular_tasks:
        gold_source = record["original_source"]
        assert run_official_test(
            gold_source,
            record["official_test_source"],
            record["target_function"],
            timeout=TEST_TIMEOUT,
        ).passed, record["task_id"]

        ok, detail = _search_replace_check(record, gold_source)
        assert ok, (record["task_id"], detail)

        ok, detail = _ast_splice_check(record, gold_source)
        assert ok, (record["task_id"], detail)


def test_the_body_only_splice_handles_a_multi_line_signature():
    """
    Regression guard for the body extraction.

    A function whose ``def`` header spans several lines must still yield a
    body that the real splicer accepts.
    """
    from benchmarks.phase0.validate_tasks import _body_only

    source = (
        "def wide(\n"
        "    first: int,\n"
        "    second: int,\n"
        ") -> int:\n"
        '    """Return the sum."""\n'
        "    return first + second\n"
    )
    body = _body_only(source)
    assert body.strip().startswith('"""Return the sum."""')
    ast.parse("def wide(first, second):\n" + body)


# --------------------------------------------------------------------------
# Manifest and runner integration
# --------------------------------------------------------------------------

def test_the_modular_manifest_verifies():
    from benchmarks.phase0.validate_modular_tasks import verify_manifest

    report = verify_manifest()
    assert report["mismatches"] == []
    assert report["passed"] is True
    assert report["files_checked"] > 0


def test_the_model_facing_payload_leaks_nothing(modular_tasks):
    payload = _load_json(os.path.join(MODULAR_DIR, "model_facing_tasks.json"))
    assert len(payload["tasks"]) == 20
    for entry in payload["tasks"]:
        assert "mutation" not in entry
        assert "gold" not in entry
        assert "official_test" not in entry
        assert "expected_change" not in entry
        for record in modular_tasks:
            if record["task_id"] != entry["task_id"]:
                continue
            assert entry["mutated_source"] == record["mutated_source"]
            assert entry["problem_prompt"] == record["problem_prompt"]


def test_the_runner_accepts_the_dataset_root_and_keeps_its_default():
    """``--data-dir`` is additive: the default still loads the old family."""
    from benchmarks.benchmark_runner import _build_parser

    parser = _build_parser()
    args = parser.parse_args([])
    assert args.data_dir is None
    args = parser.parse_args(["--data-dir", "benchmarks/data/modular"])
    assert args.data_dir == "benchmarks/data/modular"

    default_repo = TaskRepository()
    assert default_repo.task_ids[0].startswith("HE-EDIT-")
    modular_repo = TaskRepository(data_dir=MODULAR_DIR)
    assert modular_repo.task_ids[0].startswith("MOD-EDIT-")


def test_the_offline_dry_run_accepts_the_modular_family(tmp_path):
    """A mock dry run exercises the whole pipeline without any provider call."""
    result = subprocess.run(
        [
            sys.executable,
            os.path.join(REPO_ROOT, "benchmarks", "benchmark_runner.py"),
            "--mode",
            "mock",
            "--data-dir",
            "benchmarks/data/modular",
            "--tasks",
            "MOD-EDIT-001",
            "--reps",
            "1",
            "--output-dir",
            str(tmp_path / "mock_smoke"),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "MOCK RUN" in result.stdout
