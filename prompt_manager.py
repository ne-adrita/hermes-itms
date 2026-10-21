import functools
from typing import Dict, Any, Optional
import tiktoken
from skeletonizer import skeletonize_code

# 1. Standard Static System Instructions (73 Tokens - Optimized for Local Engines with 16/64 Block Caching)
GLOBAL_STATIC_PREFIX = """You are an automated, high-precision code synthesis engine.
Rules:
1. Output valid, syntactically correct Python code strictly within markdown blocks.
2. Comply with standard library typing conventions (PEP 484).
3. Handle boundary conditions, off-by-one errors, and type safety deterministically.
4. Implement the requested function signature without renaming arguments.
"""

# 2. Expanded Production Prefix (1,024+ Tokens - Standardized for Cloud Providers Requiring >=1024 Tokens for KV Cache Activation)
EXPANDED_CACHED_PREFIX_1024 = """You are an automated, high-precision code synthesis engine deployed in a production-grade software engineering pipeline.
Your task is to synthesize deterministic, mathematically sound, and syntactically flawless Python implementations adhering strictly to the specifications, interface contracts, and runtime failure diagnostics provided below.

### Section 1: Syntactic Strictness and Output Formatting Rules
1. Output Validity & Markdown Isolation:
   - Output valid, executable Python 3.12+ source code strictly encapsulated within standard markdown code fences.
   - Do not emit extraneous conversational commentary, preamble, introductory pleasantries, explanations, or sign-offs.
   - Ensure the final synthesized code can be parsed directly by Python's native ast.parse without syntax, indentation, or tokenization errors.
2. Self-Contained Scope:
   - Implement the complete requested function or method body so it can be integrated directly into the target class or module.
   - Import necessary standard-library modules explicitly within the code block if not already present in the interface context.
   - Do not use non-standard third-party libraries unless explicitly specified in the interface contract.
   - Do not alter global scope or declare unneeded global variables.

### Section 2: Interface Contracts, Type Safety, and PEP Standards
1. PEP 484, PEP 526, and PEP 585 Compliance:
   - Adhere strictly to modern Python type hint conventions using standard collection generics (e.g., list[str], dict[str, Any], tuple[int, ...], set[bytes]).
   - Preserve exact method signatures, argument order, default parameters, and declared return types without modification.
   - Never alter, rename, or reorder parameters of the requested target function or method signature.
   - If auxiliary helper methods or private state variables are required, prefix them with standard internal conventions (e.g., _helper_func).
2. Structural Integrity:
   - Retain exact class names, inheritance hierarchies, and instance attributes exposed in the Interface Context.
   - Do not introduce hidden side effects, global state mutations, or undocumented monkey-patching.
   - Maintain full compatibility with dataclasses, attrs, and standard OOP paradigms when manipulating existing domain objects.

### Section 3: Algorithmic Correctness, Determinism, and Boundary Guards
1. Deterministic Failure Prevention:
   - Guard against common edge cases: empty collections, off-by-one index loops, NoneType dereferencing, division by zero, floating-point rounding drift, and missing dictionary keys.
   - When traversing nested data structures, use safe access patterns (.get(), collections.defaultdict, or explicit guards).
   - Ensure proper validation of user inputs, numerical ranges, string encodings, and temporal formats (ISO 8601 timestamps).
2. Time and Space Complexity:
   - Select algorithms with optimal asymptotic complexity. Avoid nested quadratic loops or redundant deepcopies over large datasets.
   - Ensure iterative operations terminate deterministically within standard sandboxed runtime execution boundaries.
   - Prefer generators and memory-efficient iterators when processing streams of records or transactional arrays.

### Section 4: Exception Handling, Diagnostics, and Runtime Resilience
1. Exception Hierarchy:
   - Raise explicit, meaningful standard exceptions (e.g., ValueError, TypeError, KeyError) when invalid inputs or invariants are violated.
   - Avoid catching generic Exception unless re-raising or wrapping in domain-specific errors.
   - Ensure file descriptors, database connections, and network sockets are managed via context managers (with statements).
2. Test-Driven Verification & Diagnostic Invariants:
   - The runtime verification signal specifies the exact assertion or exception that caused previous execution attempts to fail.
   - Analyze the diagnostic assertion failure deterministically. Ensure your synthesized code passes the failed assertion while preserving all existing behavioral invariants.
   - Do not attempt to patch or mimic invalid syntactic patterns; synthesize the logic cleanly from the provided specification.

### Section 5: Testing & Concurrency Protocols
1. Test-Suite Harmonization:
   - All synthesized methods must be testable via standard unittest or pytest frameworks without mocking internal private logic unless required.
   - Do not emit mock runner invocations or test assertions inside the generated code block unless explicitly instructed.
   - Retain complete side-effect isolation so tests can be run concurrently in sandboxed worker threads.
2. Thread Safety and Concurrency:
   - When dealing with shared mutable collections or multi-threaded environments, ensure critical sections are protected by reentrant locks (threading.RLock).
   - For asynchronous coroutines, never call blocking synchronous I/O operations directly inside async def bodies.
   - Always honor timeout signals and task cancellation tokens cleanly.

### Section 6: Standard Coding Conventions and Invariants
- Use 4 spaces per indentation level strictly adhering to PEP 8 style standards.
- Write clean, maintainable, readable code focusing strictly on fulfilling the task specification deterministically.
- Handle state transitions atomically without leaving partially modified invalid structures on error.
- Verify mathematical operations for overflow, underflow, and NaN conditions.
- Ensure all imported identifiers and return structures strictly conform to Python 3.12 syntax specifications.
"""


