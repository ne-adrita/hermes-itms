import sys
import os
import ast
import re
import time
import tempfile
import textwrap
import subprocess
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any
from prompt_manager import sanitize_runtime_traceback

# ---------------------------------------------------------------------------
# ISOLATION LEVEL - read this before claiming the harness is a "sandbox".
#
# The execution gate runs candidate code in a *separate OS process* with a
# scrubbed environment and a wall-clock timeout.  That is a process boundary,
# not an OS-level sandbox:
#
#   * there is NO Docker / container / namespace isolation,
#   * the child process can read and write the real file system,
#   * network access is not blocked by the harness (a child interpreter can
#     open a socket, so "socket blocked" only holds for code that does not
#     deliberately re-enable it),
#   * CPU / memory limits are not enforced beyond the timeout.
#
# The environment scrubbing (API keys, tokens, credentials) and the timeout
# ARE enforced.  Anything stronger is a documented limitation, not a feature.
# ---------------------------------------------------------------------------
ISOLATION_LEVEL = "process_boundary_only"
ISOLATION_ENFORCED = (
    "separate OS process",
    "scrubbed environment (credentials/keys removed)",
    "wall-clock timeout with process termination",
    "AST syntax pre-validation before execution",
)
ISOLATION_NOT_ENFORCED = (
    "no Docker/container/namespace isolation",
    "no file-system confinement (child can read/write the real file system)",
    "no network isolation (not blocked by the harness)",
    "no CPU or memory limits",
)


def describe_isolation() -> Dict[str, Any]:
    """Returns an honest description of what the execution gate does and does not enforce."""
    return {
        "isolation_level": ISOLATION_LEVEL,
        "enforced": list(ISOLATION_ENFORCED),
        "not_enforced": list(ISOLATION_NOT_ENFORCED),
    }

@dataclass
class ExecutionResult:
    """Structured, deterministic result of a sandboxed execution test."""
    success: bool
    exit_code: int
    error_type: Optional[str]
    error_message: Optional[str]
    failing_assertion: Optional[str]
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool = False
    syntax_valid: bool = True

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_clean_diagnostic_signal(self) -> str:
        """Returns the isolated 3-line diagnostic string for ITMS clean-trace escalation."""
        if self.success:
            return "Execution Passed Deterministically"
        err_type = self.error_type or "ExecutionError"
        err_msg = self.error_message or "Execution failed without diagnostic message"
        assertion = self.failing_assertion or "Assertion unavailable"
        return f"- Failure Mode: {err_type}\n- Diagnostic: {err_msg}\n- Failed Assertion: {assertion}"


