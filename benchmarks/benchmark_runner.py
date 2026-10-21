#!/usr/bin/env python3
"""
Phase 1 benchmark runner.

Compares three editing methods on the 20 Phase 0 HumanEval-derived repair
tasks:

    1. full_file_regeneration
    2. search_replace_patch
    3. itms_ast_splice

The full planned experiment is ``20 tasks x 3 methods x 5 reps = 300``
attempts.

Safety rules enforced by this CLI
---------------------------------
*   the default mode is ``mock``; a live provider is never contacted unless
    ``--mode live`` is passed explicitly,
*   ``--mode live`` requires an explicit provider, a pinned model id and a
    configured credential; it fails closed otherwise,
*   ``--mode live`` prints the planned number of provider calls and requires an
    interactive confirmation (or the explicitly documented ``--yes`` flag),
*   ``--dry-run`` validates the configuration and prints the schedule without
    contacting any provider,
*   an existing, non-empty output directory is never overwritten,
*   resuming an incomplete experiment is refused.

Examples
--------
Offline configuration check (no provider call at all)::

    python benchmarks/benchmark_runner.py --dry-run --mode live --provider openrouter --model <id>

Pipeline smoke test with the deterministic mock engine::

    python benchmarks/benchmark_runner.py --mode mock --tasks HE-EDIT-001 --reps 1 \
        --output-dir results/mock_smoke

The real experiment (requires a configured credential and an explicit
confirmation)::

    python benchmarks/benchmark_runner.py --mode live --provider openrouter \
        --model <pinned-model-id> --reps 5 --output-dir results/<experiment-name>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from benchmarks.benchmark.adapters import METHODS  # noqa: E402
from benchmarks.benchmark.experiment import ExperimentError, ExperimentRunner, build_schedule  # noqa: E402
from benchmarks.benchmark.provider import (  # noqa: E402
    LiveProvider,
    MockProvider,
    ProviderError,
)
from benchmarks.benchmark.tasks import TaskRepository  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Phase 1 ITMS code-editing benchmark runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--mode",
        choices=("mock", "live"),
        default="mock",
        help="provider mode. Default: mock (offline, never contacts a provider).",
    )
    parser.add_argument(
        "--methods",
        default=",".join(METHODS),
        help="comma-separated editing methods (default: all three).",
    )
    parser.add_argument(
        "--tasks",
        default=None,
        help="comma-separated task ids (default: all 20 accepted tasks).",
    )
    parser.add_argument("--reps", type=int, default=5, help="repetitions per task/method (default: 5).")
    parser.add_argument("--provider", default=None, help="live provider name, e.g. openrouter.")
    parser.add_argument("--model", default=None, help="pinned model identifier used for live mode.")
    parser.add_argument(
        "--allow-dynamic-alias",
        action="store_true",
        help="allow a dynamic model alias (e.g. openrouter/free) in live mode.",
    )
    parser.add_argument("--output-dir", default=None, help="output directory for this experiment.")
    parser.add_argument("--seed", type=int, default=None, help="seed for the deterministic run order.")
    parser.add_argument("--temperature", type=float, default=0.2, help="sampling temperature (default: 0.2).")
    parser.add_argument(
        "--max-output-tokens", type=int, default=2048, help="maximum output tokens (default: 2048)."
    )
    parser.add_argument(
        "--request-timeout", type=float, default=60.0, help="per-request provider timeout in seconds."
    )
    parser.add_argument(
        "--evaluation-timeout",
        type=float,
        default=10.0,
        help="timeout for the official test of one candidate, in seconds.",
    )
    parser.add_argument(
        "--max-retries", type=int, default=2, help="bounded provider retry policy (default: 2)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate the configuration and print the schedule; no provider is contacted.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="skip the interactive confirmation for a live run (documented escape hatch).",
    )
    parser.add_argument(
        "--allow-overwrite",
        action="store_true",
        help="permit writing into an existing, non-empty output directory.",
    )
    parser.add_argument(
        "--mock-policy",
        choices=("wrong_answer", "malformed"),
        default="wrong_answer",
        help="deterministic mock response shape (mock mode only).",
    )
    return parser


def _split(value: Optional[str]) -> Optional[List[str]]:
    if not value:
        return None
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or None


def _resolve_output_dir(args) -> str:
    if args.output_dir:
        return args.output_dir
    base = os.path.join(_ROOT, "results")
    name = f"{args.mode}_{args.reps}reps"
    return os.path.join(base, name)


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        repository = TaskRepository()
    except Exception as exc:
        print(f"error: could not load the Phase 0 task set: {exc}", file=sys.stderr)
        return 2

    methods = _split(args.methods) or list(METHODS)
    unknown_methods = [m for m in methods if m not in METHODS]
    if unknown_methods:
        print(
            f"error: unknown editing method(s): {', '.join(unknown_methods)}. "
            f"Available methods: {', '.join(METHODS)}",
            file=sys.stderr,
        )
        return 2
    task_ids = _split(args.tasks)
    try:
        selected_tasks = [t.task_id for t in repository.select(task_ids)]
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    planned = len(selected_tasks) * len(methods) * max(1, args.reps)
    output_dir = _resolve_output_dir(args)

    print("=" * 72)
    print("ITMS code-editing benchmark — Phase 1 runner")
    print("=" * 72)
    print(f"mode                : {args.mode}")
    print(f"provider            : {args.provider or '(mock engine)'}")
    print(f"model               : {args.model or '(mock engine)'}")
    print(f"tasks               : {len(selected_tasks)}")
    print(f"methods             : {', '.join(methods)}")
    print(f"repetitions         : {args.reps}")
    print(f"planned attempts    : {planned}")
    print(f"task manifest digest: {repository.manifest_digest()}")
    print(f"output directory    : {output_dir}")
    print("-" * 72)

    if args.dry_run:
        schedule = build_schedule(selected_tasks, methods, args.reps, seed=args.seed)
        if args.mode == "live":
            provider = LiveProvider(
                provider=args.provider or "",
                model_id=args.model or "",
                max_output_tokens=args.max_output_tokens,
                temperature=args.temperature,
                request_timeout=args.request_timeout,
                max_retries=args.max_retries,
                allow_dynamic_alias=args.allow_dynamic_alias,
            )
            try:
                provider.preflight()
            except ProviderError as exc:
                print(f"live preflight: FAILED — {exc}", file=sys.stderr)
                return 2
            print("live preflight: OK (credentials and model id resolved)")
        print(f"dry run: {len(schedule)} attempts scheduled, no provider contacted.")
        return 0

    if args.mode == "live":
        provider = LiveProvider(
            provider=args.provider or "",
            model_id=args.model or "",
            max_output_tokens=args.max_output_tokens,
            temperature=args.temperature,
            request_timeout=args.request_timeout,
            max_retries=args.max_retries,
            allow_dynamic_alias=args.allow_dynamic_alias,
        )
        try:
            provider.preflight()
        except ProviderError as exc:
            print(f"error: live mode is fail-closed — {exc}", file=sys.stderr)
            return 2

        print(f"WARNING: this will make {planned} live chat-completion requests.")
        print(
            "WARNING: the provider may additionally be asked once for its model "
            "catalog (one request per run, not per attempt)."
        )
        print("WARNING: live results are the only kind of result that may be reported.")
        if not args.yes:
            try:
                answer = input(f"Type 'yes' to proceed with {planned} live calls: ")
            except EOFError:
                print("error: no interactive confirmation available; refusing to run.", file=sys.stderr)
                return 2
            if answer.strip().lower() != "yes":
                print("aborted by the operator.")
                return 1
        print("-" * 72)
    else:
        provider = MockProvider(policy=args.mock_policy)

    runner = ExperimentRunner(
        repository=repository,
        provider=provider,
        mode=args.mode,
        output_dir=output_dir,
        methods=methods,
        reps=args.reps,
        task_ids=selected_tasks,
        seed=args.seed,
        evaluation_timeout=args.evaluation_timeout,
        allow_overwrite=args.allow_overwrite,
        model_id=args.model,
    )

    try:
        summary = runner.run()
    except ExperimentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print("-" * 72)
    print(f"recorded attempts : {summary['recorded_attempts']}")
    print(f"mode              : {summary['mode']}")
    if summary.get("mock_warning"):
        print(f"WARNING          : {summary['mock_warning']}")
    print(f"summary           : {os.path.join(runner.output_paths()['summary_json'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
