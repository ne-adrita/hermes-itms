"""Editing-adapter tests: output contracts, prompt hygiene, real implementations."""

from __future__ import annotations

import ast
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from benchmarks.benchmark.adapters import (  # noqa: E402
    FullFileRegenerationAdapter,
    ItmsAstSpliceAdapter,
    SearchReplacePatchAdapter,
    get_adapter,
)
from benchmarks.benchmark.adapters.base import FailureReason  # noqa: E402

DATA_DIR = os.path.join(_ROOT, "benchmarks", "data")


def _gold_solutions():
    with open(os.path.join(DATA_DIR, "gold_solutions.json"), "r", encoding="utf-8") as fh:
        return json.load(fh)["gold_solutions"]


def _mutation_metadata():
    with open(os.path.join(DATA_DIR, "humaneval_edit_tasks.json"), "r", encoding="utf-8") as fh:
        return {t["task_id"]: t["mutation"] for t in json.load(fh)["tasks"]}


class TestAdapterRegistry:

    def test_known_methods(self):
        for method in (
            "full_file_regeneration",
            "search_replace_patch",
            "itms_ast_splice",
        ):
            adapter = get_adapter(method)
            assert adapter.name == method

    def test_unknown_method_raises(self):
        with pytest.raises(ValueError):
            get_adapter("does_not_exist")


class TestPromptHygiene:

    """No gold solution and no mutation metadata may reach a model."""

    @pytest.mark.parametrize(
        "method", ["full_file_regeneration", "search_replace_patch", "itms_ast_splice"]
    )
    def test_prompt_contains_no_gold_solution(self, repository, method):
        adapter = get_adapter(method)
        golds = _gold_solutions()
        for task in repository.select():
            prompt = adapter.build_prompt(task)
            gold = golds[task.task_id]
            assert gold.strip() not in prompt, task.task_id
            # The *repaired* form of the defective line must not appear: that
            # would hand the model the answer.
            gold_lines = gold.splitlines()
            mutated_lines = task.mutated_source.splitlines()
            for gold_line, mutated_line in zip(gold_lines, mutated_lines):
                if gold_line == mutated_line:
                    continue
                if gold_line.strip() and gold_line.strip() in prompt:
                    raise AssertionError(
                        f"{task.task_id}/{method}: the repaired line leaked into the prompt"
                    )

    @pytest.mark.parametrize(
        "method", ["full_file_regeneration", "search_replace_patch", "itms_ast_splice"]
    )
    def test_prompt_contains_no_mutation_metadata(self, repository, method):
        adapter = get_adapter(method)
        metadata = _mutation_metadata()
        for task in repository.select():
            prompt = adapter.build_prompt(task)
            mutation = metadata[task.task_id]
            assert mutation["rule"] not in prompt, task.task_id
            assert str(mutation["parameters"]) not in prompt, task.task_id

    def test_prompt_contains_the_mutated_source_and_instruction(self, repository):
        task = repository.get("HE-EDIT-001")
        prompt = FullFileRegenerationAdapter().build_prompt(task)
        assert task.mutated_source.rstrip() in prompt
        assert task.repair_instruction.splitlines()[0] in prompt

    def test_itms_prompt_uses_the_skeletonizer(self, repository):
        task = repository.get("HE-EDIT-001")
        prompt = ItmsAstSpliceAdapter().build_prompt(task)
        from skeletonizer import skeletonize_code

        skeleton = skeletonize_code(
            source_code=task.mutated_source,
            target_function_name=task.target_function,
            strip_docstrings=True,
            prune_unreachable=True,
            task_description=task.repair_instruction,
        )
        assert skeleton.rstrip() in prompt


