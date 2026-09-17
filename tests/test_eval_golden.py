"""Tests for the golden dataset loaders.

The golden JSONL files are the ground truth of the eval harness: these
tests pin their schema (not their values — the values are validated by the
runner tests against the real read layer) plus the strict validation of
the loaders.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pcbai.eval.golden import (
    DEFAULT_SIZING_TOLERANCE,
    AuditCase,
    GoldenDataError,
    SizingCase,
    load_audit_cases,
    load_golden,
    load_sizing_cases,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = REPO_ROOT / "data" / "golden"

_VALID_SEVERITIES = frozenset({"warning", "error", "info"})
_VALID_FIXTURE_SUFFIXES = frozenset({".kicad_net", ".net", ".kicad_sch"})


# ---------------------------------------------------------------------------
# Dataset shape (the values themselves are exercised by the runner tests)
# ---------------------------------------------------------------------------


def test_golden_set_counts_and_ordering() -> None:
    golden = load_golden(GOLDEN_DIR)
    assert golden.total == 21
    assert len(golden.sizing) == 15
    assert len(golden.audit) == 6


def test_golden_ids_unique_across_both_categories() -> None:
    golden = load_golden(GOLDEN_DIR)
    ids = [case.id for case in golden.sizing] + [case.id for case in golden.audit]
    assert len(ids) == len(set(ids)), "duplicate case_id across the golden set"


def test_sizing_cases_schema() -> None:
    cases = load_sizing_cases(GOLDEN_DIR / "sizing_cases.jsonl")
    fixture_functions = {
        "led_series_resistor",
        "pull_up_resistor",
        "voltage_divider",
        "decoupling_capacitor",
    }
    for case in cases:
        assert isinstance(case, SizingCase)
        assert case.id.startswith("sizing_")
        assert case.function in fixture_functions
        assert case.inputs, f"{case.id}: inputs must be non-empty"
        assert all(isinstance(value, (int, float)) for value in case.inputs.values())
        assert len(case.why.strip()) > 20, f"{case.id}: 'why' must document the math"
        expected = [case.expected] if isinstance(case.expected, (int, float)) else case.expected
        assert len(expected) >= 1
        assert all(isinstance(value, (int, float)) for value in expected)
        assert case.tolerance > 0


def test_audit_cases_schema() -> None:
    cases = load_audit_cases(GOLDEN_DIR / "audit_cases.jsonl")
    for case in cases:
        assert isinstance(case, AuditCase)
        assert case.id.startswith("audit_")
        assert case.fixture.endswith(tuple(_VALID_FIXTURE_SUFFIXES))
        assert (REPO_ROOT / case.fixture).is_file(), f"{case.id}: fixture missing"
        # Severity contract is a subset of the expected rules.
        assert set(case.expected_severities) <= set(case.expected_rules)
        assert set(case.expected_severities.values()) <= _VALID_SEVERITIES
        assert len(case.why.strip()) > 20


def test_sizing_cases_cover_all_formulas_and_polarities() -> None:
    cases = load_sizing_cases(GOLDEN_DIR / "sizing_cases.jsonl")
    by_function: dict[str, int] = {}
    for case in cases:
        by_function[case.function] = by_function.get(case.function, 0) + 1
    assert by_function == {
        "led_series_resistor": 5,
        "pull_up_resistor": 3,
        "voltage_divider": 3,
        "decoupling_capacitor": 4,
    }


def test_tolerance_default_matches_constant() -> None:
    assert DEFAULT_SIZING_TOLERANCE > 0
    assert DEFAULT_SIZING_TOLERANCE < 1e-3  # tight: E12-rounded values, not bands


# ---------------------------------------------------------------------------
# Loader validation
# ---------------------------------------------------------------------------


def _write_golden_dir(tmp_path: Path, sizing_lines: list[str], audit_lines: list[str]) -> Path:
    directory = tmp_path / "golden"
    directory.mkdir()
    (directory / "sizing_cases.jsonl").write_text("\n".join(sizing_lines) + "\n", encoding="utf-8")
    (directory / "audit_cases.jsonl").write_text("\n".join(audit_lines) + "\n", encoding="utf-8")
    return directory


def _valid_sizing_line(case_id: str = "sizing_tmp") -> str:
    return (
        '{"case_id": "' + case_id + '", "description": "temp case", '
        '"function": "led_series_resistor", "inputs": {"v_supply": 5.0, '
        '"v_led": 2.0, "i_led": 0.02}, "expected": 150.0, '
        '"why": "R = (5.0 - 2.0) / 0.02 = 150 ohm, E12 keeps 150"}'
    )


def _valid_audit_line(case_id: str = "audit_tmp") -> str:
    return (
        '{"case_id": "' + case_id + '", "description": "temp case", '
        '"fixture": "tests/fixtures/simple-led.kicad_net", '
        '"expected_rules": ["NO_DRIVER"], "expected_severities": {"NO_DRIVER": "info"}, '
        '"allow_extra_rules": false, '
        '"why": "the netlist variant carries the NO_DRIVER advisory by design"}'
    )


def test_load_golden_from_directory(tmp_path: Path) -> None:
    directory = _write_golden_dir(tmp_path, [_valid_sizing_line()], [_valid_audit_line()])
    golden = load_golden(directory)
    assert golden.total == 2
    assert golden.sizing[0].id == "sizing_tmp"
    assert golden.audit[0].id == "audit_tmp"


def test_missing_files_raise(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(GoldenDataError, match="sizing_cases.jsonl"):
        load_golden(empty)


def test_duplicate_case_id_rejected(tmp_path: Path) -> None:
    sizing = [_valid_sizing_line("sizing_dup"), _valid_sizing_line("sizing_dup")]
    directory = _write_golden_dir(tmp_path, sizing, [_valid_audit_line()])
    with pytest.raises(GoldenDataError, match="duplicate case_id"):
        load_golden(directory)


def test_malformed_json_line_rejected(tmp_path: Path) -> None:
    directory = _write_golden_dir(tmp_path, ["{not json", "{}"], [_valid_audit_line()])
    with pytest.raises(GoldenDataError, match="sizing_cases.jsonl"):
        load_golden(directory)


def test_audit_severity_must_be_subset_of_rules(tmp_path: Path) -> None:
    line = (
        '{"case_id": "audit_tmp", "description": "temp", '
        '"fixture": "tests/fixtures/simple-led.kicad_net", '
        '"expected_rules": ["LED_NO_LIMITER"], '
        '"expected_severities": {"NO_DRIVER": "info"}, '
        '"allow_extra_rules": false, "why": "severity for a rule not expected"}'
    )
    directory = _write_golden_dir(tmp_path, [_valid_sizing_line()], [line])
    with pytest.raises(GoldenDataError, match="expected_severities"):
        load_golden(directory)


def test_unknown_severity_rejected(tmp_path: Path) -> None:
    line = (
        '{"case_id": "audit_tmp", "description": "temp", '
        '"fixture": "tests/fixtures/simple-led.kicad_net", '
        '"expected_rules": ["NO_DRIVER"], '
        '"expected_severities": {"NO_DRIVER": "critical"}, '
        '"allow_extra_rules": false, "why": "critical is not a valid severity"}'
    )
    directory = _write_golden_dir(tmp_path, [_valid_sizing_line()], [line])
    with pytest.raises(GoldenDataError, match="unknown severity"):
        load_golden(directory)
