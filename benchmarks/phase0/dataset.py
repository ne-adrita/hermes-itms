"""
Phase 0 dataset pinning helpers for HumanEval.

The benchmark tasks in Phase 0 are derived from the HumanEval dataset that
ships with the official ``openai/human-eval`` repository.  This module pins an
immutable revision of that repository and provides a verified fetch of the
dataset file, plus helpers for loading and slicing it.

Nothing here is executed during offline validation: the validator only ever
reads the vendored subset in ``benchmarks/data/source/``.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

__all__ = [
    "SOURCE_REPO_URL",
    "SOURCE_REVISION",
    "SOURCE_REPO_HEAD",
    "DATASET_PATH",
    "DATASET_GZ_SHA256",
    "DATASET_RAW_SHA256",
    "DATASET_PROBLEM_COUNT",
    "LICENSE_SPDX",
    "LICENSE_FILE_PATH",
    "LICENSE_FILE_SHA256",
    "ProvenanceError",
    "fetch_license",
    "fetch_pinned_dataset",
    "load_problems",
    "problem_to_dict",
]

#: Official upstream repository for the HumanEval dataset.
SOURCE_REPO_URL = "https://github.com/openai/human-eval"

#: Immutable revision that pins ``data/HumanEval.jsonl.gz``.
SOURCE_REVISION = "463c980b59e818ace59f6f9803cd92c749ceae61"

#: Repository HEAD observed while preparing Phase 0 (documentation only; the
#: dataset itself is pinned to :data:`SOURCE_REVISION`).
SOURCE_REPO_HEAD = "b9453e8f1865df0a0057a1383767caa400acfbcf"

DATASET_PATH = "data/HumanEval.jsonl.gz"

#: sha256 of ``data/HumanEval.jsonl.gz`` at :data:`SOURCE_REVISION`.
DATASET_GZ_SHA256 = "b796127e635a67f93fb35c04f4cb03cf06f38c8072ee7cee8833d7bee06979ef"

#: sha256 of the decompressed ``HumanEval.jsonl`` payload.
DATASET_RAW_SHA256 = "1d49078ba3e2b196b9344535bef34a43021f038fad9561d6ee7c53450609a6a2"

DATASET_PROBLEM_COUNT = 164

LICENSE_SPDX = "MIT"
LICENSE_FILE_PATH = "LICENSE"
LICENSE_FILE_SHA256 = "bcba3de214851cce46ed5af42d6698044616eeace887c3231bc7a20474ab639e"

RAW_URL_TEMPLATE = "https://raw.githubusercontent.com/openai/human-eval/{rev}/{path}"


class ProvenanceError(RuntimeError):
    """Raised when the pinned dataset cannot be fetched or verified."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def fetch_pinned_dataset(destination: str, timeout: float = 120.0) -> Dict[str, Any]:
    """Download and verify the pinned HumanEval dataset.

    The compressed file is verified against :data:`DATASET_GZ_SHA256` and the
    decompressed payload against :data:`DATASET_RAW_SHA256`.  A checksum
    mismatch raises :class:`ProvenanceError`; nothing is trusted silently.
    """
    url = RAW_URL_TEMPLATE.format(rev=SOURCE_REVISION, path=DATASET_PATH)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            compressed = response.read()
    except Exception as exc:  # pragma: no cover - network dependent
        raise ProvenanceError(f"could not fetch {url}: {exc}") from exc

    gz_digest = sha256_bytes(compressed)
    if gz_digest != DATASET_GZ_SHA256:
        raise ProvenanceError(
            f"checksum mismatch for {DATASET_PATH}@{SOURCE_REVISION}: "
            f"expected {DATASET_GZ_SHA256}, got {gz_digest}"
        )

    raw = gzip.decompress(compressed)
    raw_digest = sha256_bytes(raw)
    if raw_digest != DATASET_RAW_SHA256:
        raise ProvenanceError(
            f"checksum mismatch for decompressed payload: "
            f"expected {DATASET_RAW_SHA256}, got {raw_digest}"
        )

    os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)
    with open(destination, "wb") as fh:
        fh.write(compressed)

    return {
        "url": url,
        "destination": os.path.abspath(destination),
        "gz_sha256": gz_digest,
        "raw_sha256": raw_digest,
        "bytes": len(compressed),
    }


def fetch_license(destination: str, timeout: float = 60.0) -> Dict[str, Any]:
    """Download and verify the upstream MIT licence text.

    The licence file does not exist at the pinned dataset commit; it is taken
    from the repository HEAD observed while preparing Phase 0 and verified
    against :data:`LICENSE_FILE_SHA256`.  MIT requires that the licence text
    accompanies redistributed copies of the dataset, so it is vendored next to
    the dataset subset.
    """
    url = RAW_URL_TEMPLATE.format(rev=SOURCE_REPO_HEAD, path=LICENSE_FILE_PATH)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = response.read()
    except Exception as exc:  # pragma: no cover - network dependent
        raise ProvenanceError(f"could not fetch {url}: {exc}") from exc

    digest = sha256_bytes(payload)
    if digest != LICENSE_FILE_SHA256:
        raise ProvenanceError(
            f"checksum mismatch for {LICENSE_FILE_PATH}@{SOURCE_REPO_HEAD}: "
            f"expected {LICENSE_FILE_SHA256}, got {digest}"
        )

    os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)
    with open(destination, "wb") as fh:
        fh.write(payload)
    return {"url": url, "destination": os.path.abspath(destination), "sha256": digest}


def load_problems(dataset_path: str) -> List[Dict[str, Any]]:
    """Load the pinned dataset from a local ``HumanEval.jsonl.gz`` file."""
    with open(dataset_path, "rb") as fh:
        compressed = fh.read()
    raw = gzip.decompress(compressed).decode("utf-8")
    problems = [json.loads(line) for line in raw.splitlines() if line.strip()]
    return problems


def problem_to_dict(problem: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise a raw HumanEval record into the vendored subset format."""
    return {
        "task_id": problem["task_id"],
        "prompt": problem["prompt"],
        "canonical_solution": problem["canonical_solution"],
        "test": problem["test"],
        "entry_point": problem["entry_point"],
    }


def build_source_file(problem: Dict[str, Any]) -> str:
    """Compose the reference source file for a HumanEval problem.

    The HumanEval prompt contains the import header, the function signature and
    the docstring but no implementation; ``canonical_solution`` holds the
    reference body.  Concatenating the two yields a complete, importable
    module whose docstring is byte-for-byte the original problem prompt's
    docstring.
    """
    prompt = problem["prompt"].rstrip("\n")
    body = problem["canonical_solution"].strip("\n")
    return f"{prompt}\n{body}\n"
