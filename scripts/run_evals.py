"""CLI for the pcb-ai-agent eval harness.

Runs the golden dataset through the deterministic read layer, prints a
per-case table plus the overall score, checks the regression baseline and
returns a semantic exit code for CI:

- ``0`` — every case passed and the score is within the regression
  tolerance of the baseline (or no baseline exists yet);
- ``1`` — any case failed, a regression was detected, or the baseline is
  corrupt.

Run from the repository root::

    python3 scripts/run_evals.py                # heuristic judge, CI-safe
    python3 scripts/run_evals.py --write-baseline   # persist current score
    python3 scripts/run_evals.py --llm-judge        # opt-in, NOT for CI

``--llm-judge`` wraps the deterministic run with the qualitative
:class:`~pcbai.eval.judge.LLMJudge`; it degrades to the heuristic verdict
when Ollama is unreachable and is documented as a review aid, never a CI
dependency.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# The script is executed standalone (`python3 scripts/run_evals.py`) and must
# find the package without a prior `pip install`: the src/ directory is
# prepended to sys.path before the (single) import block below. E402 is
# required — this path bootstrap IS the reason imports are not at the very
# top of the file.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from pcbai.eval.judge import HeuristicJudge, LLMJudge  # noqa: E402
from pcbai.eval.runner import (  # noqa: E402
    EvalReport,
    check_regression,
    run_evals,
    write_baseline,
)

_DEFAULT_GOLDEN_DIR = "data/golden"
_DEFAULT_BASELINE = "data/golden/baseline.json"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="run_evals.py",
        description="Run the pcb-ai-agent golden evals (heuristic judge, CI-safe).",
    )
    parser.add_argument(
        "--golden-dir",
        default=_DEFAULT_GOLDEN_DIR,
        help=f"golden dataset directory (default: {_DEFAULT_GOLDEN_DIR})",
    )
    parser.add_argument(
        "--baseline",
        default=_DEFAULT_BASELINE,
        help=f"regression baseline JSON (default: {_DEFAULT_BASELINE})",
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="write/update the regression baseline from this run, then check against it",
    )
    parser.add_argument(
        "--llm-judge",
        action="store_true",
        help="opt-in qualitative LLM judge via local Ollama (NOT for CI)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    golden_dir = Path(args.golden_dir)
    if not golden_dir.is_absolute():
        golden_dir = _REPO_ROOT / golden_dir
    baseline_path = Path(args.baseline)
    if not baseline_path.is_absolute():
        baseline_path = _REPO_ROOT / baseline_path

    judge = LLMJudge() if args.llm_judge else HeuristicJudge()
    report = run_evals(golden_dir, judge=judge, repo_root=_REPO_ROOT)

    _print_report(report, judge_name=judge.name, llm_mode=args.llm_judge)

    if args.write_baseline:
        payload = write_baseline(report, baseline_path)
        print(f"\n[baseline] wrote {baseline_path} (score {payload['score']:.4f})")

    regression = check_regression(report, baseline_path)
    print(f"[regression] {regression.message}")

    if report.failed:
        print(f"[result] FAIL — {report.failed}/{report.total} case(s) failed")
        return 1
    if not regression.ok:
        print("[result] FAIL — regression against baseline")
        return 1
    print(f"[result] OK — {report.passed}/{report.total} cases passed")
    return 0


def _print_report(report: EvalReport, *, judge_name: str, llm_mode: bool) -> None:
    title = (
        f"pcb-ai-agent eval harness — {report.total} golden cases — "
        f"judge: {judge_name}{' (llm opt-in)' if llm_mode else ''}"
    )
    print("=" * len(title))
    print(title)
    print("=" * len(title))
    print(f"{'STATUS':<6} {'CATEGORY':<8} {'SCORE':>7}  CASE")
    print("-" * 100)
    for result in sorted(report.results, key=lambda item: item.case_id):
        status = "PASS" if result.passed else "FAIL"
        print(f"{status:<6} {result.category:<8} {result.score:>7.4f}  {result.case_id}")
    print("-" * 100)
    parts = []
    for category, counts in report.by_category.items():
        parts.append(f"{category}: {counts['passed']}/{counts['total']} passed")
    print(f"  {' | '.join(parts)}")
    print(f"  overall score: {report.score:.4f} ({report.passed}/{report.total})")
    print("\n  detail:")
    for result in sorted(report.results, key=lambda item: item.case_id):
        print(f"    - {result.case_id}: {result.details}")


if __name__ == "__main__":
    sys.exit(main())