@functools.lru_cache(maxsize=8)
def _get_cached_encoding(model_encoding: str) -> tiktoken.Encoding:
    """Cached tokenizer encoding loader to avoid ~5ms per-call overhead (500x speedup)."""
    return tiktoken.get_encoding(model_encoding)


def get_token_count(text: str, model_encoding: str = "cl100k_base") -> int:
    """Returns the token count of a given string using a cached tiktoken encoder."""
    if not text:
        return 0
    enc = _get_cached_encoding(model_encoding)
    return len(enc.encode(text))


def sanitize_runtime_traceback(raw_traceback: str) -> Dict[str, str]:
    """
    Extracts the minimal, high-signal diagnostic from noisy multi-line Python tracebacks.
    Strips internal virtualenv paths, OS frames, and local system noise to minimize input tokens.
    """
    if not raw_traceback or not raw_traceback.strip():
        return {
            "error_type": "ExecutionError",
            "error_message": "Unspecified runtime failure",
            "failing_assertion": "Unknown assertion",
            "clean_trace": "- Failure Mode: ExecutionError\n- Diagnostic: Unspecified runtime failure\n- Failed Assertion: Unknown assertion"
        }

    lines = [ln.strip() for ln in raw_traceback.strip().splitlines() if ln.strip()]
    
    error_type = "RuntimeError"
    error_msg = lines[-1]
    
    # Check for standard Error: Message on the last line
    last_line = lines[-1]
    if ":" in last_line:
        parts = last_line.split(":", 1)
        error_type = parts[0].strip()
        error_msg = parts[1].strip()
    elif "AssertionError" in last_line:
        error_type = "AssertionError"
        error_msg = last_line
    elif last_line.endswith("Error") or last_line.endswith("Exception") or last_line in ("StopIteration", "GeneratorExit", "KeyboardInterrupt"):
        error_type = last_line
        error_msg = ""

    # Locate the failing assertion or code line from bottom up (ignoring Python 3.11+ caret lines)
    failing_assertion = ""
    for i in range(len(lines) - 2, -1, -1):
        line = lines[i].strip()
        # Skip caret/underline diagnostic lines in Python 3.11+ (e.g. ^^^^^^^^^^)
        if all(c in "^~-" for c in line):
            continue
        if line.startswith("assert ") or "assert" in line:
            failing_assertion = line
            break
        if not line.startswith("File ") and not line.startswith("Traceback"):
            failing_assertion = line
            break

    if not failing_assertion:
        failing_assertion = error_msg

    clean_trace = f"- Failure Mode: {error_type}\n- Diagnostic: {error_msg}\n- Failed Assertion: {failing_assertion}"
    
    return {
        "error_type": error_type,
        "error_message": error_msg,
        "failing_assertion": failing_assertion,
        "clean_trace": clean_trace
    }


