import ast
from typing import Optional, Set

class SymbolReferenceCollector(ast.NodeVisitor):
    """Collects all identifier names and type references in an AST subtree."""
    def __init__(self):
        self.references: Set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        self.references.add(node.id)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self.references.add(node.attr)
        self.generic_visit(node)

    def visit_arg(self, node: ast.arg) -> None:
        if node.annotation:
            self.visit(node.annotation)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.annotation:
            self.visit(node.annotation)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for base in node.bases:
            if isinstance(base, ast.Name):
                self.references.add(base.id)
            elif isinstance(base, ast.Attribute):
                self.references.add(base.attr)
        self.generic_visit(node)


class ContextSkeletonizer(ast.NodeTransformer):
    """
    Scans a Python source file, preserves signatures, classes, and types,
    and replaces the implementation of non-target functions with Ellipsis (...).
    Optionally strips docstrings and prunes unreferenced classes/functions.
    """
    def __init__(
        self,
        target_function_name: str,
        strip_docstrings: bool = True,
        prune_unreachable: bool = False,
        referenced_symbols: Optional[Set[str]] = None
    ):
        super().__init__()
        self.target_name = target_function_name.strip() if target_function_name else ""
        self.strip_docstrings = strip_docstrings
        self.prune_unreachable = prune_unreachable
        self.referenced_symbols = referenced_symbols or set()

    def _is_target(self, func_name: str) -> bool:
        if not self.target_name:
            return False
        if func_name == self.target_name:
            return True
        if self.target_name.endswith("." + func_name):
            return True
        return False

    def visit_Module(self, node: ast.Module) -> ast.Module:
        self.generic_visit(node)
        
        # 1. Strip module docstring if requested
        if self.strip_docstrings and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                node.body = node.body[1:]

        # 2. Prune completely unreachable classes and functions
        if self.prune_unreachable and self.referenced_symbols is not None:
            filtered_body = []
            for stmt in node.body:
                if isinstance(stmt, ast.ClassDef):
                    class_contains_target = any(
                        isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and self._is_target(n.name)
                        for n in stmt.body
                    )
                    if class_contains_target or (stmt.name in self.referenced_symbols) or self._is_target(stmt.name):
                        filtered_body.append(stmt)
                elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if self._is_target(stmt.name) or (stmt.name in self.referenced_symbols):
                        filtered_body.append(stmt)
                else:
                    filtered_body.append(stmt)
            node.body = filtered_body

        return node

    def visit_ClassDef(self, node: ast.ClassDef) -> Optional[ast.ClassDef]:
        self.generic_visit(node)
        if self.strip_docstrings and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                if len(node.body) > 1:
                    node.body = node.body[1:]
                else:
                    node.body = [ast.Expr(value=ast.Constant(value=Ellipsis))]
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        if self._is_target(node.name):
            return node
        node.body = [ast.Expr(value=ast.Constant(value=Ellipsis))]
        return node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AsyncFunctionDef:
        if self._is_target(node.name):
            return node
        node.body = [ast.Expr(value=ast.Constant(value=Ellipsis))]
        return node


def extract_referenced_symbols(source_code: str, target_name: str, task_context: str = "") -> Set[str]:
    """
    Computes the transitive fixed-point closure of all symbols referenced
    by the target function or class, including base classes, type hints,
    and task description keywords.
    """
    refs: Set[str] = set()
    if task_context:
        for word in task_context.replace(".", " ").replace("(", " ").replace(")", " ").split():
            clean = "".join(c for c in word if c.isalnum() or c == "_")
            if clean:
                refs.add(clean)

    try:
        tree = ast.parse(source_code)
    except Exception:
        return refs

    # Index class and function definitions across the module
    class_map = {}
    func_map = {}
    clean_target = target_name.split(".")[-1].strip() if target_name else ""

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            class_map[node.name] = node
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_map.setdefault(node.name, []).append(node)

    # Collect initial symbols directly referenced in target scope
    collector = SymbolReferenceCollector()
    target_nodes = []
    if clean_target in class_map:
        target_nodes.append(class_map[clean_target])
    if clean_target in func_map:
        target_nodes.extend(func_map[clean_target])

    for tn in target_nodes:
        collector.visit(tn)

    refs.update(collector.references)

    # Transitive expansion to fixed point
    prev_len = -1
    while len(refs) != prev_len:
        prev_len = len(refs)
        newly_found = set()
        for sym in refs:
            if sym in class_map:
                cls_node = class_map[sym]
                cls_col = SymbolReferenceCollector()
                cls_col.visit(cls_node)
                newly_found.update(cls_col.references)
            if sym in func_map:
                for fn in func_map[sym]:
                    fn_col = SymbolReferenceCollector()
                    fn_col.visit(fn)
                    newly_found.update(fn_col.references)
        refs.update(newly_found)

    return refs


def skeletonize_code(
    source_code: str,
    target_function_name: str,
    strip_docstrings: bool = True,
    prune_unreachable: bool = False,
    task_description: str = ""
) -> str:
    """
    Parses source code, validates syntax, and returns the skeletonized representation.
    - Strips function bodies into ...
    - Strips docstrings
    - Optionally prunes unreferenced classes/functions via AST reachability analysis.
    """
    if not source_code or not source_code.strip():
        return source_code

    try:
        parsed_ast = ast.parse(source_code)
    except Exception:
        return source_code

    referenced_symbols = None
    if prune_unreachable:
        referenced_symbols = extract_referenced_symbols(source_code, target_function_name, task_description)

    transformer = ContextSkeletonizer(
        target_function_name,
        strip_docstrings=strip_docstrings,
        prune_unreachable=prune_unreachable,
        referenced_symbols=referenced_symbols
    )
    transformed_ast = transformer.visit(parsed_ast)
    ast.fix_missing_locations(transformed_ast)
    return ast.unparse(transformed_ast)
