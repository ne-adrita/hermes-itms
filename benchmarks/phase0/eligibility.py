"""
Phase 0 task-eligibility rules (AST based).

The ITMS skeletonizer replaces the body of every non-target function with
``...``.  A task is therefore only a fair comparison between
``full_file_regeneration``, ``search_replace_patch`` and ``itms_ast_splice`` if
the target function can be repaired using *only* the information the model is
given (the problem prompt, the docstring and the surrounding file).

The checks implemented here are deliberately conservative.  They are reported
per candidate in the validation output so that any residual limitation is
visible rather than silently assumed away.
"""

from __future__ import annotations

import ast
import builtins
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

__all__ = [
    "EligibilityResult",
    "check_self_containment",
    "module_level_definitions",
]

#: Names that are provided by the Python standard library / builtins and are
#: therefore always available to the target function.
_BUILTIN_NAMES: Set[str] = set(dir(builtins)) | {
    "List", "Dict", "Tuple", "Set", "Optional", "Any", "Union", "Callable",
    "Iterable", "Sequence", "Mapping", "Iterator", "FrozenSet", "Generator",
    "TypeVar", "DefaultDict", "Deque",
}


@dataclass
class EligibilityResult:
    """Outcome of the self-containment analysis for one candidate task."""

    eligible: bool
    reasons: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "eligible": self.eligible,
            "reasons": list(self.reasons),
            "details": dict(self.details),
        }


def module_level_definitions(tree: ast.Module) -> Dict[str, ast.AST]:
    """Return the module-level functions and classes of a parsed module."""
    defined: Dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.setdefault(node.name, node)
    return defined


def _all_defined_functions(tree: ast.Module) -> Dict[str, ast.AST]:
    defined: Dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined.setdefault(node.name, node)
    return defined


def _free_names(node: ast.AST) -> Set[str]:
    """Names loaded inside ``node`` that are not bound inside ``node``."""
    bound: Set[str] = set()
    loaded: Set[str] = set()

    class Collector(ast.NodeVisitor):
        def visit_arg(self, n: ast.arg) -> None:
            bound.add(n.arg)
            if n.annotation is not None:
                self.visit(n.annotation)

        def visit_Name(self, n: ast.Name) -> None:
            if isinstance(n.ctx, ast.Store):
                bound.add(n.id)
            else:
                loaded.add(n.id)
            self.generic_visit(n)

        def visit_FunctionDef(self, n: ast.FunctionDef) -> None:
            bound.add(n.name)
            self._args(n)
            for d in n.decorator_list:
                self.visit(d)
            for s in n.body:
                self.visit(s)

        def visit_AsyncFunctionDef(self, n: ast.AsyncFunctionDef) -> None:
            self.visit_FunctionDef(n)  # type: ignore[arg-type]

        def visit_ClassDef(self, n: ast.ClassDef) -> None:
            bound.add(n.name)
            self.generic_visit(n)

        def visit_Lambda(self, n: ast.Lambda) -> None:
            self._args(n)
            self.visit(n.body)

        def _args(self, n) -> None:
            a = n.args
            for arg in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs):
                self.visit_arg(arg)
            if a.vararg is not None:
                self.visit_arg(a.vararg)
            if a.kwarg is not None:
                self.visit_arg(a.kwarg)
            for d in list(a.defaults) + [d for d in a.kw_defaults if d is not None]:
                self.visit(d)

        def visit_ExceptHandler(self, n: ast.ExceptHandler) -> None:
            if n.name:
                bound.add(n.name)
            self.generic_visit(n)

        def visit_Import(self, n: ast.Import) -> None:
            for alias in n.names:
                bound.add(alias.asname or alias.name.split(".")[0])

        def visit_ImportFrom(self, n: ast.ImportFrom) -> None:
            for alias in n.names:
                bound.add(alias.asname or alias.name)

        def visit_Global(self, n: ast.Global) -> None:
            bound.update(n.names)

        def visit_Nonlocal(self, n: ast.Nonlocal) -> None:
            bound.update(n.names)

    Collector().visit(node)
    return loaded - bound


def check_self_containment(
    source: str,
    target_function: str,
) -> EligibilityResult:
    """Check that the target function can be repaired in isolation.

    The following conditions are enforced (all AST based):

    1.  The module parses.
    2.  The target function exists exactly once at module level.
    3.  The target function does not call any function defined in the same
        file (the ITMS skeletonizer would strip that implementation to ``...``,
        which would make the task unanswerable for a repair agent).
    4.  The target function does not reference any other module-level function
        or class defined in the same file.
    5.  Every other free name used by the target function is either a Python
        builtin or imported by the module itself.
    """
    reasons: List[str] = []
    details: Dict[str, Any] = {}

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return EligibilityResult(False, [f"source_does_not_parse: {exc}"], details)

    module_defs = module_level_definitions(tree)
    all_funcs = _all_defined_functions(tree)

    targets = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == target_function
    ]
    if len(targets) != 1:
        return EligibilityResult(
            False,
            [f"target_function_not_uniquely_defined_at_module_level: {target_function}"],
            details,
        )
    target = targets[0]
    details["target_function"] = target_function

    # 3. calls to same-file functions
    called: Set[str] = set()
    for node in ast.walk(target):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                called.add(node.func.id)
            elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                called.add(node.func.value.id)
    same_file_calls = sorted(called & set(all_funcs))
    details["same_file_calls"] = same_file_calls
    if same_file_calls:
        reasons.append(f"calls_function_defined_in_same_file: {same_file_calls}")

    # 4./5. free names
    free = _free_names(target)
    same_file_refs = sorted(free & set(all_funcs))
    details["same_file_free_name_refs"] = same_file_refs
    if same_file_refs:
        reasons.append(f"references_name_defined_in_same_file: {same_file_refs}")

    module_bound: Set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_bound.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                module_bound.add(alias.asname or alias.name)
    module_bound.update(module_defs.keys())

    unresolved = sorted(free - module_bound - _BUILTIN_NAMES)
    details["unresolved_free_names"] = unresolved
    if unresolved:
        reasons.append(f"depends_on_names_not_provided_by_module_or_builtins: {unresolved}")

    # Nested helper functions inside the target are fine (they travel with the
    # function), but we record them because the known ITMS splicer limitation
    # concerns body-only replacements that declare a nested function.
    nested = [
        node.name
        for node in ast.walk(target)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node is not target
    ]
    details["nested_function_defs"] = nested

    details["module_level_definitions"] = sorted(module_defs.keys())
    details["has_other_module_level_functions"] = sorted(
        name for name in module_defs if isinstance(module_defs[name], (ast.FunctionDef, ast.AsyncFunctionDef))
        and name != target_function
    )

    return EligibilityResult(not reasons, reasons, details)


def docstring_of(tree: ast.Module, target_function: str) -> Optional[str]:
    """Return the raw docstring of the target function (if any)."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == target_function:
            body = list(node.body)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    return body[0].value.value
            return None
    return None
