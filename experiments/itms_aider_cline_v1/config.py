"""
Experiment configuration for the ITMS vs Aider vs Cline comparison.

The configuration is a plain JSON document so that the exact settings of a
run can be committed, diffed and cited in the report.  It never contains a
credential: API keys are read from the environment / the project's ``.env``
at run time and are never written to disk.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "experiment_config.json"
)

REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)


@dataclass
class ExperimentConfig:
    schema_version: str = "1.0"
    config_version: str = "1.0.0"
    experiment_id: str = "itms_vs_aider_vs_cline_v1"
    description: str = ""

    # --- task set ---------------------------------------------------------
    task_manifest: str = "task_manifest.json"
    task_ids: List[str] = field(default_factory=list)

    # --- systems ----------------------------------------------------------
    systems: List[str] = field(default_factory=lambda: ["itms", "aider", "cline"])

    # --- repetitions ------------------------------------------------------
    repetitions: int = 5

    # --- model / provider -------------------------------------------------
    provider: str = "openrouter"
    model_id: str = ""
    temperature: float = 0.0
    max_output_tokens: int = 2048

    # --- timeouts ---------------------------------------------------------
    system_timeout_seconds: float = 300.0
    evaluation_timeout_seconds: float = 20.0
    request_timeout_seconds: float = 120.0

    # --- seeds ------------------------------------------------------------
    run_order_seed: int = 20260101
    bootstrap_seed: int = 20260101
    seed_policy: str = "per_system_support"
    seed_base: int = 770000

    # --- output -----------------------------------------------------------
    output_root: str = "experiments/itms_aider_cline_v1/runs"

    # --- extra ------------------------------------------------------------
    notes: List[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Optional[str] = None) -> "ExperimentConfig":
        path = path or DEFAULT_CONFIG_PATH
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        kwargs = {k: v for k, v in payload.items() if k in known}
        unknown = sorted(set(payload) - known)
        config = cls(**kwargs)
        config._unknown_keys = unknown  # type: ignore[attr-defined]
        return config

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "config_version": self.config_version,
            "experiment_id": self.experiment_id,
            "description": self.description,
            "task_manifest": self.task_manifest,
            "task_ids": self.task_ids,
            "systems": self.systems,
            "repetitions": self.repetitions,
            "provider": self.provider,
            "model_id": self.model_id,
            "temperature": self.temperature,
            "max_output_tokens": self.max_output_tokens,
            "system_timeout_seconds": self.system_timeout_seconds,
            "evaluation_timeout_seconds": self.evaluation_timeout_seconds,
            "request_timeout_seconds": self.request_timeout_seconds,
            "run_order_seed": self.run_order_seed,
            "bootstrap_seed": self.bootstrap_seed,
            "seed_policy": self.seed_policy,
            "seed_base": self.seed_base,
            "output_root": self.output_root,
            "notes": self.notes,
        }

    # -- derived -----------------------------------------------------------

    @property
    def planned_attempts(self) -> int:
        return len(self.task_ids) * len(self.systems) * self.repetitions

    def seed_for(self, task_id: str, system: str, repetition: int) -> Optional[int]:
        """The seed *requested* for one cell.

        A seed is only ever *applied* by a system that supports one; the
        runner records ``seed_supported``/``seed_applied`` separately so that
        "we asked for a seed" and "a seed was actually used" are never
        conflated.
        """
        if self.seed_policy == "none":
            return None
        digest = sum((i + 1) * ord(ch) for i, ch in enumerate(f"{task_id}|{system}"))
        return self.seed_base + digest % 100000 + repetition * 1000


def load_manifest(path: Optional[str] = None) -> Dict[str, Any]:
    path = path or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "task_manifest.json"
    )
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)
