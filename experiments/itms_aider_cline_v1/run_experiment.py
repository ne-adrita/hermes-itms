"""Command-line entry point for the live experiment.

Phase 2 only.  ``--dry-run`` performs the offline validation instead and
never contacts a provider.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from typing import List, Optional

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from experiments.itms_aider_cline_v1.config import (  # noqa: E402
    ExperimentConfig,
    load_manifest,
)
from experiments.itms_aider_cline_v1.runner import run_experiment  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None)
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--mode", choices=["live", "mock"], default="live")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--systems", default=None,
                        help="comma separated subset of itms,aider,cline")
    parser.add_argument("--tasks", default=None,
                        help="comma separated subset of task ids")
    parser.add_argument("--repetitions", type=int, default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true",
                        help="validate the plan without calling any provider")
    parser.add_argument("--yes", action="store_true",
                        help="do not ask for confirmation before a live run")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    config = ExperimentConfig.load(args.config)
    manifest = load_manifest(args.manifest)

    if args.systems:
        config.systems = [s.strip() for s in args.systems.split(",") if s.strip()]
    if args.tasks:
        config.task_ids = [t.strip() for t in args.tasks.split(",") if t.strip()]
    if args.repetitions is not None:
        config.repetitions = args.repetitions
    if args.model:
        config.model_id = args.model
    if args.seed is not None:
        config.run_order_seed = args.seed

    if args.dry_run:
        from experiments.itms_aider_cline_v1.dry_run import main as dry_main

        return dry_main([])

    output_dir = args.output_dir or os.path.join(
        REPO_ROOT,
        config.output_root,
        f"{config.experiment_id}_{args.mode}_{uuid.uuid4().hex[:8]}",
    )
    if os.path.exists(output_dir) and os.listdir(output_dir):
        print(f"refusing to write into a non-empty directory: {output_dir}",
              file=sys.stderr)
        return 2

    print(f"experiment : {config.experiment_id}")
    print(f"systems    : {config.systems}")
    print(f"tasks      : {len(config.task_ids)}")
    print(f"repetitions: {config.repetitions}")
    print(f"planned    : {config.planned_attempts} attempts")
    print(f"model      : {config.provider}/{config.model_id}")
    print(f"output     : {output_dir}")

    if args.mode == "live" and not args.yes:
        answer = input("Proceed with a live run? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("aborted")
            return 1

    summary = run_experiment(
        config=config,
        manifest=manifest,
        output_dir=output_dir,
        mode=args.mode,
    )
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