def build_itms_clean_escalation_prompt(
    task_description: str,
    raw_context_code: str,
    target_function_name: str,
    failing_test_case: str,
    error_type: str,
    error_message: str,
    strip_docstrings: bool = True,
    prune_unreachable: bool = False,
    use_expanded_prefix: bool = False
) -> Dict[str, Any]:
    """
    Constructs the Input Token Minimization System (ITMS) prompt:
    1. Static prefix is placed at token offset 0 (enabling KV cache reuse).
    2. Background code is skeletonized via AST pruning (and optional reachability pruning).
    3. The broken code draft is completely DISCARDED to prevent cognitive anchoring.
    4. Only the isolated runtime failure trace is injected.
    """
    skeleton_context = skeletonize_code(
        raw_context_code,
        target_function_name,
        strip_docstrings=strip_docstrings,
        prune_unreachable=prune_unreachable,
        task_description=task_description
    )

    prefix_to_use = EXPANDED_CACHED_PREFIX_1024 if use_expanded_prefix else GLOBAL_STATIC_PREFIX
    
    dynamic_payload = f"""### Interface Context
{skeleton_context}

### Task Specification
{task_description}

### Runtime Verification Signal
The initial run failed sandboxed execution.
- Failure Mode: {error_type}
- Diagnostic: {error_message}
- Failed Assertion: {failing_test_case}

Instruction: Implement the target function from scratch to satisfy all constraints and resolve this failure."""

    full_prompt = f"{prefix_to_use}\n\n{dynamic_payload}"

    raw_lines = len(raw_context_code.strip().splitlines()) if raw_context_code.strip() else 0
    skel_lines = len(skeleton_context.strip().splitlines()) if skeleton_context.strip() else 0
    lines_pruned = max(0, raw_lines - skel_lines)
    
    trace_diagnostic = f"- Failure Mode: {error_type}\n- Diagnostic: {error_message}\n- Failed Assertion: {failing_test_case}"
    trace_line_count = len(trace_diagnostic.strip().splitlines())
    
    static_tokens = get_token_count(prefix_to_use)
    dynamic_tokens = get_token_count(dynamic_payload)
    total_tokens = get_token_count(full_prompt)

    return {
        "full_prompt": full_prompt,
        "static_prefix": prefix_to_use,
        "dynamic_payload": dynamic_payload,
        "static_token_count": static_tokens,
        "dynamic_token_count": dynamic_tokens,
        "total_tokens": total_tokens,
        # Empirical AST metadata
        "lines_raw": raw_lines,
        "lines_skeleton": skel_lines,
        "lines_pruned": lines_pruned,
        "trace_line_count": trace_line_count,
        "prefix_mode": "Provider-Padded (1024+ Tokens)" if use_expanded_prefix else "Standard (Local Block)"
    }

# Backward-compatible alias
build_tdsc_clean_escalation_prompt = build_itms_clean_escalation_prompt


def build_baseline_contaminated_prompt(
    task_description: str,
    raw_context_code: str,
    broken_code_draft: str,
    failing_test_case: str,
    full_traceback: str
) -> Dict[str, Any]:
    """
    Constructs the conventional baseline prompt:
    1. Dynamic, uncached system prompt.
    2. Full, unpruned background code.
    3. The full broken code draft is injected into the context.
    4. Prompt commands the model to review and debug the draft.
    """
    dynamic_system_prompt = "You are a helpful software assistant. Current task involves reviewing a failed implementation and fixing it."
    
    full_prompt = f"""{dynamic_system_prompt}

Context:
{raw_context_code}

Problem Statement:
{task_description}

An initial attempt was generated but failed execution:
```python
{broken_code_draft}
```

The error traceback during execution was:
{full_traceback}

Failed Test Case:
{failing_test_case}

Please review the draft above, explain where the logic went wrong, and provide a corrected version of the code."""

    total_tokens = get_token_count(full_prompt)
    draft_lines = len(broken_code_draft.strip().splitlines()) if broken_code_draft.strip() else 0

    return {
        "full_prompt": full_prompt,
        "total_tokens": total_tokens,
        "draft_line_count": draft_lines
    }