class TestFullFileAdapter:

    def test_valid_single_block_is_applied(self, simple_task):
        adapter = FullFileRegenerationAdapter()
        response = "```python\ndef below_zero(xs):\n    return [x for x in xs if x < 0]\n```"
        result = adapter.parse_and_apply(simple_task, 1, response)
        assert result.applied is True
        assert result.parse_status == "ok"
        assert "def below_zero" in result.candidate_source

    def test_multiple_blocks_are_rejected(self, simple_task):
        adapter = FullFileRegenerationAdapter()
        response = "```python\na = 1\n```\n```python\nb = 2\n```"
        result = adapter.parse_and_apply(simple_task, 1, response)
        assert result.applied is False
        assert result.failure_reason == FailureReason.MULTIPLE_CODE_BLOCKS

    def test_missing_block_is_rejected(self, simple_task):
        adapter = FullFileRegenerationAdapter()
        result = adapter.parse_and_apply(simple_task, 1, "I fixed it!")
        assert result.applied is False
        assert result.failure_reason == FailureReason.NO_CODE_BLOCK

    def test_empty_response_is_rejected(self, simple_task):
        adapter = FullFileRegenerationAdapter()
        result = adapter.parse_and_apply(simple_task, 1, "   ")
        assert result.applied is False
        assert result.failure_reason == FailureReason.EMPTY_RESPONSE

    def test_syntax_error_is_reported(self, simple_task):
        adapter = FullFileRegenerationAdapter()
        response = "```python\ndef below_zero(xs)\n    return []\n```"
        result = adapter.parse_and_apply(simple_task, 1, response)
        assert result.applied is False
        assert result.failure_reason == FailureReason.SYNTAX_ERROR

    def test_missing_target_function_is_reported(self, simple_task):
        adapter = FullFileRegenerationAdapter()
        response = "```python\ndef unrelated():\n    return 1\n```"
        result = adapter.parse_and_apply(simple_task, 1, response)
        assert result.applied is False
        assert result.failure_reason == FailureReason.TARGET_FUNCTION_MISSING


class TestSearchReplaceAdapter:

    def _patch(self, task, search: str, replace: str) -> str:
        return f"<<<<<<< SEARCH\n{search}\n=======\n{replace}\n>>>>>>> REPLACE"

    def test_exact_single_match_is_applied(self, simple_task):
        adapter = SearchReplacePatchAdapter()
        search = "        if balance <= 0:"
        replace = "        if balance < 0:"
        response = self._patch(simple_task, search, replace)
        result = adapter.parse_and_apply(simple_task, 1, response)
        assert result.applied is True
        assert "balance < 0" in result.candidate_source
        assert "balance <= 0" not in result.candidate_source

    def test_missing_search_text_is_rejected(self, simple_task):
        adapter = SearchReplacePatchAdapter()
        response = self._patch(simple_task, "this text is not in the file", "x = 1")
        result = adapter.parse_and_apply(simple_task, 1, response)
        assert result.applied is False
        assert result.failure_reason == FailureReason.SEARCH_BLOCK_NOT_FOUND

    def test_ambiguous_search_text_is_rejected(self, repository):
        adapter = SearchReplacePatchAdapter()
        task = repository.get("HE-EDIT-001")
        # "enumerate(numbers):" occurs twice in the file, so the patch is
        # ambiguous and must be refused rather than applied to the wrong site.
        assert task.mutated_source.count("enumerate(numbers):") > 1
        response = self._patch(task, "enumerate(numbers):", "enumerate(xs):")
        result = adapter.parse_and_apply(task, 1, response)
        assert result.applied is False
        assert result.failure_reason == FailureReason.AMBIGUOUS_SEARCH_BLOCK

    def test_multiple_patch_blocks_are_rejected(self, simple_task):
        adapter = SearchReplacePatchAdapter()
        response = (
            self._patch(simple_task, "a", "b")
            + "\n"
            + self._patch(simple_task, "c", "d")
        )
        result = adapter.parse_and_apply(simple_task, 1, response)
        assert result.applied is False
        assert result.failure_reason == FailureReason.MULTIPLE_PATCH_BLOCKS

    def test_malformed_patch_is_rejected(self, simple_task):
        adapter = SearchReplacePatchAdapter()
        result = adapter.parse_and_apply(simple_task, 1, "SEARCH: foo\nREPLACE: bar\n")
        assert result.applied is False
        assert result.failure_reason == FailureReason.MALFORMED_PATCH

    def test_uses_the_project_text_diff_patcher(self, simple_task, monkeypatch):
        import surgical_splicer

        calls = []
        original = surgical_splicer.TextDiffPatcher.apply_search_replace

        def spy(*args, **kwargs):
            calls.append((args, kwargs))
            return original(*args, **kwargs)

        monkeypatch.setattr(
            surgical_splicer.TextDiffPatcher, "apply_search_replace", staticmethod(spy)
        )
        adapter = SearchReplacePatchAdapter()
        response = self._patch(
            simple_task, "        if balance <= 0:", "        if balance < 0:"
        )
        adapter.parse_and_apply(simple_task, 1, response)
        assert calls, "the project TextDiffPatcher was not used"


