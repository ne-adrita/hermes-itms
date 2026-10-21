"""
Phase 0 tests: offline validation of the HumanEval-derived edit task set.

These tests are self-contained: they read the artefacts in
``benchmarks/data/`` and re-run the offline checks.  They never contact a model
or an inference API, and they never run the benchmark.

Run with::

    python -m pytest benchmarks/phase0/test_phase0_tasks.py -q
"""

from __future__ import annotations

import ast
import json
import os
import sys
from typing import Any, Dict, List

import pytest

_PHASE0_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_PHASE0_DIR))
if _PHASE0_DIR not in sys.path:
    sys.path.insert(0, _PHASE0_DIR)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dataset import sha256_text  # noqa: E402
from eligibility import check_self_containment, docstring_of  # noqa: E402
from mutation_engine import MUTATION_RULES, apply_mutation  # noqa: E402
from runner import run_official_test  # noqa: E402
from validate_tasks import verify_manifest  # noqa: E402

DATA_DIR = os.path.join(_ROOT, "benchmarks", "data")
TASKS_FILE = os.path.join(DATA_DIR, "humaneval_edit_tasks.json")
GOLD_FILE = os.path.join(DATA_DIR, "gold_solutions.json")
MODEL_FACING_FILE = os.path.join(DATA_DIR, "model_facing_tasks.json")
CATALOGUE_FILE = os.path.join(DATA_DIR, "mutation_catalogue.json")
MANIFEST_FILE = os.path.join(DATA_DIR, "manifest.json")
SUBSET_FILE = os.path.join(DATA_DIR, "source", "humaneval_subset.jsonl")
PROVENANCE_FILE = os.path.join(DATA_DIR, "source", "PROVENANCE.json")

EXPECTED_TASK_COUNT = 20


