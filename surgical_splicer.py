import ast
import re
import textwrap
from dataclasses import dataclass
from typing import Optional, List, Tuple, Dict, Any

@dataclass
class SurgicalTarget:
    """Represents a target AST node located for surgical editing."""
    name: str
    qualified_name: str
    node_type: str
    start_line: int          # 1-indexed, inclusive (includes decorators)
    end_line: int            # 1-indexed, inclusive
    body_start_line: int     # 1-indexed, first line of statements
    base_indentation: str    # Leading whitespace of the def/class line
    original_text: str       # Exact source text slice
    parent_class: Optional[str] = None
    body_indentation: str = ""   # Leading whitespace of the first body statement


class ASTNodeLocator:
    """
    Scans a Python source file to identify the exact line boundaries
    and indentation level of any function, method, or class.
    Supports multi-level qualified names (e.g. 'Outer.Inner.method')
    as well as standalone function and class lookups.

    Resolution rules (deterministic, ambiguity-safe):
      1. An exact qualified path match wins ('AccountLedger.reconcile_balance',
         'Outer.Inner.method', or a module level 'foo').
      2. Otherwise, for a single-segment name, every node with that name is
         collected.  If more than one exists the name is *ambiguous* and no
         target is returned, so a caller can never silently edit the wrong
         function.
    """
    @staticmethod
    def _collect_entries(source_code: str) -> List[Tuple[str, ast.AST, Optional[str]]]:
        """
        Returns (qualified_path, node, nearest_enclosing_class_path) for every
        function/class definition in the file, in source order.
        """
        tree = ast.parse(source_code)
        entries: List[Tuple[str, ast.AST, Optional[str]]] = []

        def walk(nodes, prefix: str, class_prefix: Optional[str]) -> None:
            for node in nodes:
                if isinstance(node, ast.ClassDef):
                    path = f"{prefix}.{node.name}" if prefix else node.name
                    entries.append((path, node, class_prefix))
                    walk(node.body, path, path)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    path = f"{prefix}.{node.name}" if prefix else node.name
                    entries.append((path, node, class_prefix))
                    walk(node.body, path, class_prefix)

        walk(tree.body, "", None)
        return entries

    @staticmethod
    def _build_target(
        source_code: str,
        path: str,
        node: ast.AST,
        class_prefix: Optional[str],
        lines: List[str]
    ) -> SurgicalTarget:
        num_lines = len(lines)

        start_line = node.lineno
        if getattr(node, "decorator_list", None):
            start_line = min(start_line, node.decorator_list[0].lineno)

        end_line = getattr(node, "end_lineno", None)
        if end_line is None:
            end_line = num_lines

        def_line_text = lines[node.lineno - 1] if 1 <= node.lineno <= num_lines else ""
        indentation = def_line_text[: len(def_line_text) - len(def_line_text.lstrip())]

        slice_lines = lines[start_line - 1 : end_line]
        original_text = "".join(slice_lines)

        body = getattr(node, "body", None)
        body_start = body[0].lineno if body else start_line
        body_text = lines[body_start - 1] if 1 <= body_start <= num_lines else ""
        body_indentation = body_text[: len(body_text) - len(body_text.lstrip())]

        return SurgicalTarget(
            name=node.name,
            qualified_name=path,
            node_type=type(node).__name__,
            start_line=start_line,
            end_line=end_line,
            body_start_line=body_start,
            base_indentation=indentation,
            original_text=original_text,
            parent_class=class_prefix,
            body_indentation=body_indentation,
        )

    @staticmethod
    def find_matches(source_code: str, target_name: str) -> List[SurgicalTarget]:
        """
        Returns every target that matches `target_name`.
        An empty list means "not found"; more than one element means the name
        is ambiguous and must not be edited blindly.
        """
        if not source_code or not target_name:
            return []

        try:
            entries = ASTNodeLocator._collect_entries(source_code)
        except Exception:
            return []

        lines = source_code.splitlines(keepends=True)
        query = target_name.strip()

        exact = [e for e in entries if e[0] == query]
        if exact:
            path, node, class_prefix = exact[0]
            return [ASTNodeLocator._build_target(source_code, path, node, class_prefix, lines)]

        if "." in query:
            return []

        by_name = [e for e in entries if getattr(e[1], "name", None) == query]
        return [
            ASTNodeLocator._build_target(source_code, path, node, class_prefix, lines)
            for path, node, class_prefix in by_name
        ]

    @staticmethod
    def locate_target(source_code: str, target_name: str) -> Optional[SurgicalTarget]:
        """
        Locates a function or class by name or qualified name (e.g. 'AccountLedger.reconcile_balance',
        'Outer.Inner.method', 'standalone_func', or 'AccountLedger').
        Returns line boundaries and leading indentation.
        Returns None when the target cannot be found *or* when the name is
        ambiguous (more than one candidate), so that a wrong node is never
        edited by accident.
        """
        matches = ASTNodeLocator.find_matches(source_code, target_name)
        if len(matches) == 1:
            return matches[0]
        return None

    @staticmethod
    def locate_target_ex(
        source_code: str, target_name: str
    ) -> Tuple[Optional[SurgicalTarget], Optional[str]]:
        """
        Same as `locate_target` but returns an explicit diagnostic instead of
        collapsing "not found" and "ambiguous" into None.
        """
        if not source_code or not target_name:
            return None, "No source code or target name supplied."
        try:
            ast.parse(source_code)
        except SyntaxError as e:
            return None, f"Source code is not valid Python: {e.msg} (line {e.lineno})."

        matches = ASTNodeLocator.find_matches(source_code, target_name)
        if not matches:
            return None, f"Target '{target_name}' was not found in the source file."
        if len(matches) > 1:
            names = ", ".join(sorted(m.qualified_name for m in matches))
            return None, (
                f"Target '{target_name}' is ambiguous: it matches {len(matches)} "
                f"definitions ({names}). Use a qualified name."
            )
        return matches[0], None