class TestItmsSpliceAdapter:

    def test_body_replacement_is_spliced(self, simple_task):
        adapter = ItmsAstSpliceAdapter()
        response = "```python\n    result = []\n    for x in xs:\n        if x < 0:\n            result.append(x)\n    return result\n```"
        result = adapter.parse_and_apply(simple_task, 1, response)
        assert result.applied is True
        assert "def below_zero" in result.candidate_source

    def test_full_node_replacement_is_spliced(self, simple_task):
        adapter = ItmsAstSpliceAdapter()
        response = (
            "```python\ndef below_zero(xs):\n"
            '    """Return the negative values."""\n'
            "    return [x for x in xs if x < 0]\n```"
        )
        result = adapter.parse_and_apply(simple_task, 1, response)
        assert result.applied is True
        assert "def below_zero" in result.candidate_source

    def test_uses_the_project_splicer_and_locator(self, simple_task, monkeypatch):
        import surgical_splicer

        spliced = []
        original_splice = surgical_splicer.ASTSurgicalSplicer.splice
        located = []
        original_locate = surgical_splicer.ASTNodeLocator.locate_target

        def splice_spy(cls, *args, **kwargs):
            spliced.append(args)
            return original_splice(*args, **kwargs)

        def locate_spy(*args, **kwargs):
            located.append(args)
            return original_locate(*args, **kwargs)

        monkeypatch.setattr(
            surgical_splicer.ASTSurgicalSplicer, "splice", classmethod(splice_spy)
        )
        monkeypatch.setattr(
            surgical_splicer.ASTNodeLocator, "locate_target", staticmethod(locate_spy)
        )

        adapter = ItmsAstSpliceAdapter()
        response = "```python\n    return [x for x in xs if x < 0]\n```"
        adapter.parse_and_apply(simple_task, 1, response)
        assert located, "ASTNodeLocator was not used"
        assert spliced, "ASTSurgicalSplicer.splice was not used"

    def test_nested_function_replacement_stays_nested(self, repository):
        """
        A body-only replacement that declares a nested function must keep the
        nested function inside the target body.

        (This used to be a documented ITMS limitation: the nested ``def`` was
        hoisted to module level and the original method body was overwritten
        while the splice still reported success. The defect has been fixed;
        the historical benchmark runs measured against the unfixed splicer are
        left untouched.)
        """
        from surgical_splicer import ASTNodeLocator, ASTSurgicalSplicer

        source = (
            "class Outer:\n"
            "    class Inner:\n"
            "        def method(self, x):\n"
            "            def helper(v):\n"
            "                return v * 2\n"
            "            return helper(x) + 1\n"
        )
        target = ASTNodeLocator.locate_target(source, "Outer.Inner.method")
        ok, spliced, _ = ASTSurgicalSplicer.splice(
            source,
            target,
            "    def helper(v):\n        return v * 2\n    return helper(x) + 5\n",
        )
        assert ok is True
        # The nested def must NOT be hoisted to module level any more.
        tree = ast.parse(spliced)
        module_level = [
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        ]
        assert "helper" not in module_level
        # The target method survives and keeps the nested helper.
        inner_cls = tree.body[0].body[0]
        method = inner_cls.body[0]
        assert method.name == "method"
        assert any(
            isinstance(node, ast.FunctionDef) and node.name == "helper"
            for node in method.body
        )

    def test_syntax_error_in_replacement_is_reported(self, simple_task):
        adapter = ItmsAstSpliceAdapter()
        response = "```python\n    return [x for x in xs if x < 0\n```"
        result = adapter.parse_and_apply(simple_task, 1, response)
        assert result.applied is False
        assert result.failure_reason in (
            FailureReason.SYNTAX_ERROR,
            FailureReason.SPLICE_FAILED,
        )