def _load(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def tasks_doc() -> Dict[str, Any]:
    return _load(TASKS_FILE)


@pytest.fixture(scope="module")
def tasks(tasks_doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    return tasks_doc["tasks"]


@pytest.fixture(scope="module")
def golds() -> Dict[str, str]:
    return _load(GOLD_FILE)["gold_solutions"]


@pytest.fixture(scope="module")
def model_facing() -> Dict[str, Any]:
    return _load(MODEL_FACING_FILE)


# --------------------------------------------------------------------------
# Artefacts
# --------------------------------------------------------------------------

class TestArtefacts:

    def test_all_artefacts_exist(self):
        for path in (
            TASKS_FILE,
            GOLD_FILE,
            MODEL_FACING_FILE,
            CATALOGUE_FILE,
            MANIFEST_FILE,
            SUBSET_FILE,
            PROVENANCE_FILE,
        ):
            assert os.path.exists(path), path

    def test_task_count(self, tasks):
        assert len(tasks) == EXPECTED_TASK_COUNT

    def test_task_ids_are_stable_and_unique(self, tasks):
        ids = [t["task_id"] for t in tasks]
        assert ids == [f"HE-EDIT-{i:03d}" for i in range(1, EXPECTED_TASK_COUNT + 1)]
        assert len(set(ids)) == len(ids)

    def test_manifest_verifies(self):
        result = verify_manifest()
        assert result["passed"], result["mismatches"]

    def test_manifest_records_source_provenance(self):
        manifest = _load(MANIFEST_FILE)
        assert manifest["source"]["revision"]
        assert manifest["source"]["dataset_gz_sha256"]
        assert manifest["counts"]["tasks"] == EXPECTED_TASK_COUNT

    def test_task_records_contain_required_fields(self, tasks):
        required = {
            "task_id",
            "humaneval_task_id",
            "target_function",
            "source",
            "problem_prompt",
            "official_test_source",
            "original_source",
            "mutated_source",
            "mutation",
            "repair_instruction",
            "gold_solution_reference",
            "checksums",
            "eligibility",
        }
        for task in tasks:
            assert required.issubset(task.keys()), task["task_id"]
            assert task["source"]["repository"]
            assert task["source"]["revision"]
            assert task["source"]["license"]

    def test_vendored_subset_only_contains_used_problems(self, tasks):
        with open(SUBSET_FILE, "r", encoding="utf-8") as fh:
            subset_ids = {json.loads(line)["task_id"] for line in fh if line.strip()}
        assert subset_ids == {t["humaneval_task_id"] for t in tasks}

    def test_provenance_pins_a_commit_sha(self):
        provenance = _load(PROVENANCE_FILE)
        assert provenance["revision_type"] == "git_commit_sha"
        assert len(provenance["revision"]) == 40
        assert provenance["license"] == "MIT"


# --------------------------------------------------------------------------
# Mutation catalogue
# --------------------------------------------------------------------------

class TestMutationCatalogue:

    def test_catalogue_matches_engine(self):
        catalogue = _load(CATALOGUE_FILE)
        assert [r["name"] for r in catalogue["rules"]] == [r.name for r in MUTATION_RULES]
        for rule in catalogue["rules"]:
            assert rule["description"]
            assert rule["parameters"]

    def test_mutations_are_deterministic(self, tasks):
        for task in tasks:
            mutation = task["mutation"]
            first, _ = apply_mutation(
                task["original_source"],
                task["target_function"],
                mutation["rule"],
                mutation["parameters"],
            )
            second, _ = apply_mutation(
                task["original_source"],
                task["target_function"],
                mutation["rule"],
                mutation["parameters"],
            )
            assert first == second == task["mutated_source"], task["task_id"]

    def test_mutations_preserve_the_docstring(self, tasks):
        for task in tasks:
            original_doc = docstring_of(ast.parse(task["original_source"]), task["target_function"])
            mutated_doc = docstring_of(ast.parse(task["mutated_source"]), task["target_function"])
            assert original_doc is not None, task["task_id"]
            assert original_doc == mutated_doc, task["task_id"]

    def test_mutation_is_a_single_localised_edit(self, tasks):
        for task in tasks:
            original = task["original_source"].splitlines()
            mutated = task["mutated_source"].splitlines()
            assert len(original) == len(mutated), task["task_id"]
            differing = [i for i, (a, b) in enumerate(zip(original, mutated)) if a != b]
            assert differing, task["task_id"]

    def test_mutated_source_is_syntactically_valid(self, tasks):
        for task in tasks:
            ast.parse(task["mutated_source"])

    def test_mutated_source_is_not_a_trivial_placeholder(self, tasks):
        for task in tasks:
            body_lines = [
                line.strip()
                for line in task["mutated_source"].splitlines()
                if line.strip() and not line.strip().startswith(("#", '"""', "'''"))
            ]
            assert len(body_lines) >= 3, task["task_id"]
            assert "pass" not in body_lines, task["task_id"]
            assert "..." not in body_lines, task["task_id"]


# --------------------------------------------------------------------------
# Gold / mutant behaviour
# --------------------------------------------------------------------------

class TestOfflineTestExecution:

    def test_gold_passes_and_mutant_fails(self, tasks):
        for task in tasks:
            gold = run_official_test(
                task["original_source"],
                task["official_test_source"],
                task["target_function"],
            )
            assert gold.passed, (task["task_id"], gold.error_type, gold.error_message)
            mutated = run_official_test(
                task["mutated_source"],
                task["official_test_source"],
                task["target_function"],
            )
            assert not mutated.passed, task["task_id"]
            assert not mutated.timed_out, task["task_id"]

    def test_sandbox_blocks_network(self):
        source = (
            "def probe():\n"
            "    import socket\n"
            "    socket.socket()\n"
            "    return 1\n"
        )
        test = "\n\ndef check(candidate):\n    assert candidate() == 1\n"
        outcome = run_official_test(source, test, "probe")
        assert not outcome.passed
        assert outcome.error_type in ("ExecutionError", "OSError", "NameError")


# --------------------------------------------------------------------------
# Eligibility
# --------------------------------------------------------------------------

class TestEligibility:

    def test_accepted_tasks_are_self_contained(self, tasks):
        for task in tasks:
            result = check_self_containment(task["original_source"], task["target_function"])
            assert result.eligible, (task["task_id"], result.reasons)

    def test_helper_calling_function_is_rejected(self):
        source = (
            "def helper(x):\n"
            "    return x * 2\n"
            "\n"
            "def target(x):\n"
            "    return helper(x) + 1\n"
        )
        result = check_self_containment(source, "target")
        assert not result.eligible
        assert any("same_file" in reason for reason in result.reasons)

    def test_unresolvable_free_name_is_rejected(self):
        source = (
            "def target(x):\n"
            "    return undefined_helper(x)\n"
        )
        result = check_self_containment(source, "target")
        assert not result.eligible

    def test_builtin_only_function_is_accepted(self):
        source = (
            "def target(xs):\n"
            "    return sum(abs(x) for x in xs)\n"
        )
        result = check_self_containment(source, "target")
        assert result.eligible, result.reasons


# --------------------------------------------------------------------------
# Leakage / model-facing separation
# --------------------------------------------------------------------------

class TestModelFacingSeparation:

    def test_model_facing_payload_has_no_gold(self, model_facing, golds):
        blob = json.dumps(model_facing)
        for task_id, gold in golds.items():
            assert gold.strip() not in blob, task_id

    def test_model_facing_payload_has_no_mutation_metadata(self, model_facing, tasks):
        blob = json.dumps(model_facing)
        for task in tasks:
            assert task["mutation"]["rule"] not in blob, task["task_id"]

    def test_model_facing_payload_covers_every_task(self, model_facing, tasks):
        assert {t["task_id"] for t in model_facing["tasks"]} == {t["task_id"] for t in tasks}

    def test_official_test_does_not_contain_the_gold_source(self, tasks, golds):
        for task in tasks:
            gold = golds[task["task_id"]]
            assert gold.strip() not in task["official_test_source"], task["task_id"]

    def test_repair_instruction_does_not_contain_the_gold_source(self, tasks, golds):
        for task in tasks:
            gold = golds[task["task_id"]]
            assert gold.strip() not in task["repair_instruction"], task["task_id"]

    def test_gold_file_is_never_referenced_from_the_model_facing_payload(self, model_facing):
        blob = json.dumps(model_facing)
        assert "gold_solutions" not in blob


# --------------------------------------------------------------------------
# Phase 0 must not talk to a model
# --------------------------------------------------------------------------

class TestNoModelAccess:

    FORBIDDEN_IMPORTS = {
        "openai",
        "anthropic",
        "openrouter",
        "model_dispatcher",
        "requests",
        "httpx",
        "urllib3",
    }
    OFFLINE_MODULES = (
        "eligibility.py",
        "mutation_engine.py",
        "runner.py",
        "validate_tasks.py",
        "write_report.py",
        "test_phase0_tasks.py",
    )

    @staticmethod
    def _imports_of(path: str) -> set:
        with open(path, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names.add(node.module.split(".")[0])
        return names

    def test_phase0_modules_do_not_import_model_clients(self):
        for name in self.OFFLINE_MODULES:
            path = os.path.join(_PHASE0_DIR, name)
            imports = self._imports_of(path)
            assert not (imports & self.FORBIDDEN_IMPORTS), f"{name}: {imports}"

    def test_offline_modules_do_not_import_network_libraries(self):
        # ``dataset.py`` is the only module allowed to reach the network, and
        # only to fetch the checksum-verified pinned dataset.
        for name in self.OFFLINE_MODULES:
            path = os.path.join(_PHASE0_DIR, name)
            imports = self._imports_of(path)
            assert "urllib" not in imports, name
            assert "socket" not in imports, name

    def test_only_the_dataset_module_can_fetch(self):
        fetchers = []
        for name in os.listdir(_PHASE0_DIR):
            if not name.endswith(".py") or name.startswith("test_"):
                continue
            imports = self._imports_of(os.path.join(_PHASE0_DIR, name))
            if "urllib" in imports:
                fetchers.append(name)
        assert fetchers == ["dataset.py"]
