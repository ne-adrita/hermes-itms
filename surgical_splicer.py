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


class ASTNodeLocator:
    """
    Scans a Python source file to identify the exact line boundaries
    and indentation level of any function, method, or class.
    Supports multi-level qualified names (e.g. 'Outer.Inner.method')
    as well as standalone function and class lookups.
    """
    @staticmethod
    def locate_target(source_code: str, target_name: str) -> Optional[SurgicalTarget]:
        """
        Locates a function or class by name or qualified name (e.g. 'AccountLedger.reconcile_balance',
        'Outer.Inner.method', 'standalone_func', or 'AccountLedger').
        Returns line boundaries and leading indentation.
        """
        if not source_code or not target_name:
            return None

        try:
            tree = ast.parse(source_code)
        except Exception:
            return None

        lines = source_code.splitlines(keepends=True)
        num_lines = len(lines)
        target_clean = target_name.strip()
        parts = target_clean.split(".")

        matched_node = None
        matched_class_name = None

        def search_scope(nodes: List[ast.stmt], target_parts: List[str], current_parent: Optional[str] = None):
            nonlocal matched_node, matched_class_name
            if not target_parts or matched_node is not None:
                return

            current_target = target_parts[0]
            remaining_parts = target_parts[1:]

            for node in nodes:
                if isinstance(node, ast.ClassDef):
                    if node.name == current_target:
                        if not remaining_parts:
                            matched_node = node
                            matched_class_name = current_parent
                            return
                        else:
                            class_qual = f"{current_parent}.{node.name}" if current_parent else node.name
                            search_scope(node.body, remaining_parts, class_qual)
                            if matched_node is not None:
                                return
                    elif len(target_parts) == 1:
                        # Unqualified search: inspect inside this class
                        search_scope(node.body, target_parts, node.name)
                        if matched_node is not None:
                            return

                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name == current_target and not remaining_parts:
                        matched_node = node
                        matched_class_name = current_parent
                        return
                    elif len(target_parts) == 1:
                        search_scope(node.body, target_parts, current_parent)
                        if matched_node is not None:
                            return

        search_scope(tree.body, parts)

        if not matched_node:
            return None

        # Determine line boundaries (ast lineno is 1-indexed)
        # Include decorators if present
        start_line = matched_node.lineno
        if hasattr(matched_node, "decorator_list") and matched_node.decorator_list:
            first_dec = matched_node.decorator_list[0]
            start_line = min(start_line, first_dec.lineno)

        end_line = getattr(matched_node, "end_lineno", None)
        if end_line is None:
            end_line = num_lines

        # Extract leading indentation of the definition line
        def_line_text = lines[matched_node.lineno - 1] if 1 <= matched_node.lineno <= num_lines else ""
        indentation = def_line_text[: len(def_line_text) - len(def_line_text.lstrip())]

        # Extract original text slice
        slice_lines = lines[start_line - 1 : end_line]
        original_text = "".join(slice_lines)

        body_start = matched_node.body[0].lineno if getattr(matched_node, "body", None) else start_line

        qual_name = f"{matched_class_name}.{matched_node.name}" if matched_class_name else matched_node.name

        return SurgicalTarget(
            name=matched_node.name,
            qualified_name=qual_name,
            node_type=type(matched_node).__name__,
            start_line=start_line,
            end_line=end_line,
            body_start_line=body_start,
            base_indentation=indentation,
            original_text=original_text,
            parent_class=matched_class_name
        )


