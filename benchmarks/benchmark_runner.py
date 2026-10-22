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
from typing import Any, Dict, List, Optional, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from benchmarks.benchmark.adapters import METHODS  # noqa: E402
from benchmarks.benchmark.budget import BudgetError, BudgetGate  # noqa: E402
from benchmarks.benchmark.experiment import (  # noqa: E402
    AttemptPlan,
    ExperimentError,
    ExperimentRunner,
    build_schedule,
)
from benchmarks.benchmark.output_budget import resolve_output_token_limit  # noqa: E402
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
    parser.add_argument(
        "--data-dir",
        default=None,
        help=(
            "root of the task dataset. The default is the HumanEval-derived "
            "family in benchmarks/data. The modular family in "
            "benchmarks/data/modular uses the same layout and can be "
            "selected with --data-dir benchmarks/data/modular."
        ),
    )
    parser.add_argument("--seed", type=int, default=None, help="seed for the deterministic run order.")
    parser.add_argument(
        "--schedule-file",
        default=None,
        help=(
            "path to a JSON file that lists the planned cells explicitly. "
            "The file holds a list of {\"task_id\", \"method\", \"rep\"} "
            "objects. It is needed when the request budget is smaller than "
            "the full cross product of tasks x methods x repetitions, because "
            "the default schedule cannot express a reduced plan. The cells "
            "must be unique and the number of cells is the planned number of "
            "attempts."
        ),
    )
    parser.add_argument("--temperature", type=float, default=0.2, help="sampling temperature (default: 0.2).")
    parser.add_argument(
        "--max-output-tokens",
        type=int,
        default=0,
        help=(
            "maximum output tokens per attempt. 0 (default) derives the limit "
            "from the task set so that the largest file the baseline strategy "
            "must re-emit always fits; the same limit is used by all three "
            "strategies and is recorded per attempt."
        ),
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=None,
        help=(
            "hard cap on provider requests. MANDATORY in live mode: a run "
            "without a request cap is unbounded. Defaults to the planned "
            "attempt count in mock mode."
        ),
    )
    parser.add_argument(
        "--max-spend-usd",
        type=float,
        default=None,
        help=(
            "optional cap on the cumulative cost the provider REPORTED. It is "
            "never applied to an estimate; when the provider does not report a "
            "cost, the attempt cap is the binding guard."
        ),
    )
    parser.add_argument(
        "--require-free-model",
        action="store_true",
        help="refuse to start unless the pinned model id is a ':free' route.",
    )
    parser.add_argument(
        "--seed-base",
        type=int,
        default=None,
        help=(
            "base value for the request-level seed. When set, every cell gets a "
            "deterministic seed derived from this base and the cell's position "
            "in the schedule. Omitting it means no seed is sent."
        ),
    )
    parser.add_argument(
        "--allow-shared-credential",
        action="store_true",
        help=(
            "acknowledge that the declared provider's credential is also "
            "configured for another provider. The live preflight refuses such "
            "a configuration by default."
        ),
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


def _load_schedule(path: str) -> Tuple[List["AttemptPlan"], Dict[str, Any]]:
    """
    Loads an explicit schedule from a JSON file.

    The file holds a list of ``{"task_id", "method", "rep"}`` objects, or an
    object with a ``"cells"`` key holding that list.  The function returns
    the cells and the provenance that is recorded in the run metadata.
    """
    import hashlib
    import json

    if not os.path.exists(path):
        raise ValueError(f"schedule file not found: {path}")
    with open(path, "rb") as handle:
        raw = handle.read()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"schedule file is not valid JSON: {exc}") from exc

    if isinstance(payload, dict):
        cells = payload.get("cells")
        if cells is None:
            raise ValueError(
                "schedule file is an object without a 'cells' list"
            )
        description = payload.get("description")
    else:
        cells = payload
        description = None
    if not isinstance(cells, list) or not cells:
        raise ValueError("schedule file must hold a non-empty list of cells")

    plans: List[AttemptPlan] = []
    for index, cell in enumerate(cells):
        if not isinstance(cell, dict):
            raise ValueError(f"schedule cell {index} is not an object")
        try:
            plans.append(
                AttemptPlan(
                    task_id=str(cell["task_id"]),
                    method=str(cell["method"]),
                    rep=int(cell["rep"]),
                )
            )
        except KeyError as exc:
            raise ValueError(
                f"schedule cell {index} is missing the field {exc}"
            ) from exc
    provenance = {
        "source": os.path.abspath(path),
        "sha256": digest,
        "cell_count": len(plans),
        "description": description,
    }
    return plans, provenance


