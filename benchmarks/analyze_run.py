#!/usr/bin/env python3
"""
Offline analysis of a completed ITMS code-editing benchmark run.

The script reads the artefacts of a finished run and regenerates the tables
and figures.  It makes **no network request**, contacts **no provider** and
never writes into the run directory: everything is written to ``--out-dir``
(default ``<run-dir>/analysis``).

It also performs an **independent integrity validation** of the run before any
number is presented: matrix completeness, JSONL/CSV agreement, artefact
presence, candidate digest verification, prompt persistence, mock
contamination, retention of failed and unevaluated attempts, null (never zero)
telemetry, model identity, a single output cap across the strategies, seed
consistency, and cost reporting.  When a check fails, the report is written
with a *VALIDATION FAILED* banner and the exit code is 2.

Examples
--------
::

    python benchmarks/analyze_run.py --run-dir results/my_run
    python benchmarks/analyze_run.py --run-dir results/my_run --out-dir /tmp/analysis
    python benchmarks/analyze_run.py --run-dir results/my_run --no-figures
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

from benchmarks.benchmark.report import AnalysisError, write_analysis  # noqa: E402


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--run-dir", required=True, help="the run directory to analyse")
    parser.add_argument(
        "--out-dir",
        default=None,
        help="where to write analysis.json, RESULTS_SUMMARY.md and the figures "
        "(default: <run-dir>/analysis)",
    )
    parser.add_argument(
        "--no-figures", action="store_true", help="write the analysis but no figures"
    )
    parser.add_argument(
        "--label",
        default=None,
        help="a headline label written at the top of the report, e.g. "
        "'SMALL LIVE PILOT - 9 requests, not statistically conclusive'",
    )
    parser.add_argument(
        "--note",
        default=None,
        help="additional setup/limitation paragraphs written under the label",
    )
    args = parser.parse_args(argv)

    try:
        analysis = write_analysis(
            run_dir=args.run_dir,
            out_dir=args.out_dir,
            write_figures=not args.no_figures,
            label=args.label,
            note=args.note,
        )
    except AnalysisError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # pragma: no cover - defensive
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(f"run directory   : {analysis['run_dir']}")
    print(f"mode            : {analysis['mode']}")
    print(f"is_measurement  : {analysis['is_measurement']}")
    print(f"records         : {analysis['denominators']['recorded_attempts']}")
    print(f"evaluated       : {analysis['denominators']['evaluated_attempts']}")
    print(
        "integrity       : "
        + ("ALL CHECKS PASSED" if analysis["integrity"]["all_passed"] else "VALIDATION FAILED")
    )
    print(f"analysis        : {analysis.get('analysis_path')}")
    print(f"summary         : {analysis.get('summary_path')}")
    for figure in analysis.get("figures", []):
        print(f"figure          : {figure}")

    if not analysis["integrity"]["all_passed"]:
        for check in analysis["integrity"]["checks"]:
            if not check["passed"]:
                print(f"FAIL {check['check']}: {check['detail']}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
