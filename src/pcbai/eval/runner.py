"""Eval runner and regression guard for pcb-ai-agent.

:func:`run_evals` executes the golden dataset against the real read layer
(sizing math via ``pcbai.design.sizing``, design audit via
``pcbai.design.audit``) and scores every case with a judge
(:class:`~pcbai.eval.judge.HeuristicJudge` by default). The result is a
deterministic :class:`EvalReport` ordered by ``case_id``.

:func:`check_regression` implements the **regression guard**: an eval run
fails CI when its overall score drops more than
:data:`REGRESSION_TOLERANCE` below the stored baseline. A missing baseline
is *not* a failure (it is reported as "no baseline") — the baseline is
written explicitly with :func:`write_baseline` (CLI ``--write-baseline``).

Design notes
------------
- Execution is strictly sequential (no threads — ecosystem rule).
- Fixtures are resolved repository-root-relative from ``AuditCase.fixture``;
  ``repo_root`` defaults to ``golden_dir.parent.parent`` (``data/golden``
  -> repo root) and is overridable for tests.
- A sizing function that raises (invalid inputs) or a fixture that is
  missing/unreadable marks that one case as failed with the error message —
  the run continues, never aborts.
- The overall ``score`` is the fraction of passed cases; every passed case
  scores 1.0 under the heuristic judge, so the fraction and the mean
  coincide for CI runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pcbai.design.audit import audit_design
from pcbai.eval.golden import (
    SIZING_FUNCTIONS,
    AuditCase,
    SizingCase,
    load_golden,
)
from pcbai.eval.judge import HeuristicJudge, JudgeVerdict
from pcbai.kicad.netlist import NetlistError, SchematicError, parse_netlist, parse_schematic
from pcbai.kicad.sexpr import SExprError

__all__ = [
    "REGRESSION_TOLERANCE",
    "EvalReport",
    "EvalResult",
    "RegressionVerdict",
    "check_regression",
    "run_evals",
    "write_baseline",
]

#: Maximum allowed drop of the overall score vs the stored baseline.
REGRESSION_TOLERANCE: float = 0.02

_CATEGORY_SIZING = "sizing"
_CATEGORY_AUDIT = "audit"


@dataclass(frozen=True)
class EvalResult:
    """The scored outcome of one golden case."""

    case_id: str
    category: str
    passed: bool
    score: float
    details: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "passed": self.passed,
            "score": round(self.score, 4),
            "details": self.details,
        }


@dataclass
class EvalReport:
    """The aggregated result of one eval run.

    Attributes:
        total: Number of evaluated cases.
        passed: Number of cases that passed.
        failed: Number of cases that failed.
        by_category: Per-category ``{"total", "passed", "failed"}`` counts.
        score: Overall score = fraction of passed cases (``[0, 1]``).
        results: Per-case results ordered by ``case_id``.
    """

    total: int
    passed: int
    failed: int
    by_category: dict[str, dict[str, int]] = field(default_factory=dict)
    score: float = 0.0
    results: list[EvalResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "score": round(self.score, 4),
            "by_category": {
                category: dict(counts) for category, counts in self.by_category.items()
            },
            "results": [result.to_dict() for result in self.results],
        }


@dataclass(frozen=True)
class RegressionVerdict:
    """The regression-guard outcome of a run against a baseline.

    Attributes:
        ok: ``True`` when the score is within tolerance of the baseline, or
            when no baseline exists (a missing baseline is informational,
            not a failure).
        score: The current run's overall score.
        baseline_score: The stored baseline score, or ``None``.
        threshold: The tolerance used for the comparison.
        message: Human-readable explanation.
    """

    ok: bool
    score: float
    baseline_score: float | None
    threshold: float
    message: str


def run_evals(
    golden_dir: str | Path,
    *,
    judge: Any = None,
    repo_root: str | Path | None = None,
) -> EvalReport:
    """Evaluate the golden dataset in ``golden_dir`` and return a report.

    Args:
        golden_dir: Directory containing ``sizing_cases.jsonl`` and
            ``audit_cases.jsonl``.
        judge: The judge to score cases with (defaults to
            :class:`HeuristicJudge`). Any object exposing ``score_sizing``
            and ``score_audit`` works (e.g. :class:`LLMJudge`).
        repo_root: Base directory for fixture paths; defaults to
            ``golden_dir.parent.parent``.

    Raises:
        ValueError: when the golden set is empty (a guard with no data is a
            configuration error, not a pass).
    """
    golden = load_golden(golden_dir)
    if golden.total == 0:
        raise ValueError("run_evals requires at least one golden case")

    root = Path(repo_root) if repo_root else Path(golden_dir).resolve().parent.parent
    active_judge = judge or HeuristicJudge()

    results: list[EvalResult] = []
    results.extend(_evaluate_sizing_cases(golden.sizing, active_judge))
    results.extend(_evaluate_audit_cases(golden.audit, active_judge, root))
    results.sort(key=lambda result: result.case_id)

    passed = sum(1 for result in results if result.passed)
    return EvalReport(
        total=len(results),
        passed=passed,
        failed=len(results) - passed,
        by_category=_by_category(results),
        score=passed / len(results),
        results=results,
    )


def check_regression(report: EvalReport, baseline_path: str | Path) -> RegressionVerdict:
    """Compare ``report.score`` against a stored baseline JSON file.

    A missing baseline reports ``ok=True`` with ``baseline_score=None``
    (first-run mode; write one with :func:`write_baseline`). An unreadable
    or invalid baseline reports ``ok=False`` — a corrupt guard must fail
    loudly so it gets re-written.
    """
    path = Path(baseline_path)
    if not path.is_file():
        return RegressionVerdict(
            ok=True,
            score=report.score,
            baseline_score=None,
            threshold=REGRESSION_TOLERANCE,
            message=f"no baseline file at {path} (first run?) — write one with --write-baseline",
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return RegressionVerdict(
            ok=False,
            score=report.score,
            baseline_score=None,
            threshold=REGRESSION_TOLERANCE,
            message=f"baseline {path} unreadable ({type(exc).__name__}: {exc})",
        )
    baseline_score = data.get("score") if isinstance(data, dict) else None
    if not isinstance(baseline_score, (int, float)):
        return RegressionVerdict(
            ok=False,
            score=report.score,
            baseline_score=None,
            threshold=REGRESSION_TOLERANCE,
            message=f"baseline {path} has no numeric 'score' field",
        )
    ok = report.score >= float(baseline_score) - REGRESSION_TOLERANCE
    return RegressionVerdict(
        ok=ok,
        score=report.score,
        baseline_score=float(baseline_score),
        threshold=REGRESSION_TOLERANCE,
        message=(
            f"score {report.score:.4f} vs baseline {baseline_score:.4f} "
            f"(tolerance {REGRESSION_TOLERANCE:.4f}) -> {'OK' if ok else 'REGRESSION'}"
        ),
    )


def write_baseline(report: EvalReport, baseline_path: str | Path) -> dict[str, Any]:
    """Persist the current run as the new regression baseline.

    Writes ``{"project", "suite", "score", "total", "passed",
    "by_category", "created_at"}`` as pretty JSON and returns the payload.
    """
    if report.failed:
        raise ValueError(
            f"refusing to write a baseline for a failing run "
            f"({report.failed}/{report.total} cases failed)"
        )
    payload = {
        "project": "pcb-ai-agent",
        "suite": "golden-evals",
        "score": round(report.score, 4),
        "total": report.total,
        "passed": report.passed,
        "by_category": {category: dict(counts) for category, counts in report.by_category.items()},
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    path = Path(baseline_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


# ---------------------------------------------------------------------------
# Per-category evaluation
# ---------------------------------------------------------------------------


def _evaluate_sizing_cases(cases: tuple[SizingCase, ...], judge: Any) -> list[EvalResult]:
    results: list[EvalResult] = []
    for case in cases:
        try:
            actual = _compute_sizing(case)
        except (ValueError, TypeError) as exc:
            results.append(_error_result(case.id, _CATEGORY_SIZING, f"sizing error: {exc}"))
            continue
        verdict = judge.score_sizing(case, actual)
        results.append(_verdict_result(case.id, _CATEGORY_SIZING, verdict))
    return results


def _evaluate_audit_cases(
    cases: tuple[AuditCase, ...], judge: Any, repo_root: Path
) -> list[EvalResult]:
    results: list[EvalResult] = []
    for case in cases:
        try:
            rules, severities = _audit_fixture(repo_root / case.fixture)
        except (OSError, NetlistError, SchematicError, SExprError, ValueError) as exc:
            results.append(_error_result(case.id, _CATEGORY_AUDIT, f"fixture error: {exc}"))
            continue
        verdict = judge.score_audit(case, rules, severities)
        results.append(_verdict_result(case.id, _CATEGORY_AUDIT, verdict))
    return results


def _compute_sizing(case: SizingCase) -> float | list[float]:
    """Run the sizing function of ``case`` and normalise the result shape."""
    function = SIZING_FUNCTIONS.get(case.function)
    if function is None:
        raise ValueError(f"unknown sizing function {case.function!r}")
    result = function(**case.inputs)
    if isinstance(result, tuple):
        return [float(value) for value in result]
    return float(result)


def _audit_fixture(path: Path) -> tuple[set[str], dict[str, str]]:
    """Audit a KiCad fixture file; returns (rule ids, rule -> severity)."""
    if not path.is_file():
        raise FileNotFoundError(f"audit fixture not found: {path}")
    suffix = path.suffix.lower()
    if suffix in _NETLIST_SUFFIXES:
        design = parse_netlist(path)
    elif suffix in _SCHEMATIC_SUFFIXES:
        design = parse_schematic(path)
    else:
        raise ValueError(
            f"unsupported fixture extension {suffix!r} (need .kicad_net/.net/.kicad_sch)"
        )
    report = audit_design(design, source=suffix.lstrip("."))
    rules = {finding.rule for finding in report.findings}
    severities = {finding.rule: finding.severity for finding in report.findings}
    return rules, severities


_NETLIST_SUFFIXES: frozenset[str] = frozenset({".kicad_net", ".net"})
_SCHEMATIC_SUFFIXES: frozenset[str] = frozenset({".kicad_sch"})


def _verdict_result(case_id: str, category: str, verdict: JudgeVerdict) -> EvalResult:
    return EvalResult(
        case_id=case_id,
        category=category,
        passed=verdict.passed,
        score=verdict.score,
        details=verdict.details,
    )


def _error_result(case_id: str, category: str, details: str) -> EvalResult:
    return EvalResult(case_id=case_id, category=category, passed=False, score=0.0, details=details)


def _by_category(results: list[EvalResult]) -> dict[str, dict[str, int]]:
    """``{"sizing": {"total", "passed", "failed"}, "audit": {...}}``."""
    categories: dict[str, dict[str, int]] = {}
    for category in (_CATEGORY_SIZING, _CATEGORY_AUDIT):
        cases = [result for result in results if result.category == category]
        passed = sum(1 for result in cases if result.passed)
        categories[category] = {
            "total": len(cases),
            "passed": passed,
            "failed": len(cases) - passed,
        }
    return categories
