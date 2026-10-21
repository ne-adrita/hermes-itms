"""
Phase 0 deterministic mutation engine.

This module implements a small, fully deterministic catalogue of AST-level
mutation rules used to derive *defective* variants of HumanEval reference
implementations.  Every mutation is:

*   expressed as a named rule plus explicit parameters,
*   reproducible from (source text, target function, rule, parameters),
*   applied to a single AST node located by a deterministic source-order index,
*   byte-preserving everywhere except the single mutated node,
*   guaranteed to leave the module syntactically valid.

The engine never touches docstrings: candidate nodes are collected from the
*body* of the target function only, and the docstring expression is excluded
explicitly.

No network access, no model calls, no third-party dependencies.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

__all__ = [
    "MUTATION_RULES",
    "MutationError",
    "MutationSpec",
    "apply_mutation",
    "enumerate_candidate_mutations",
    "describe_rule",
]


class MutationError(Exception):
    """Raised when a requested mutation cannot be applied deterministically."""


# --------------------------------------------------------------------------
# Text-position helpers
# --------------------------------------------------------------------------

def _line_offsets(source: str) -> List[int]:
    """Return the byte offset of the start of each 1-indexed line."""
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line.encode("utf-8")))
    return offsets


def _absolute_offset(source: str, lineno: int, col_offset: int) -> int:
    """Convert an AST (lineno, col_offset) pair into an absolute byte offset.

    ``col_offset`` is a UTF-8 byte offset, so line contents are encoded before
    the column is applied.
    """
    lines = source.splitlines(keepends=True)
    if lineno - 1 >= len(lines):
        raise MutationError(f"line {lineno} out of range")
    base = sum(len(line.encode("utf-8")) for line in lines[: lineno - 1])
    return base + col_offset


def _replace_span(source: str, start: int, end: int, new_text: str) -> str:
    """Replace the byte span [start, end) with ``new_text``."""
    data = source.encode("utf-8")
    return (data[:start] + new_text.encode("utf-8") + data[end:]).decode("utf-8")


# --------------------------------------------------------------------------
# Rule definitions
# --------------------------------------------------------------------------

COMPARISON_OPS: Dict[str, str] = {
    "<": "<=",
    "<=": "<",
    ">": ">=",
    ">=": ">",
    "==": "!=",
    "!=": "==",
    "in": "not in",
    "not in": "in",
    "is": "is not",
    "is not": "is",
}

ARITHMETIC_OPS: Dict[str, str] = {
    "+": "-",
    "-": "+",
    "*": "//",
    "//": "*",
    "%": "//",
    "/": "*",
    "**": "*",
}

BOOLEAN_OPS: Dict[str, str] = {
    "and": "or",
    "or": "and",
}

_COMPARISON_SYMBOLS = {
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.Eq: "==",
    ast.NotEq: "!=",
    ast.Is: "is",
    ast.IsNot: "is not",
    ast.In: "in",
    ast.NotIn: "not in",
}

_ARITHMETIC_SYMBOLS = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
    ast.FloorDiv: "//",
    ast.Mod: "%",
    ast.Pow: "**",
}


@dataclass(frozen=True)
class MutationRule:
    """A named, parameterised mutation rule."""

    name: str
    category: str
    description: str
    parameters: Tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "parameters": list(self.parameters),
        }


MUTATION_RULES: Tuple[MutationRule, ...] = (
    MutationRule(
        name="comparison_operator_swap",
        category="logic",
        description=(
            "Replace the comparison operator of a comparison expression with a "
            "different, logically adjacent operator (e.g. '>' becomes '>=')."
        ),
        parameters=("from_op", "to_op", "occurrence"),
    ),
    MutationRule(
        name="off_by_one_boundary",
        category="boundary",
        description=(
            "Shift by +/- 1 an integer literal that sits in a boundary "
            "position (a comparison operand, a slice bound, a subscript "
            "index, a range() argument, or an operand of '+'/'-'), producing "
            "an off-by-one error."
        ),
        parameters=("delta", "occurrence"),
    ),
    MutationRule(
        name="incorrect_constant",
        category="constant",
        description=(
            "Replace a numeric literal with a different numeric literal, "
            "changing a hard-coded constant."
        ),
        parameters=("new_value", "occurrence"),
    ),
    MutationRule(
        name="arithmetic_operator_swap",
        category="arithmetic",
        description=(
            "Replace the arithmetic operator of a binary expression with a "
            "different operator (e.g. '+' becomes '-')."
        ),
        parameters=("from_op", "to_op", "occurrence"),
    ),
    MutationRule(
        name="boolean_operator_swap",
        category="logic",
        description=(
            "Replace a boolean connective ('and'/'or') with its counterpart, "
            "changing the short-circuit logic of a condition."
        ),
        parameters=("from_op", "to_op", "occurrence"),
    ),
)

_RULES_BY_NAME: Dict[str, MutationRule] = {r.name: r for r in MUTATION_RULES}


def describe_rule(name: str) -> MutationRule:
    if name not in _RULES_BY_NAME:
        raise MutationError(f"unknown mutation rule: {name}")
    return _RULES_BY_NAME[name]



_TOKEN_PATTERNS: Dict[str, str] = {
    "<": r"<(?![=])",
    "<=": r"<=",
    ">": r">(?!=)",
    ">=": r">=",
    "==": r"==",
    "!=": r"!=",
    "in": r"\bin\b",
    "not in": r"\bnot\s+in\b",
    "is": r"\bis\b",
    "is not": r"\bis\s+not\b",
    "+": r"\+",
    "-": r"-",
    "*": r"\*(?!\*)",
    "/": r"/(?!/)",
    "//": r"//",
    "%": r"%",
    "**": r"\*\*",
    "and": r"\band\b",
    "or": r"\bor\b",
}


def _node_start(source: str, node: ast.AST) -> int:
    return _absolute_offset(source, node.lineno, node.col_offset)


def _node_end(source: str, node: ast.AST) -> int:
    return _absolute_offset(source, node.end_lineno, node.end_col_offset)


def _operator_span(source: str, left: ast.AST, right: ast.AST, symbol: str) -> Tuple[int, int]:
    """Locate the operator token between two operands.

    CPython does not attach source positions to operator nodes, so the token is
    located textually inside the gap between the two operands.  The gap is
    unambiguous: it contains exactly one operator.
    """
    pattern = _TOKEN_PATTERNS.get(symbol)
    if pattern is None:
        raise MutationError(f"no token pattern for operator {symbol!r}")
    gap_start = _node_end(source, left)
    gap_end = _node_start(source, right)
    gap = source[gap_start:gap_end]
    match = re.search(pattern, gap)
    if match is None:
        raise MutationError(f"operator {symbol!r} not found between operands")
    return gap_start + match.start(), gap_start + match.end()

# --------------------------------------------------------------------------
# Node collection
# --------------------------------------------------------------------------

def _target_function_node(tree: ast.Module, target_name: str) -> ast.FunctionDef:
    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == target_name
    ]
    if not matches:
        raise MutationError(f"target function {target_name!r} not found")
    if len(matches) > 1:
        raise MutationError(f"target function {target_name!r} is ambiguous")
    return matches[0]


def _docstring_nodes(func: ast.FunctionDef) -> set:
    """Return the set of AST node ids that belong to the docstring."""
    ids = set()
    body = list(func.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        if isinstance(body[0].value.value, str):
            for node in ast.walk(body[0]):
                ids.add(id(node))
    return ids


def _body_nodes(func: ast.FunctionDef) -> List[ast.AST]:
    """All AST nodes inside the function body, in deterministic source order.

    The docstring is excluded.  Decorators, annotations and default arguments
    are excluded as well: only executable body statements are mutated.
    """
    excluded = _docstring_nodes(func)
    nodes: List[ast.AST] = []
    for stmt in func.body:
        for node in ast.walk(stmt):
            if id(node) in excluded:
                continue
            nodes.append(node)
    nodes.sort(key=lambda n: (getattr(n, "lineno", 0), getattr(n, "col_offset", 0)))
    return nodes


def _comparison_candidates(func: ast.FunctionDef) -> List[ast.Compare]:
    return [n for n in _body_nodes(func) if isinstance(n, ast.Compare)]


def _arithmetic_candidates(func: ast.FunctionDef) -> List[ast.BinOp]:
    return [n for n in _body_nodes(func) if isinstance(n, ast.BinOp)]


def _boolean_candidates(func: ast.FunctionDef) -> List[ast.BoolOp]:
    return [n for n in _body_nodes(func) if isinstance(n, ast.BoolOp)]


def _parent_map(func: ast.FunctionDef) -> Dict[int, ast.AST]:
    """Map ``id(node) -> parent`` for every node inside the function."""
    parents: Dict[int, ast.AST] = {}
    for stmt in func.body:
        for parent in ast.walk(stmt):
            for child in ast.iter_child_nodes(parent):
                parents[id(child)] = parent
    return parents


def _is_boundary_integer_context(
    node: ast.Constant, parents: Dict[int, ast.AST]
) -> bool:
    """Is this integer literal used in a boundary-like position?

    Boundary positions are comparisons, slice bounds, subscript indices,
    ``range()`` arguments and the operand of a ``+``/``-`` expression.  Literals
    in "plain" positions (initialisers, dictionary values, ...) are excluded
    from the ``off_by_one_boundary`` rule because changing them is an
    *incorrect constant*, not an off-by-one error.
    """
    parent = parents.get(id(node))
    if parent is None:
        return False
    if isinstance(parent, ast.Compare):
        return True
    if isinstance(parent, ast.Subscript):
        return True
    if isinstance(parent, ast.Slice):
        return True
    if isinstance(parent, ast.BinOp) and isinstance(parent.op, (ast.Add, ast.Sub)):
        return True
    if isinstance(parent, ast.Call):
        func = parent.func
        if isinstance(func, ast.Name) and func.id in ("range", "len"):
            return True
    return False


def _numeric_constant_candidates(func: ast.FunctionDef) -> List[ast.Constant]:
    out = []
    for n in _body_nodes(func):
        if not isinstance(n, ast.Constant):
            continue
        if isinstance(n.value, bool):
            continue
        if not isinstance(n.value, (int, float)):
            continue
        out.append(n)
    return out


def _off_by_one_candidates(func: ast.FunctionDef) -> List[ast.Constant]:
    parents = _parent_map(func)
    return [
        node
        for node in _numeric_constant_candidates(func)
        if isinstance(node.value, int)
        and not isinstance(node.value, bool)
        and _is_boundary_integer_context(node, parents)
    ]


# --------------------------------------------------------------------------
# Application
# --------------------------------------------------------------------------

@dataclass
class MutationSpec:
    """A concrete, fully parameterised mutation."""

    rule: str
    params: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"rule": self.rule, "parameters": dict(self.params)}

    @property
    def key(self) -> str:
        items = ",".join(f"{k}={self.params[k]!r}" for k in sorted(self.params))
        return f"{self.rule}({items})"


def _apply_comparison(source: str, func: ast.FunctionDef, spec: MutationSpec) -> str:
    cands = _comparison_candidates(func)
    occ = spec.params["occurrence"]
    if occ >= len(cands):
        raise MutationError("occurrence out of range")
    node = cands[occ]
    from_op = spec.params["from_op"]
    to_op = spec.params["to_op"]
    idx = spec.params.get("op_index", 0)
    if idx >= len(node.ops):
        raise MutationError("comparison operator index out of range")
    if _COMPARISON_SYMBOLS.get(type(node.ops[idx])) != from_op:
        raise MutationError(f"operator {from_op!r} not found at occurrence {occ}")
    left = node.left if idx == 0 else node.comparators[idx - 1]
    right = node.comparators[idx]
    start, end = _operator_span(source, left, right, from_op)
    return _replace_span(source, start, end, to_op)


def _apply_arithmetic(source: str, func: ast.FunctionDef, spec: MutationSpec) -> str:
    cands = _arithmetic_candidates(func)
    occ = spec.params["occurrence"]
    if occ >= len(cands):
        raise MutationError("occurrence out of range")
    node = cands[occ]
    from_op = spec.params["from_op"]
    to_op = spec.params["to_op"]
    sym = _ARITHMETIC_SYMBOLS.get(type(node.op))
    if sym != from_op:
        raise MutationError(f"operator {from_op!r} not found at occurrence {occ}")
    start, end = _operator_span(source, node.left, node.right, from_op)
    return _replace_span(source, start, end, to_op)


def _apply_boolean(source: str, func: ast.FunctionDef, spec: MutationSpec) -> str:
    cands = _boolean_candidates(func)
    occ = spec.params["occurrence"]
    if occ >= len(cands):
        raise MutationError("occurrence out of range")
    node = cands[occ]
    from_op = spec.params["from_op"]
    to_op = spec.params["to_op"]
    if not isinstance(node.op, (ast.And, ast.Or)):
        raise MutationError("unsupported boolean operator node")
    current = "and" if isinstance(node.op, ast.And) else "or"
    if current != from_op:
        raise MutationError(f"operator {from_op!r} not found at occurrence {occ}")
    if len(node.values) < 2:
        raise MutationError("boolean operator requires at least two operands")
    start, end = _operator_span(source, node.values[0], node.values[1], from_op)
    return _replace_span(source, start, end, to_op)


def _apply_off_by_one(source: str, func: ast.FunctionDef, spec: MutationSpec) -> str:
    cands = _off_by_one_candidates(func)
    occ = spec.params["occurrence"]
    if occ >= len(cands):
        raise MutationError("occurrence out of range")
    node = cands[occ]
    if not isinstance(node.value, int) or isinstance(node.value, bool):
        raise MutationError("off_by_one_boundary requires an integer literal")
    delta = spec.params["delta"]
    new_value = node.value + delta
    start = _absolute_offset(source, node.lineno, node.col_offset)
    end = _absolute_offset(source, node.end_lineno, node.end_col_offset)
    return _replace_span(source, start, end, repr(new_value))


def _apply_incorrect_constant(source: str, func: ast.FunctionDef, spec: MutationSpec) -> str:
    cands = _numeric_constant_candidates(func)
    occ = spec.params["occurrence"]
    if occ >= len(cands):
        raise MutationError("occurrence out of range")
    node = cands[occ]
    new_value = spec.params["new_value"]
    if isinstance(node.value, int) and isinstance(new_value, float):
        raise MutationError("type mismatch between integer literal and float value")
    start = _absolute_offset(source, node.lineno, node.col_offset)
    end = _absolute_offset(source, node.end_lineno, node.end_col_offset)
    text = repr(new_value)
    return _replace_span(source, start, end, text)


_APPLIERS: Dict[str, Callable[[str, ast.FunctionDef, MutationSpec], str]] = {
    "comparison_operator_swap": _apply_comparison,
    "arithmetic_operator_swap": _apply_arithmetic,
    "boolean_operator_swap": _apply_boolean,
    "off_by_one_boundary": _apply_off_by_one,
    "incorrect_constant": _apply_incorrect_constant,
}


def apply_mutation(
    source: str,
    target_function: str,
    rule: str,
    params: Dict[str, Any],
) -> Tuple[str, Dict[str, Any]]:
    """Apply a named mutation rule to ``source``.

    Returns ``(mutated_source, applied_params)``.  Raises :class:`MutationError`
    if the mutation cannot be applied deterministically.
    """
    if rule not in _APPLIERS:
        raise MutationError(f"unknown mutation rule: {rule}")
    tree = ast.parse(source)
    func = _target_function_node(tree, target_function)
    spec = MutationSpec(rule=rule, params=dict(params))
    mutated = _APPLIERS[rule](source, func, spec)
    # The result must still parse.
    ast.parse(mutated)
    return mutated, dict(params)


# --------------------------------------------------------------------------
# Candidate enumeration (used by the task builder)
# --------------------------------------------------------------------------

def enumerate_candidate_mutations(
    source: str,
    target_function: str,
    max_occurrences: int = 3,
) -> List[MutationSpec]:
    """Enumerate the deterministic candidate mutations for a source file.

    The enumeration order is fixed: rules in :data:`MUTATION_RULES` order and,
    within a rule, occurrences in ascending source order.  The builder relies on
    this order being stable across runs.
    """
    tree = ast.parse(source)
    func = _target_function_node(tree, target_function)
    specs: List[MutationSpec] = []

    for rule in MUTATION_RULES:
        if rule.name == "comparison_operator_swap":
            cands = _comparison_candidates(func)
            for occ, node in enumerate(cands[:max_occurrences]):
                for i, op in enumerate(node.ops):
                    sym = _COMPARISON_SYMBOLS.get(type(op))
                    if sym is None or sym not in COMPARISON_OPS:
                        continue
                    to_op = COMPARISON_OPS[sym]
                    specs.append(
                        MutationSpec(
                            rule=rule.name,
                            params={"from_op": sym, "to_op": to_op, "occurrence": occ, "op_index": i},
                        )
                    )
                    break
        elif rule.name == "arithmetic_operator_swap":
            cands = _arithmetic_candidates(func)
            for occ, node in enumerate(cands[:max_occurrences]):
                for i, op in enumerate([node.op]):
                    sym = _ARITHMETIC_SYMBOLS.get(type(op))
                    if sym is None or sym not in ARITHMETIC_OPS:
                        continue
                    to_op = ARITHMETIC_OPS[sym]
                    specs.append(
                        MutationSpec(
                            rule=rule.name,
                            params={"from_op": sym, "to_op": to_op, "occurrence": occ},
                        )
                    )
                    break
        elif rule.name == "boolean_operator_swap":
            cands = _boolean_candidates(func)
            for occ, node in enumerate(cands[:max_occurrences]):
                current = "and" if isinstance(node.op, ast.And) else "or"
                specs.append(
                    MutationSpec(
                        rule=rule.name,
                        params={"from_op": current, "to_op": BOOLEAN_OPS[current], "occurrence": occ},
                    )
                )
        elif rule.name == "off_by_one_boundary":
            cands = _off_by_one_candidates(func)
            for occ, node in enumerate(cands[:max_occurrences]):
                if not isinstance(node.value, int) or isinstance(node.value, bool):
                    continue
                specs.append(
                    MutationSpec(
                        rule=rule.name,
                        params={"delta": 1, "occurrence": occ},
                    )
                )
        elif rule.name == "incorrect_constant":
            cands = _numeric_constant_candidates(func)
            for occ, node in enumerate(cands[:max_occurrences]):
                if isinstance(node.value, int):
                    new_value = node.value + 1
                else:
                    new_value = node.value * 2 if node.value != 0 else 1.0
                specs.append(
                    MutationSpec(
                        rule=rule.name,
                        params={"new_value": new_value, "occurrence": occ},
                    )
                )
    return specs
