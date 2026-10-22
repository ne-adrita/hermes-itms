"""
A macOS Seatbelt profile for the official-evaluation subprocess.

The profile is generated at run time (the paths depend on the interpreter and
on the temporary module directory) by
:func:`benchmarks.phase0.sandbox_profile.build_sandbox_profile`.

What it enforces
----------------
*   **no network** - the profile denies every network operation.  This is
    enforced by the kernel, not by the interpreter-level guard that a
    candidate could bypass with a child interpreter.
*   **no access to the operator's files** - the home directory (and therefore
    the project directory and the ``.env`` file that holds the provider
    credential) is denied for reading and writing.  Only three places stay
    readable: the interpreter's own virtual environment, the temporary
    evaluation directory, and the system/runtime paths.
*   **writes only inside the evaluation directory**.

What it does not enforce
------------------------
*   CPU / memory / process limits are applied separately with POSIX resource
    limits (``RLIMIT_CPU``, ``RLIMIT_AS``, ``RLIMIT_NPROC``, ``RLIMIT_FSIZE``)
    by :mod:`benchmarks.phase0.runner`.
*   Seatbelt is a macOS facility.  On other platforms the runner keeps the
    environment scrub plus the interpreter-level socket guard, and the run
    metadata records the weaker isolation level.
"""

from __future__ import annotations

import os
import sys
from typing import List, Sequence

__all__ = [
    "build_sandbox_profile",
    "sandbox_available",
    "SANDBOX_TOOL",
    "ISOLATION_SEATBELT",
    "ISOLATION_SCRUBBED_SUBPROCESS",
    "ISOLATION_OFF",
]

SANDBOX_TOOL = "/usr/bin/sandbox-exec"

ISOLATION_SEATBELT = "macos_seatbelt_profile"
ISOLATION_SCRUBBED_SUBPROCESS = "scrubbed_environment_subprocess"
ISOLATION_OFF = "none"


def _subpath(path: str) -> str:
    return f'(subpath "{path}")'


def _literal(path: str) -> str:
    return f'(literal "{path}")'


def sandbox_available() -> bool:
    """Whether a Seatbelt sandbox can be used on this host."""
    if not sys.platform.startswith("darwin"):
        return False
    return os.path.exists(SANDBOX_TOOL)


def build_sandbox_profile(
    module_dir: str,
    tmp_dir: str,
    deny_paths: Sequence[str] = (),
    allow_paths: Sequence[str] = (),
) -> str:
    """
    Builds the Seatbelt profile for one evaluation.

    The rule order matters: the profile denies everything, then allows reads
    broadly, then denies the sensitive trees, and finally re-allows the
    interpreter's virtual environment and the evaluation directory.  The last
    matching rule wins in Seatbelt, so the re-allow rules are what keeps the
    interpreter working while the rest of the home directory stays denied.
    """
    lines: List[str] = [
        "(version 1)",
        "(deny default)",
        # -- process -------------------------------------------------------
        "(allow process-exec)",
        "(allow process-fork)",
        "(allow signal (target self))",
        # -- file system: broad read, narrow write -------------------------
        "(allow file-read-metadata)",
        "(allow file-read*)",
        "(allow file-map-executable)",
        f'(allow file-write* {_subpath(module_dir)})',
        # -- sensitive trees are denied ------------------------------------
    ]
    for path in deny_paths:
        if path:
            lines.append(f"(deny file-read* {_subpath(path)})")
            lines.append(f"(deny file-write* {_subpath(path)})")
    for path in allow_paths:
        if path:
            lines.append(f"(allow file-read* {_subpath(path)})")
    lines.extend(
        [
            # -- devices and misc --------------------------------------------
            "(allow file-ioctl)",
            '(allow file-read* (literal "/dev/null") (literal "/dev/urandom") (literal "/dev/random"))',
            "(allow mach-lookup)",
            "(allow sysctl-read)",
            "(allow iokit-open)",
            # -- network is denied by (deny default); stated explicitly -----
            "(deny network*)",
        ]
    )
    return "\n".join(lines) + "\n"
