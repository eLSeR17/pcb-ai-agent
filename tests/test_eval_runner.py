"""Tests for the eval runner and the regression guard.

``run_evals`` executes the committed golden dataset against the real read
layer (sizing formulas + audit rules over the fixtures) and must score 1.0
— the whole point of the harness is to lock the read layer down. The
regression guard tests use a temporary baseline file so the committed
``data/golden/baseline.json`` (written by the CLI) is never touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pcbai.eval.golden import GoldenDataError
from pcbai.eval.runner import (
    EvalReport,
    check_regression,
    run_evals,
    write_baseline,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = REPO_ROOT / "data" / "golden"


def test_run_evals_over_real_golden_scores_perfect() -> None:
    report = run_evals(GOLDEN_DIR)
    assert report.total == 21
    assert report.passed == 21
    assert report.failed == 0
    assert report.score == 1.0
    assert report.by_category == {
        "sizing": {"total": 15, "passed": 15, "failed": 0},
        "audit": {"total": 6, "passed": 6, "failed": 0},
    }
    # Deterministic ordering: results are sorted by case_id.
    ids = [result.case_id for result in report.results]
    assert ids == sorted(ids)
    # Every verdict carries a non-empty human-readable detail.
    assert all(result.details for result in report.results)


def test_run_evals_deterministic_across_runs() -> None:
    first = run_evals(GOLDEN_DIR)
    second = run_evals(GOLDEN_DIR)
    assert first.to_dict() == second.to_dict()


def test_run_evals_missing_golden_dir_raises(tmp_path: Path) -> None:
    with pytest.raises(GoldenDataError, match="sizing_cases.jsonl"):
        run_evals(tmp_path / "no-such-golden")


def test_check_regression_missing_baseline_is_ok(tmp_path: Path) -> None:
    report = run_evals(GOLDEN_DIR)
    verdict = check_regression(report, tmp_path / "no-baseline.json")
    assert verdict.ok
    assert verdict.baseline_score is None
    assert "no baseline" in verdict.message


def test_check_regression_with_written_baseline_ok(tmp_path: Path) -> None:
    report = run_evals(GOLDEN_DIR)
    baseline = tmp_path / "baseline.json"
    payload = write_baseline(report, baseline)
    assert payload["score"] == 1.0
    assert baseline.is_file()

    verdict = check_regression(report, baseline)
    assert verdict.ok
    assert verdict.baseline_score == 1.0
    assert verdict.score == 1.0


def test_check_regression_detects_simulated_regression(tmp_path: Path) -> None:
    report = run_evals(GOLDEN_DIR)
    baseline = tmp_path / "baseline.json"
    # A baseline that claims a score the current run cannot reach: any
    # later run drops more than REGRESSION_TOLERANCE below it.
    baseline.write_text(json.dumps({"score": 1.5}), encoding="utf-8")

    verdict = check_regression(report, baseline)
    assert not verdict.ok
    assert "REGRESSION" in verdict.message
    assert verdict.baseline_score == 1.5


def test_check_regression_corrupt_baseline_fails_loudly(tmp_path: Path) -> None:
    report = run_evals(GOLDEN_DIR)
    baseline = tmp_path / "baseline.json"
    baseline.write_text("{not json", encoding="utf-8")

    verdict = check_regression(report, baseline)
    assert not verdict.ok
    assert "unreadable" in verdict.message


def test_check_regression_baseline_without_score_fails(tmp_path: Path) -> None:
    report = run_evals(GOLDEN_DIR)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"total": 21}), encoding="utf-8")

    verdict = check_regression(report, baseline)
    assert not verdict.ok
    assert "score" in verdict.message


def test_write_baseline_refuses_a_failing_run(tmp_path: Path) -> None:
    failing = EvalReport(total=3, passed=1, failed=2, score=1 / 3)
    with pytest.raises(ValueError, match="refusing to write"):
        write_baseline(failing, tmp_path / "baseline.json")