class ASTSurgicalSplicer:
    """
    Surgically splices candidate replacement code into an existing source file
    using exact line boundaries while strictly preserving comments, formatting,
    and indentation.

    KNOWN LIMITATION (do not fix before the baseline benchmark measurement):
    A body-only replacement (no ``def``/``class`` header) that itself declares
    a nested function is mis-indented. The nested ``def`` is hoisted to module
    level and the original method body is overwritten, so the spliced file is
    syntactically valid but semantically wrong, and ``splice()`` still reports
    success. Reproducer:

        source    = "class Outer:\\n    class Inner:\\n        def method(self, x):\\n"
                    "            def helper(v):\\n                return v * 2\\n"
                    "            return helper(x) + 1\\n"
        target    = "Outer.Inner.method"
        replacement = "    def helper(v):\\n        return v * 2\\n    return helper(x) + 5\\n"

    Full-node replacements (with a ``def`` header) are unaffected. This
    limitation is documented rather than fixed so that the benchmark baseline
    is measured against the current, unchanged implementation.
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

    @classmethod
    def splice(
        cls,
        original_source: str,
        target: SurgicalTarget,
        replacement_code: str
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Slices the target lines from original_source and splices replacement_code.
        Intelligently detects whether replacement is a full node definition, a class wrapper,
        or a body-only block. Re-indents replacement code to match target's base indentation level.
        Returns (success: bool, spliced_code: str, error_message: Optional[str]).
        """
        clean_code = cls.clean_replacement_code(replacement_code)
        if not clean_code:
            return False, original_source, "Replacement code is empty."

        # If target is a class method, separate any top-level helper functions/classes
        # so they are placed at module level rather than indented inside the class.
        is_function_target = target.node_type in ("FunctionDef", "AsyncFunctionDef")
        module_helpers = []
        if is_function_target and target.parent_class is not None:
            try:
                cand_tree = ast.parse(clean_code)
                cand_lines = clean_code.splitlines(keepends=True)
                method_parts = []
                for node in cand_tree.body:
                    if isinstance(node, ast.ClassDef):
                        # Extract all methods from class wrapper
                        for item in node.body:
                            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                f_start = item.lineno - 1
                                if hasattr(item, "decorator_list") and item.decorator_list:
                                    f_start = min(f_start, item.decorator_list[0].lineno - 1)
                                f_end = getattr(item, "end_lineno", len(cand_lines))
                                method_parts.append(textwrap.dedent("".join(cand_lines[f_start:f_end])).strip())
                    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        f_start = node.lineno - 1
                        if hasattr(node, "decorator_list") and node.decorator_list:
                            f_start = min(f_start, node.decorator_list[0].lineno - 1)
                        f_end = getattr(node, "end_lineno", len(cand_lines))
                        chunk = textwrap.dedent("".join(cand_lines[f_start:f_end])).strip()
                        if node.name == target.name:
                            method_parts.append(chunk)
                        else:
                            # Standalone helper function -> module level
                            module_helpers.append(chunk)
                    else:
                        f_start = node.lineno - 1
                        f_end = getattr(node, "end_lineno", len(cand_lines))
                        module_helpers.append(textwrap.dedent("".join(cand_lines[f_start:f_end])).strip())

                if method_parts:
                    clean_code = "\n\n".join(method_parts)
            except Exception:
                pass
        elif is_function_target:
            # Standalone function target: unwrap if wrapped in a class
            try:
                cand_tree = ast.parse(clean_code)
                for node in cand_tree.body:
                    if isinstance(node, ast.ClassDef):
                        cand_lines = clean_code.splitlines(keepends=True)
                        extracted_methods = []
                        for item in node.body:
                            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                f_start = item.lineno - 1
                                if hasattr(item, "decorator_list") and item.decorator_list:
                                    f_start = min(f_start, item.decorator_list[0].lineno - 1)
                                f_end = getattr(item, "end_lineno", len(cand_lines))
                                extracted_methods.append(textwrap.dedent("".join(cand_lines[f_start:f_end])).strip())
                        if extracted_methods:
                            clean_code = "\n\n".join(extracted_methods)
                            break
            except Exception:
                pass

        # Detect if candidate is body-only (e.g. lacks 'def ' or 'class ' or '@')
        first_clean_line = clean_code.splitlines()[0].strip() if clean_code.splitlines() else ""
        has_header = first_clean_line.startswith(("def ", "async def ", "class ", "@"))
        
        is_body_only = is_function_target and not has_header

        rep_lines = clean_code.splitlines()
        indented_lines = []
        target_indent = (target.base_indentation + "    ") if is_body_only else target.base_indentation

        for line in rep_lines:
            if line.strip():
                indented_lines.append(f"{target_indent}{line}")
            else:
                indented_lines.append("")

        new_block = "\n".join(indented_lines) + "\n"

        orig_lines = original_source.splitlines(keepends=True)
        
        # Line boundaries: if body-only, slice from body_start_line; otherwise from start_line
        effective_start = target.body_start_line if is_body_only else target.start_line
        start_idx = max(0, effective_start - 1)
        end_idx = min(len(orig_lines), target.end_line)

        prefix_lines = orig_lines[:start_idx]
        suffix_lines = orig_lines[end_idx:]

        # Reconstruct composite file with module helpers prepended
        helper_header = ("\n\n".join(module_helpers) + "\n\n") if module_helpers else ""
        spliced_source = helper_header + "".join(prefix_lines) + new_block + "".join(suffix_lines)

        # Pre-validate AST syntax on the resulting spliced source
        try:
            ast.parse(spliced_source)
            return True, spliced_source, None
        except SyntaxError as e:
            return False, original_source, f"SyntaxError in spliced output at line {e.lineno}: {e.msg}"
        except Exception as e:
            return False, original_source, f"ParseError in spliced output: {str(e)}"

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
            fresh_target = ASTNodeLocator.locate_target(current_code, target.qualified_name)
            target_to_use = fresh_target or target
            ok, current_code, err = cls.splice(current_code, target_to_use, rep_code)
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
