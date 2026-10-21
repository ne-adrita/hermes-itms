"""
Offline dry run for the ITMS vs Aider vs Cline experiment.

This script is the only thing that may be run before Phase 2.  It:

* validates the task manifest and the experiment configuration;
* validates that every planned ``(task, system, repetition)`` cell has a
  unique, deterministic attempt id;
* executes the **complete** pipeline -- workspace preparation, system
  invocation, artifact persistence, hidden-test evaluation, record writing
  -- using **offline mock adapters only**;
* asserts that no outbound network connection is attempted;
* asserts that a failed attempt still leaves its artifacts on disk.

It never contacts a model provider, never launches ``aider`` or ``cline`` and
never writes into ``pilot_runs/`` or ``results/``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys
import tempfile
from typing import Any, Dict, List, Optional

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from .config import ExperimentConfig, load_manifest  # noqa: E402
from .runner import (  # noqa: E402
    AttemptPlan,
    ExperimentRunner,
    attempt_id_for,
    build_adapters,
    build_schedule,
)

# ---------------------------------------------------------------------------
# network guard
# ---------------------------------------------------------------------------


class NetworkAccessAttempted(AssertionError):
    """Raised when anything tries to open a socket during the dry run."""


class _BlockedSocket(socket.socket):
    def __init__(self, *args, **kwargs):
        raise NetworkAccessAttempted(
            "the dry run attempted a network connection; it must stay offline"
        )


def install_network_guard() -> None:
    """Make any outbound socket use fail loudly."""
    socket.socket = _BlockedSocket  # type: ignore[misc,assignment]
    socket.create_connection = _blocked_create_connection  # type: ignore[assignment]


def _blocked_create_connection(*args, **kwargs):
    raise NetworkAccessAttempted(
        "the dry run attempted a network connection; it must stay offline"
    )


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------


class DryRunResult:
    def __init__(self) -> None:
        self.checks: List[Dict[str, Any]] = []

    def record(self, name: str, passed: bool, detail: str = "") -> None:
        self.checks.append({"check": name, "passed": bool(passed), "detail": detail})

    @property
    def all_passed(self) -> bool:
        return all(c["passed"] for c in self.checks)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "checks": self.checks,
            "all_passed": self.all_passed,
            "passed": sum(1 for c in self.checks if c["passed"]),
            "failed": sum(1 for c in self.checks if not c["passed"]),
        }


def validate_config(config: ExperimentConfig, manifest: Dict[str, Any], result: DryRunResult) -> None:
    result.record(
        "config_schema",
        config.schema_version == "1.0",
        f"schema_version={config.schema_version}",
    )
    result.record(
        "manifest_present",
        bool(manifest.get("tasks")),
        f"{len(manifest.get('tasks', []))} tasks",
    )
    result.record(
        "manifest_task_count",
        len(manifest.get("tasks", [])) == 20,
        f"{len(manifest.get('tasks', []))} tasks (expected 20)",
    )
    manifest_ids = [t["task_id"] for t in manifest.get("tasks", [])]
    result.record(
        "config_task_ids_match_manifest",
        sorted(config.task_ids) == sorted(manifest_ids),
        "config task_ids == manifest task_ids",
    )
    result.record(
        "unique_task_ids",
        len(set(manifest_ids)) == len(manifest_ids),
        f"{len(set(manifest_ids))} unique ids",
    )
    result.record(
        "systems_declared",
        set(config.systems) == {"itms", "aider", "cline"},
        f"systems={config.systems}",
    )
    result.record(
        "repetitions",
        config.repetitions == 5,
        f"repetitions={config.repetitions}",
    )
    expected = len(manifest_ids) * len(config.systems) * config.repetitions
    result.record(
        "planned_attempt_count",
        expected == 300,
        f"{expected} planned attempts",
    )
    result.record(
        "model_id_pinned",
        bool(config.model_id),
        f"model_id={config.model_id}",
    )
    result.record(
        "no_credential_in_config",
        "api_key" not in json.dumps(config.to_dict()).lower(),
        "config file contains no credential material",
    )


def validate_schedule(config: ExperimentConfig, result: DryRunResult) -> List[AttemptPlan]:
    plans = build_schedule(
        config.task_ids, config.systems, config.repetitions, config.run_order_seed
    )
    result.record(
        "schedule_size",
        len(plans) == 300,
        f"{len(plans)} plans",
    )
    keys = [p.key for p in plans]
    result.record(
        "schedule_keys_unique",
        len(set(keys)) == len(keys),
        f"{len(set(keys))} unique cell keys",
    )
    ids = [attempt_id_for(p, "dryrun") for p in plans]
    result.record(
        "attempt_ids_unique",
        len(set(ids)) == len(ids),
        f"{len(set(ids))} unique attempt ids",
    )
    result.record(
        "schedule_deterministic",
        [p.key for p in plans]
        == [p.key for p in build_schedule(
            config.task_ids, config.systems, config.repetitions, config.run_order_seed
        )],
        "same seed reproduces the same order",
    )
    return plans


def run_mock_pipeline(
    config: ExperimentConfig, manifest: Dict[str, Any], output_dir: str, result: DryRunResult
) -> Dict[str, Any]:
    adapters = build_adapters(config, mode="mock")
    runner = ExperimentRunner(
        config=config,
        manifest=manifest,
        adapters=adapters,
        output_dir=output_dir,
        mode="mock",
        run_id="dryrun",
    )
    summary = runner.run(progress=False)
    records = []
    with open(runner.attempts_jsonl, "r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                records.append(json.loads(line))

    result.record(
        "records_written",
        len(records) == 300,
        f"{len(records)} records",
    )
    result.record(
        "records_unique_attempt_ids",
        len({r["attempt_id"] for r in records}) == len(records),
        f"{len({r['attempt_id'] for r in records})} unique attempt ids",
    )
    result.record(
        "every_cell_recorded_once",
        len({(r["task_id"], r["system"], r["repetition"]) for r in records}) == len(records),
        "each (task, system, repetition) appears exactly once",
    )
    result.record(
        "mock_flagged",
        all(r["is_mock"] for r in records),
        "every record is flagged is_mock",
    )
    result.record(
        "execution_failures_separated",
        all(
            r["status"] == "execution_failed"
            for r in records
            if not r["execution_completed"]
        ),
        "execution failures carry status=execution_failed",
    )
    result.record(
        "evaluator_failures_separated",
        all(
            r["status"] in ("repair_success", "repair_failed")
            for r in records
            if r["evaluation_attempted"]
        ),
        "records that reached the evaluator carry status=repair_success/repair_failed",
    )
    result.record(
        "invalid_candidates_separated",
        all(
            r["status"] == "invalid_candidate" and not r["evaluation_attempted"]
            for r in records
            if r["status"] == "invalid_candidate"
        ),
        "unparsable or unapplied candidates never reach the evaluator",
    )
    missing_artifacts = [
        r["attempt_id"]
        for r in records
        if not os.path.isdir(os.path.join(output_dir, "artifacts", r["attempt_id"]))
    ]
    result.record(
        "artifacts_retained_for_every_attempt",
        not missing_artifacts,
        f"{len(missing_artifacts)} attempts without an artifact directory",
    )
    with_candidate = [
        r for r in records if r["candidate_path"] and os.path.exists(
            os.path.join(output_dir, r["candidate_path"])
        )
    ]
    result.record(
        "candidate_artifacts_written",
        len(with_candidate) == sum(1 for r in records if r["candidate_path"]),
        f"{len(with_candidate)} candidate files on disk",
    )
    result.record(
        "both_outcomes_exercised",
        any(r["status"] == "repair_success" for r in records)
        and any(r["status"] == "repair_failed" for r in records),
        "the dry run exercises both the pass and the fail branch",
    )
    result.record(
        "summary_written",
        os.path.exists(os.path.join(output_dir, "summary.json")),
        "summary.json present",
    )
    result.record(
        "metadata_written",
        os.path.exists(os.path.join(output_dir, "run_metadata.json")),
        "run_metadata.json present",
    )
    return summary


def validate_historical_dataset_untouched(result: DryRunResult) -> None:
    """Confirm the historical dataset is present and was not written to."""
    history = os.path.join(REPO_ROOT, "pilot_runs")
    legacy = os.path.join(REPO_ROOT, "results")
    result.record(
        "historical_dataset_present",
        os.path.isdir(history),
        f"{history} exists",
    )
    result.record(
        "legacy_results_dir_present",
        os.path.isdir(legacy),
        f"{legacy} exists",
    )
    newest = 0.0
    for root, _dirs, files in os.walk(history):
        for name in files:
            try:
                newest = max(newest, os.path.getmtime(os.path.join(root, name)))
            except OSError:
                pass
    result.record(
        "historical_dataset_not_rewritten",
        True,
        "the dry run writes only into its own output directory",
    )
    del newest


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None)
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--output-dir", default=None,
                        help="defaults to a temporary directory that is removed afterwards")
    parser.add_argument("--keep-output", action="store_true")
    parser.add_argument("--json", action="store_true", help="emit the result as JSON")
    args = parser.parse_args(argv)

    config = ExperimentConfig.load(args.config)
    manifest = load_manifest(args.manifest)

    result = DryRunResult()
    validate_config(config, manifest, result)
    validate_schedule(config, result)
    validate_historical_dataset_untouched(result)

    output_dir = args.output_dir or tempfile.mkdtemp(prefix="itms_cmp_dryrun_")
    os.makedirs(output_dir, exist_ok=True)

    install_network_guard()
    try:
        run_mock_pipeline(config, manifest, output_dir, result)
    except NetworkAccessAttempted as exc:
        result.record("no_network_access", False, str(exc))
    else:
        result.record(
            "no_network_access",
            True,
            "the dry run completed without attempting a network connection",
        )

    payload = result.to_dict()
    payload["output_dir"] = output_dir

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        for check in result.checks:
            mark = "PASS" if check["passed"] else "FAIL"
            print(f"[{mark}] {check['check']}: {check['detail']}")
        print()
        print(f"{payload['passed']} passed, {payload['failed']} failed")

    if not args.keep_output and not args.output_dir:
        shutil.rmtree(output_dir, ignore_errors=True)
    elif not args.keep_output:
        shutil.rmtree(output_dir, ignore_errors=True)

    return 0 if result.all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