def _validate_schedule_cells(schedule: List["AttemptPlan"], repository) -> None:
    """
    Validates the cells of an explicit schedule against the task set.

    The check runs *before* the live preflight and before the operator is
    asked to confirm the run, so a broken plan can never reach the
    provider.
    """
    known_tasks = set(repository.task_ids)
    seen = set()
    for cell in schedule:
        if cell.task_id not in known_tasks:
            raise ValueError(
                f"the schedule references an unknown task id: {cell.task_id}"
            )
        if cell.method not in METHODS:
            raise ValueError(
                f"the schedule references an unknown method: {cell.method}"
            )
        if cell.rep < 1:
            raise ValueError(
                f"the schedule references a repetition below 1: {cell.rep}"
            )
        if cell.key in seen:
            raise ValueError(f"the schedule contains a duplicate cell: {cell.key}")
        seen.add(cell.key)


def _resolve_output_dir(args) -> str:
    if args.output_dir:
        return args.output_dir
    base = os.path.join(_ROOT, "results")
    name = f"{args.mode}_{args.reps}reps"
    return os.path.join(base, name)


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        repository = TaskRepository(data_dir=args.data_dir)
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

    # -- an explicit schedule overrides the default cross product ---------
    schedule = None
    schedule_provenance: Dict[str, Any] = {}
    if args.schedule_file:
        try:
            schedule, schedule_provenance = _load_schedule(args.schedule_file)
            _validate_schedule_cells(schedule, repository)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if task_ids:
            wanted = set(task_ids)
            schedule = [p for p in schedule if p.task_id in wanted]
        if not schedule:
            print("error: the schedule file selects no cells", file=sys.stderr)
            return 2
        methods = sorted({p.method for p in schedule})
        selected_tasks = sorted({p.task_id for p in schedule})
        print(
            f"note: an explicit schedule of {len(schedule)} cell(s) was loaded "
            f"from {args.schedule_file}; --reps is ignored for such a schedule."
        )
        planned = len(schedule)
    else:
        planned = len(selected_tasks) * len(methods) * max(1, args.reps)
    output_dir = _resolve_output_dir(args)

    # -- the output-token limit is derived from the task set (B5) ---------
    selected_task_objects = repository.select(task_ids)
    limit_info = resolve_output_token_limit(
        selected_task_objects, explicit=args.max_output_tokens
    )
    output_limit = int(limit_info["resolved_limit"])

    # -- the request budget (B2) ------------------------------------------
    if args.mode == "live" and not args.max_attempts:
        print(
            "error: live mode requires an explicit --max-attempts limit. A run "
            "without a request cap is unbounded.",
            file=sys.stderr,
        )
        return 2
    max_attempts = args.max_attempts if args.max_attempts else planned
    if max_attempts > planned:
        # A cap above the plan cannot increase the spend: the plan already
        # bounds the number of requests.  The effective cap is the lower one.
        print(
            f"note: --max-attempts ({max_attempts}) is above the planned number "
            f"of attempts ({planned}); the effective cap is {planned}."
        )
        max_attempts = planned
    budget = BudgetGate(
        max_attempts=max_attempts,
        max_spend_usd=args.max_spend_usd,
        require_free_model=bool(args.require_free_model),
        model_id=args.model,
    )

    print("=" * 72)
    print("ITMS code-editing benchmark — Phase 1 runner")
    print("=" * 72)
    print(f"mode                : {args.mode}")
    print(f"provider            : {args.provider or '(mock engine)'}")
    print(f"model               : {args.model or '(mock engine)'}")
    print(f"tasks               : {len(selected_tasks)}")
    print(f"methods             : {', '.join(methods)}")
    if schedule is not None:
        _rep_counts: Dict[int, int] = {}
        for _cell in schedule:
            _rep_counts[_cell.rep] = _rep_counts.get(_cell.rep, 0) + 1
        print(
            "repetitions         : (explicit schedule) "
            + ", ".join(
                f"{count} cell(s) at rep {rep}"
                for rep, count in sorted(_rep_counts.items())
            )
        )
    else:
        print(f"repetitions         : {args.reps}")
    print(f"planned attempts    : {planned}")
    print(f"max attempts        : {max_attempts} (hard request cap)")
    print(f"max spend           : {args.max_spend_usd if args.max_spend_usd is not None else '(reported cost not capped)'}")
    print(f"output token limit  : {output_limit} ({limit_info['limit_source']})")
    print(f"request seed base   : {args.seed_base if args.seed_base is not None else '(no request-level seed)'}")
    print(f"task manifest digest: {repository.manifest_digest()}")
    print(f"output directory    : {output_dir}")
    print("-" * 72)

    if args.dry_run:
        if schedule is None:
            schedule = build_schedule(
                selected_tasks, methods, args.reps, seed=args.seed
            )
        if args.mode == "live":
            provider = LiveProvider(
                provider=args.provider or "",
                model_id=args.model or "",
                max_output_tokens=output_limit,
                temperature=args.temperature,
                request_timeout=args.request_timeout,
                max_retries=args.max_retries,
                allow_dynamic_alias=args.allow_dynamic_alias,
                allow_shared_credential=args.allow_shared_credential,
                enforce_credential_binding=True,
            )
            try:
                provider.preflight()
            except ProviderError as exc:
                print(f"live preflight: FAILED — {exc}", file=sys.stderr)
                return 2
            print("live preflight: OK (credentials and model id resolved)")
        try:
            budget.preflight()
        except BudgetError as exc:
            print(f"budget preflight: FAILED — {exc}", file=sys.stderr)
            return 2
        print(f"dry run: {len(schedule)} attempts scheduled, no provider contacted.")
        if args.schedule_file:
            by_method: Dict[str, int] = {}
            by_task: Dict[str, int] = {}
            for cell in schedule:
                by_method[cell.method] = by_method.get(cell.method, 0) + 1
                by_task[cell.task_id] = by_task.get(cell.task_id, 0) + 1
            print(f"dry run: cells per method: {by_method}")
            print(
                "dry run: attempts per task: "
                f"min={min(by_task.values())} max={max(by_task.values())} "
                f"over {len(by_task)} task(s)"
            )
            print(
                "dry run: every task is covered by every method: "
                f"{all(len({c.method for c in schedule if c.task_id == t}) == len(methods) for t in by_task)}"
            )
        return 0

    if args.mode == "live":
        provider = LiveProvider(
            provider=args.provider or "",
            model_id=args.model or "",
            max_output_tokens=output_limit,
            temperature=args.temperature,
            request_timeout=args.request_timeout,
            max_retries=args.max_retries,
            allow_dynamic_alias=args.allow_dynamic_alias,
            allow_shared_credential=args.allow_shared_credential,
            enforce_credential_binding=True,
        )
        try:
            provider.preflight()
        except ProviderError as exc:
            print(f"error: live mode is fail-closed — {exc}", file=sys.stderr)
            return 2
        try:
            budget.preflight()
        except BudgetError as exc:
            print(f"error: budget preflight failed — {exc}", file=sys.stderr)
            return 2

        print(f"WARNING: this will make {planned} live chat-completion requests.")
        print(
            f"WARNING: the run stops after {max_attempts} requests (hard cap)."
        )
        if args.max_spend_usd is not None:
            print(
                f"WARNING: the run also stops when the REPORTED cost reaches "
                f"${args.max_spend_usd:.4f}. A reported cost is only available "
                f"when the provider supplies one."
            )
        else:
            print(
                "WARNING: no spend cap is configured; the attempt cap is the only "
                "guard. Cost is only known when the provider reports it."
            )
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
        budget=budget,
        seed_base=args.seed_base,
        output_token_limit=output_limit,
        output_limit_justification=limit_info,
        schedule=schedule,
        schedule_provenance=schedule_provenance,
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