class ASTSurgicalSplicer:
    """
    Surgically splices candidate replacement code into an existing source file
    using exact line boundaries while strictly preserving comments, formatting,
    and indentation.

    Guarantees
    ----------
    * A **body-only** replacement (no ``def``/``class`` header for the target)
      keeps the original ``def``/``class`` header and nests *any* nested
      function or class declared by the candidate **inside** that body, at the
      body's own indentation level.  Nested helpers are never hoisted to module
      scope and the original body is never silently discarded.
    * A **full-node** replacement (a ``def``/``class`` with the target's own
      name) replaces exactly the target's line range.
    * A candidate that wraps the target inside a redundant class is unwrapped
      to the target definition itself.
    * The spliced file must parse, and the target node must still exist in it.
      An edit that would remove or destroy the target is rejected instead of
      being reported as a success.
    * Everything outside the target's line range (comments, imports, other
      functions, other classes) is preserved byte-for-byte.
    """
    @staticmethod
    def clean_replacement_code(candidate_code: str) -> str:
        """Strips markdown code fences, conversational preamble, and unnecessary wrapping."""
        if not candidate_code or not candidate_code.strip():
            return ""

        # 1. First priority: Extract content inside markdown code blocks
        pattern = r"```+[^\n]*\n(.*?)```+"
        matches = re.findall(pattern, candidate_code, flags=re.DOTALL)
        if matches:
            # Check if joining all blocks produces valid Python (preserves helper functions + main function)
            combined = "\n\n".join(m.strip() for m in matches if m.strip())
            try:
                ast.parse(combined)
                return textwrap.dedent(combined).strip()
            except Exception:
                # Fallback: select best valid block or longest block
                valid_blocks = []
                for m in matches:
                    try:
                        ast.parse(m.strip())
                        valid_blocks.append(m.strip())
                    except Exception:
                        pass
                best_block = max(valid_blocks, key=len) if valid_blocks else max(matches, key=len)
                return textwrap.dedent(best_block).strip()

        # 2. Fallback: Parse line-by-line in case closing fence was truncated
        lines = candidate_code.splitlines()
        code_lines = []
        inside_fence = False
        saw_fence = False

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("```"):
                inside_fence = not inside_fence
                saw_fence = True
                continue
            if inside_fence:
                code_lines.append(line)

        if saw_fence and code_lines:
            return textwrap.dedent("\n".join(code_lines)).strip()

        # 3. If no code fences at all, return raw text dedented and stripped
        return textwrap.dedent(candidate_code).strip()

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _parse_candidate(clean_code: str) -> Tuple[Optional[str], Optional[ast.AST], Optional[str]]:
        """
        Parses a candidate snippet either as a module or as a function body.

        Returns (kind, tree, error) where kind is 'module', 'body' or None.
        'body' is used for snippets such as a bare ``return ...`` which are not
        valid at module level but are valid inside the target function.
        """
        try:
            return ("module", ast.parse(clean_code), None)
        except SyntaxError as module_error:
            module_msg = module_error.msg
            module_lineno = module_error.lineno
        try:
            wrapper = "def _itms_probe():\n" + textwrap.indent(clean_code, "    ")
            return ("body", ast.parse(wrapper), None)
        except SyntaxError as body_error:
            return (
                None,
                None,
                f"SyntaxError in replacement code: {module_msg} (line {module_lineno}).",
            )

    @staticmethod
    def _node_source(lines: List[str], node: ast.AST, fallback_end: int) -> str:
        """Returns the source slice of a node, decorators included, dedented."""
        start = node.lineno
        if getattr(node, "decorator_list", None):
            start = min(start, node.decorator_list[0].lineno)
        end = getattr(node, "end_lineno", None) or fallback_end
        chunk = "".join(lines[start - 1 : end])
        return textwrap.dedent(chunk).strip()

    @staticmethod
    def _indent_block(code: str, indentation: str) -> str:
        """Indents every non-empty line of `code` with `indentation`."""
        out_lines = []
        for line in code.splitlines():
            out_lines.append(f"{indentation}{line}" if line.strip() else "")
        return "\n".join(out_lines) + "\n"

    @staticmethod
    def _split_leading_comment_block(lines: List[str]) -> int:
        """
        Returns the number of leading lines that form a shebang / encoding /
        header comment block.  Module level helpers must be inserted *after*
        that block so the file header is not corrupted.
        """
        count = 0
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                count += 1
                continue
            break
        return count

    @classmethod
    def _classify_candidate(
        cls, clean_code: str, target: SurgicalTarget
    ) -> Tuple[Optional[str], str, List[str], Optional[str]]:
        """
        Classifies a candidate against the located target.

        Returns (kind, payload, module_helpers, error) where kind is one of
        'full_node', 'body_only' or None (on error).
        """
        kind, tree, error = cls._parse_candidate(clean_code)
        if kind is None:
            return None, "", [], error

        if kind == "body":
            # Only valid inside a function body: the target's own header is
            # preserved and the snippet becomes the new body.
            if target.node_type == "ClassDef":
                return None, "", [], (
                    f"Replacement does not define a class named '{target.name}'; "
                    "it cannot be applied as a class body."
                )
            return "body_only", clean_code, [], None

        cand_lines = clean_code.splitlines(keepends=True)
        fallback_end = len(cand_lines)
        is_class_target = target.node_type == "ClassDef"

        matching: Optional[ast.AST] = None
        wrapper_class: Optional[ast.AST] = None
        others: List[ast.AST] = []

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not is_class_target and node.name == target.name and matching is None:
                    matching = node
                else:
                    others.append(node)
            elif isinstance(node, ast.ClassDef):
                if is_class_target and node.name == target.name and matching is None:
                    matching = node
                else:
                    others.append(node)
            else:
                others.append(node)

        if matching is None and not is_class_target:
            # A redundant class wrapper around the target method/function.
            for node in list(others):
                if isinstance(node, ast.ClassDef):
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == target.name:
                            matching = item
                            wrapper_class = node
                            others.remove(node)
                            break
                if matching is not None:
                    break

        if matching is None:
            if is_class_target:
                return None, "", [], (
                    f"Replacement does not define a class named '{target.name}'; "
                    "refusing to apply an edit that would destroy the target."
                )
            # No definition of the target: the candidate is treated as the new
            # *body* of the target.  Any nested def/class it declares stays
            # nested, inside the target body.
            return "body_only", clean_code, [], None

        payload = cls._node_source(cand_lines, matching, fallback_end)
        if not payload.strip():
            return None, "", [], "Replacement code is empty after extraction."

        helpers: List[str] = []
        for node in others:
            if node is wrapper_class:
                continue
            chunk = cls._node_source(cand_lines, node, fallback_end)
            if chunk.strip():
                helpers.append(chunk)

        return "full_node", payload, helpers, None

    # -- public API ------------------------------------------------------

    @classmethod
    def splice(
        cls,
        original_source: str,
        target: SurgicalTarget,
        replacement_code: str
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Slices the target lines from original_source and splices replacement_code.
        Intelligently detects whether the replacement is a full node definition,
        a class wrapper, or a body-only block.  Re-indents the replacement code
        to match the target's own indentation level.
        Returns (success: bool, spliced_code: str, error_message: Optional[str]).
        """
        clean_code = cls.clean_replacement_code(replacement_code)
        if not clean_code:
            return False, original_source, "Replacement code is empty."

        # The target must still exist in the source we are about to edit.
        located, locate_error = ASTNodeLocator.locate_target_ex(
            original_source, target.qualified_name
        )
        if located is None:
            return False, original_source, (
                locate_error
                or f"Target '{target.qualified_name}' could not be located in the source file."
            )
        if (
            located.start_line != target.start_line
            or located.end_line != target.end_line
        ):
            return False, original_source, (
                f"Stale target '{target.qualified_name}': the located node "
                f"(lines {located.start_line}-{located.end_line}) does not match the "
                f"supplied target (lines {target.start_line}-{target.end_line})."
            )

        kind, payload, module_helpers, error = cls._classify_candidate(clean_code, target)
        if kind is None:
            return False, original_source, error or "The replacement could not be applied safely."

        orig_lines = original_source.splitlines(keepends=True)

        if kind == "body_only":
            effective_start = target.body_start_line
            target_indent = target.body_indentation or (target.base_indentation + "    ")
        else:
            effective_start = target.start_line
            target_indent = target.base_indentation

        start_idx = max(0, effective_start - 1)
        end_idx = min(len(orig_lines), target.end_line)

        prefix_lines = orig_lines[:start_idx]
        suffix_lines = orig_lines[end_idx:]

        new_block = cls._indent_block(payload, target_indent)

        # Module level helpers are inserted after any shebang/encoding header.
        if module_helpers:
            header_count = min(cls._split_leading_comment_block(orig_lines), len(prefix_lines))
            helper_text = "\n\n".join(module_helpers) + "\n\n"
            prefix_lines = (
                prefix_lines[:header_count] + [helper_text] + prefix_lines[header_count:]
            )

        spliced_source = "".join(prefix_lines) + new_block + "".join(suffix_lines)

        # 1. The spliced file must be valid Python.
        try:
            ast.parse(spliced_source)
        except SyntaxError as e:
            return False, original_source, f"SyntaxError in spliced output at line {e.lineno}: {e.msg}"
        except Exception as e:
            return False, original_source, f"ParseError in spliced output: {str(e)}"

        # 2. The target must still exist after the edit.
        post, post_error = ASTNodeLocator.locate_target_ex(
            spliced_source, target.qualified_name
        )
        if post is None:
            return False, original_source, (
                f"The edit would remove the target '{target.qualified_name}'. "
                f"Rejected. ({post_error})"
            )

        return True, spliced_source, None

    @classmethod
    def splice_multiple(
        cls,
        original_source: str,
        replacements: List[Tuple[SurgicalTarget, str]]
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Applies multiple surgical replacements to a source file safely.
        Sorts targets in reverse line order so line index changes in later
        substitutions do not invalidate start/end lines of earlier targets.
        """
        if not replacements:
            return True, original_source, None

        # Sort targets descending by start_line to preserve earlier line indices
        sorted_reps = sorted(replacements, key=lambda r: r[0].start_line, reverse=True)
        current_code = original_source
        for target, rep_code in sorted_reps:
            fresh_target, locate_error = ASTNodeLocator.locate_target_ex(
                current_code, target.qualified_name
            )
            if fresh_target is None:
                return False, original_source, (
                    locate_error
                    or f"Target '{target.qualified_name}' could not be re-located for multi-splice."
                )
            ok, current_code, err = cls.splice(current_code, fresh_target, rep_code)
            if not ok:
                return False, original_source, err

        return True, current_code, None


class TextDiffPatcher:
    """
    Baseline implementation: Simulates or applies text-based Search/Replace diffs
    (Aider style) to illustrate whitespace/matching fragility.
    """
    @staticmethod
    def apply_search_replace(
        original_source: str,
        search_block: str,
        replace_block: str
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Attempts standard string replace. Captures real-world failure modes
        (indentation mismatches, duplicate matches, trailing space slips).
        """
        if search_block not in original_source:
            # Check if whitespace mismatch is the culprit
            normalized_orig = "\n".join(line.strip() for line in original_source.splitlines())
            normalized_search = "\n".join(line.strip() for line in search_block.splitlines())
            if normalized_search in normalized_orig:
                return False, original_source, "DiffFailure: Indentation/Whitespace drift prevented exact string match."
            return False, original_source, "DiffFailure: SEARCH block not found in source file."

        # Count occurrences
        count = original_source.count(search_block)
        if count > 1:
            return False, original_source, f"DiffFailure: Ambiguous search block matches {count} different locations."

        patched = original_source.replace(search_block, replace_block, 1)

        # Validate syntax of patched output
        try:
            ast.parse(patched)
            return True, patched, None
        except SyntaxError as e:
            return False, original_source, f"IndentationError/SyntaxError in patched code: {e.msg}"