class ExecutionHarness:
    """
    Deterministic Execution Gate.
    Executes candidate code in an isolated subprocess boundary at zero API cost.
    Guards against syntax errors, infinite loops, and unhandled exceptions.

    Isolation level: ``process_boundary_only`` (see `ISOLATION_NOT_ENFORCED`).
    This is *not* a Docker/container sandbox: the child process keeps the
    machine's file-system and network permissions.
    """
    def __init__(self, default_timeout: float = 3.0):
        self.default_timeout = default_timeout

    @staticmethod
    def get_sanitized_sandbox_env() -> Dict[str, str]:
        """
        Constructs a minimal, secure environment dictionary for subprocess execution.
        Explicitly scrubs all API keys, authorization tokens, cloud credentials,
        and secret variables to prevent credential exfiltration by untrusted code.
        """
        safe_keys = {
            "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC",
            "PATHEXT", "PYTHONPATH", "PYTHONHOME", "LANG", "LC_ALL",
            "PROGRAMDATA", "SYSTEMDRIVE", "USERPROFILE"
        }
        sanitized = {k: v for k, v in os.environ.items() if k.upper() in safe_keys}
        blocked_substrings = ("KEY", "SECRET", "TOKEN", "AUTH", "PASSWD", "PASSWORD", "CREDENTIAL")
        filtered = {
            k: v for k, v in sanitized.items()
            if not any(sub in k.upper() for sub in blocked_substrings)
        }
        filtered["PYTHONDONTWRITEBYTECODE"] = "1"
        filtered["PYTHONUNBUFFERED"] = "1"
        return filtered

    def validate_syntax(self, code: str) -> tuple[bool, Optional[str]]:
        """Static pre-check using Python's native AST parser."""
        try:
            ast.parse(code)
            return True, None
        except SyntaxError as e:
            return False, f"SyntaxError at line {e.lineno}: {e.msg}"
        except Exception as e:
            return False, f"ParseError: {str(e)}"

    def execute_test(
        self,
        candidate_code: str,
        context_code: str = "",
        test_suite_code: str = "",
        target_function_name: Optional[str] = None,
        timeout: Optional[float] = None
    ) -> ExecutionResult:
        """
        Executes candidate code combined with context and test assertions in an isolated subprocess.
        Automatically resolves class method binding and dedents LLM-indented blocks.
        """
        timeout_limit = timeout if timeout is not None else self.default_timeout
        t0 = time.perf_counter()

        # Extract code from markdown blocks and normalize indentation
        clean_candidate = self._extract_code_blocks(candidate_code)
        clean_candidate = textwrap.dedent(clean_candidate)

        # Detect if target function belongs to a class in context_code
        binding_code = ""
        resolved_target = (target_function_name or "").split(".")[-1].strip()
        if context_code.strip() and resolved_target:
            try:
                tree = ast.parse(context_code)
                for node in ast.walk(tree):
                    if isinstance(node, ast.ClassDef):
                        is_member = any(isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == resolved_target for item in node.body)
                        is_named = bool(target_function_name and target_function_name.startswith(f"{node.name}."))
                        if is_member or is_named:
                            binding_code = f"\ntry:\n    {node.name}.{resolved_target} = {resolved_target}\nexcept (NameError, AttributeError):\n    pass\n"
                            break
            except Exception:
                pass

        # Standard library convenience imports to prevent spurious NameErrors on benchmarks
        convenience_imports = (
            "import sys, os, math, json, re, time, collections, itertools, heapq, hashlib, functools, copy, string, datetime, decimal\n"
            "from typing import List, Dict, Tuple, Set, Optional, Any, Union, Callable, Iterable, Sequence, Mapping, Iterator\n"
            "from collections import defaultdict, deque, Counter, OrderedDict, namedtuple\n"
            "from functools import reduce, lru_cache, partial\n"
            "from datetime import datetime, date, timedelta\n"
            "from decimal import Decimal\n"
        )

        # Check if candidate code can be surgically spliced into context_code
        candidate_spliced = False
        spliced_module = context_code
        if context_code.strip() and resolved_target:
            try:
                from surgical_splicer import ASTNodeLocator, ASTSurgicalSplicer
                target_node = ASTNodeLocator.locate_target(context_code, target_function_name or resolved_target)
                if target_node:
                    ok, spliced_res, _ = ASTSurgicalSplicer.splice(context_code, target_node, clean_candidate)
                    if ok:
                        spliced_module = spliced_res
                        candidate_spliced = True
            except Exception:
                pass

        if candidate_spliced:
            full_script = f"{convenience_imports}\n{spliced_module}\n\n{test_suite_code}"
        else:
            # Assemble composite script: Context + Candidate Code + Method Binding + Test Suite
            script_parts = [convenience_imports]
            if context_code.strip():
                script_parts.append(context_code.strip())
            if clean_candidate.strip():
                script_parts.append(clean_candidate.strip())
            if binding_code.strip():
                script_parts.append(binding_code.strip())
            if test_suite_code.strip():
                script_parts.append(test_suite_code.strip())

            full_script = "\n\n".join(script_parts)

        # 1. AST Syntax Pre-validation
        is_valid_syntax, syntax_err = self.validate_syntax(full_script)
        if not is_valid_syntax:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return ExecutionResult(
                success=False,
                exit_code=1,
                error_type="SyntaxError",
                error_message=syntax_err or "SyntaxError: invalid syntax",
                failing_assertion=syntax_err or "SyntaxError",
                stdout="",
                stderr=syntax_err or "",
                duration_ms=elapsed_ms,
                timed_out=False,
                syntax_valid=False
            )

        # 2. Isolated Subprocess Execution
        temp_file_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".py",
                delete=False,
                encoding="utf-8"
            ) as tmp:
                tmp.write(full_script)
                temp_file_path = tmp.name

            proc = subprocess.run(
                [sys.executable, temp_file_path],
                capture_output=True,
                text=True,
                timeout=timeout_limit,
                env=self.get_sanitized_sandbox_env()
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            if proc.returncode == 0:
                return ExecutionResult(
                    success=True,
                    exit_code=0,
                    error_type=None,
                    error_message=None,
                    failing_assertion=None,
                    stdout=proc.stdout,
                    stderr=proc.stderr,
                    duration_ms=elapsed_ms,
                    timed_out=False,
                    syntax_valid=True
                )
            else:
                diag = sanitize_runtime_traceback(proc.stderr)
                return ExecutionResult(
                    success=False,
                    exit_code=proc.returncode,
                    error_type=diag["error_type"],
                    error_message=diag["error_message"],
                    failing_assertion=diag["failing_assertion"],
                    stdout=proc.stdout,
                    stderr=proc.stderr,
                    duration_ms=elapsed_ms,
                    timed_out=False,
                    syntax_valid=True
                )

        except subprocess.TimeoutExpired as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return ExecutionResult(
                success=False,
                exit_code=-1,
                error_type="TimeoutExpired",
                error_message=f"Execution exceeded timeout limit ({timeout_limit:.1f}s)",
                failing_assertion=f"TimeoutExpired: Execution time > {timeout_limit:.1f}s",
                stdout=e.stdout or "" if hasattr(e, "stdout") and e.stdout else "",
                stderr=e.stderr or "" if hasattr(e, "stderr") and e.stderr else "",
                duration_ms=elapsed_ms,
                timed_out=True,
                syntax_valid=True
            )
        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return ExecutionResult(
                success=False,
                exit_code=1,
                error_type=type(e).__name__,
                error_message=str(e),
                failing_assertion="Internal Execution Harness Exception",
                stdout="",
                stderr=str(e),
                duration_ms=elapsed_ms,
                timed_out=False,
                syntax_valid=True
            )
        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.unlink(temp_file_path)
                except OSError:
                    pass

    @staticmethod
    def _extract_code_blocks(text: str) -> str:
        """
        Strips markdown formatting, code fences, and conversational preamble.
        Extracts strictly the candidate Python source code.
        """
        if not text or not text.strip():
            return ""

        # 1. First priority: Extract content inside markdown code blocks
        pattern = r"```+[^\n]*\n(.*?)```+"
        matches = re.findall(pattern, text, flags=re.DOTALL)
        if matches:
            # Check if joining all blocks produces valid Python
            combined = "\n\n".join(m.strip() for m in matches if m.strip())
            try:
                ast.parse(combined)
                return textwrap.dedent(combined).strip()
            except Exception:
                # Fallback: pick best valid Python block
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
        lines = text.splitlines()
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
        return textwrap.dedent(text).strip()
