"""Golden dataset: case models and JSONL loaders for the eval harness.

Each golden case encodes an *expected* outcome computed by hand with the
documented formulas — the ground truth the harness measures the code
against. The JSONL files under ``data/golden/`` are the dataset; this
module only loads and validates them (no decision logic lives here — that
is the judges' job in :mod:`pcbai.eval.judge`).

Dataset contracts
-----------------
``sizing_cases.jsonl`` — one :class:`SizingCase` per line::

    {"id": "...", "description": "...", "function": "led_series_resistor",
     "inputs": {"v_supply": 5.0, "v_led": 2.0, "i_led": 0.02},
     "expected": [150.0, 0.06], "why": "R=(5-2)/0.02=150 -> E12 150; P=..."}

``audit_cases.jsonl`` — one :class:`AuditCase` per line::

    {"case_id": "...", "description": "...",
     "fixture": "tests/fixtures/bad-led.kicad_net",
     "expected_rules": ["LED_NO_LIMITER"], "expected_severities": {...},
     "allow_extra_rules": false, "why": "..."}

Validation rules (raising :class:`GoldenDataError`):

- every file must contain at least one valid JSON object per line;
- ``case_id`` values are unique within a file (and across the golden set);
- ``function`` must be a known sizing function and ``expected`` must be a
  number or a list of numbers — the judge validates the shape against the
  actual output at scoring time, so the loader only checks the values;
- audit ``expected_severities`` keys must be a subset of ``expected_rules``
  and every declared severity must be one of "info"/"warning"/"error".

Sizing functions are imported from :mod:`pcbai.design.sizing` — the eval
harness never duplicates calculation logic, it only references the same
pure functions the MCP server and the agent use.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pcbai.design.sizing import (
    decoupling_capacitor,
    led_series_resistor,
    pull_up_resistor,
    voltage_divider,
)

__all__ = [
    "AuditCase",
    "GoldenDataError",
    "GoldenSet",
    "SIZING_FUNCTIONS",
    "SCALAR_SIZING_FUNCTIONS",
    "SizingCase",
    "VALID_SEVERITIES",
    "load_audit_cases",
    "load_golden",
    "load_sizing_cases",
]

#: Sizing function name -> callable (the only place the golden dataset knows
#: the evaluation subjects; logic stays in ``pcbai.design.sizing``).
SIZING_FUNCTIONS: dict[str, Callable[..., float | tuple[float, float]]] = {
    "led_series_resistor": led_series_resistor,
    "pull_up_resistor": pull_up_resistor,
    "voltage_divider": voltage_divider,
    "decoupling_capacitor": decoupling_capacitor,
}

#: Sizing functions whose result is a pair (e.g. ``(R, P)`` or ``(V_out, ratio)``).
PAIR_SIZING_FUNCTIONS: frozenset[str] = frozenset({"led_series_resistor", "voltage_divider"})

#: Sizing functions whose result is a single scalar (ohms or farads).
SCALAR_SIZING_FUNCTIONS: frozenset[str] = frozenset({"pull_up_resistor", "decoupling_capacitor"})

#: Default relative tolerance for sizing comparisons (expected is stored
#: already rounded to the E12 series; 1e-6 only absorbs float noise).
DEFAULT_SIZING_TOLERANCE: float = 1e-6

#: Valid audit finding severities in the golden dataset (mirrors
#: ``pcbai.design.audit``): everything else is a data error.
VALID_SEVERITIES: frozenset[str] = frozenset({"info", "warning", "error"})


class GoldenDataError(ValueError):
    """Raised when a golden JSONL dataset is malformed or fails validation."""


@dataclass(frozen=True)
class SizingCase:
    """One golden dimensional-calculation case.

    Attributes:
        id: Unique case id within the sizing dataset.
        description: Human-readable statement of the design question.
        function: Name of the ``pcbai.design.sizing`` function under test
            (validated against :data:`SIZING_FUNCTIONS`).
        inputs: Keyword arguments for that function (floats).
        expected: The hand-computed expected result, *already rounded to
            the E12 series* for resistors/capacitors: a single number for
            scalar-returning functions, a list for pair-returning ones
            (e.g. ``[R, P]``). The loader accepts both shapes for every
            function; the judge checks the shape against the actual output
            at scoring time.
        why: Formula walk-through documenting how ``expected`` was derived.
        tolerance: Relative tolerance for the value comparison (default
            1e-6; the dataset stores exact E12-rounded values).
    """

    id: str
    description: str
    function: str
    inputs: dict[str, float]
    expected: float | list[float]
    why: str = ""
    tolerance: float = DEFAULT_SIZING_TOLERANCE


@dataclass(frozen=True)
class AuditCase:
    """One golden design-audit case.

    Attributes:
        id: Unique case id within the audit dataset.
        fixture: Repository-root-relative path to the KiCad design file
            the audit runs over (e.g. ``"tests/fixtures/bad-led.kicad_net"``).
        description: Human-readable statement of the design intent (used
            by the LLM judge to frame the case; may be empty).
        expected_rules: Audit rule ids that MUST fire (recall is computed
            against this list). Empty for designs expected to be clean.
        expected_severities: Optional ``{rule: severity}`` contract — a
            declared severity that does not match fails the case.
        allow_extra_rules: When ``True``, detected rules beyond
            ``expected_rules`` are tolerated. Default ``False`` (strict):
            any extra rule fails the case, so regressions that start firing
            new findings are caught.
        why: Documentation of the design intent and expected behaviour.
    """

    id: str
    fixture: str
    description: str = ""
    expected_rules: list[str] = field(default_factory=list)
    expected_severities: dict[str, str] = field(default_factory=dict)
    allow_extra_rules: bool = False
    why: str = ""


@dataclass(frozen=True)
class GoldenSet:
    """A fully loaded golden dataset (sizing + audit cases)."""

    sizing: tuple[SizingCase, ...]
    audit: tuple[AuditCase, ...]

    @property
    def total(self) -> int:
        """Number of golden cases in the set."""
        return len(self.sizing) + len(self.audit)


def load_golden(golden_dir: str | Path) -> GoldenSet:
    """Load both datasets from ``golden_dir`` and return a :class:`GoldenSet`.

    Expects ``sizing_cases.jsonl`` and ``audit_cases.jsonl`` inside
    ``golden_dir``. Raises :class:`GoldenDataError` on any malformed or
    invalid content.
    """
    directory = Path(golden_dir)
    sizing = load_sizing_cases(directory / "sizing_cases.jsonl")
    audit = load_audit_cases(directory / "audit_cases.jsonl")
    return GoldenSet(sizing=tuple(sizing), audit=tuple(audit))


def load_sizing_cases(path: str | Path) -> list[SizingCase]:
    """Load and validate the sizing golden dataset from a JSONL file."""
    records = _read_jsonl(path)
    cases: list[SizingCase] = []
    seen: set[str] = set()
    for record in records:
        case_id = _case_id(record, path)
        if case_id in seen:
            raise GoldenDataError(
                f"{path.name}: duplicate case_id {case_id!r} in the sizing dataset"
            )
        seen.add(case_id)

        function = _required_str(record, "function", path)
        if function not in SIZING_FUNCTIONS:
            raise GoldenDataError(
                f"{path.name}:{case_id}: unknown sizing function {function!r} "
                f"(known: {', '.join(sorted(SIZING_FUNCTIONS))})"
            )
        inputs = _float_map(record.get("inputs"), path, case_id, "inputs")
        expected = _expected_value(record.get("expected"), path, case_id, function)
        cases.append(
            SizingCase(
                id=case_id,
                description=_required_str(record, "description", path, case_id),
                function=function,
                inputs=inputs,
                expected=expected,
                why=_optional_str(record, "why"),
                tolerance=_optional_positive_float(
                    record.get("tolerance"), DEFAULT_SIZING_TOLERANCE
                ),
            )
        )
    return cases


def load_audit_cases(path: str | Path) -> list[AuditCase]:
    """Load and validate the audit golden dataset from a JSONL file."""
    records = _read_jsonl(path)
    cases: list[AuditCase] = []
    seen: set[str] = set()
    for record in records:
        case_id = _case_id(record, path)
        if case_id in seen:
            raise GoldenDataError(
                f"{path.name}: duplicate case_id {case_id!r} in the audit dataset"
            )
        seen.add(case_id)

        fixture = _required_str(record, "fixture", path, case_id)
        rules = _str_list(record.get("expected_rules"), path, case_id, "expected_rules")
        severities = _str_map(
            record.get("expected_severities"), path, case_id, "expected_severities"
        )
        unknown = sorted(set(severities) - set(rules))
        if unknown:
            raise GoldenDataError(
                f"{path.name}:{case_id}: expected_severities refers to rules not listed "
                f"in expected_rules: {unknown}"
            )
        bad_severities = sorted({s for s in severities.values() if s not in VALID_SEVERITIES})
        if bad_severities:
            raise GoldenDataError(
                f"{path.name}:{case_id}: unknown severity "
                f"{', '.join(repr(s) for s in bad_severities)} "
                f"(valid: {', '.join(sorted(VALID_SEVERITIES))})"
            )
        allow_extra = record.get("allow_extra_rules", False)
        if not isinstance(allow_extra, bool):
            raise GoldenDataError(f"{path.name}:{case_id}: allow_extra_rules must be a bool")
        cases.append(
            AuditCase(
                id=case_id,
                fixture=fixture,
                description=_optional_str(record, "description"),
                expected_rules=rules,
                expected_severities=severities,
                allow_extra_rules=allow_extra,
                why=_optional_str(record, "why"),
            )
        )
    return cases


# ---------------------------------------------------------------------------
# Low-level JSONL / field validation helpers
# ---------------------------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read ``path`` line by line into JSON objects (blank lines skipped)."""
    if not path.is_file():
        raise GoldenDataError(f"golden dataset file not found: {path}")
    records: list[dict[str, Any]] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise GoldenDataError(f"{path.name}:{lineno}: invalid JSON: {exc}") from exc
        if not isinstance(obj, dict):
            raise GoldenDataError(f"{path.name}:{lineno}: expected a JSON object per line")
        records.append(obj)
    if not records:
        raise GoldenDataError(f"{path.name}: no cases found")
    return records


def _case_id(record: dict[str, Any], path: Path) -> str:
    """Read and validate the ``case_id`` field of a golden record.

    ``id`` is accepted as a fallback for hand-written datasets that still
    use the legacy key; ``case_id`` is the documented schema.
    """
    value = record.get("case_id", record.get("id"))
    if not isinstance(value, str) or not value.strip():
        raise GoldenDataError(f"{path.name}: field 'case_id' must be a non-empty string")
    return value.strip()


def _required_str(record: dict[str, Any], key: str, path: Path, case_id: str | None = None) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        where = f"{path.name}:{case_id}" if case_id else path.name
        raise GoldenDataError(f"{where}: field {key!r} must be a non-empty string")
    return value.strip()


def _optional_str(record: dict[str, Any], key: str) -> str:
    value = record.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise GoldenDataError(f"field {key!r} must be a string, got {value!r}")
    return value.strip()


def _float_map(value: Any, path: Path, case_id: str, key: str) -> dict[str, float]:
    if not isinstance(value, dict):
        raise GoldenDataError(f"{path.name}:{case_id}: {key} must be a JSON object")
    converted: dict[str, float] = {}
    for name, number in value.items():
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise GoldenDataError(f"{path.name}:{case_id}: {key}.{name} must be a number")
        converted[str(name)] = float(number)
    if not converted:
        raise GoldenDataError(f"{path.name}:{case_id}: {key} must not be empty")
    return converted


def _str_list(value: Any, path: Path, case_id: str, key: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise GoldenDataError(f"{path.name}:{case_id}: {key} must be a list of non-empty strings")
    return [str(item).strip() for item in value]


def _str_map(value: Any, path: Path, case_id: str, key: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise GoldenDataError(f"{path.name}:{case_id}: {key} must be a JSON object")
    converted: dict[str, str] = {}
    for rule, severity in value.items():
        if not isinstance(rule, str) or not isinstance(severity, str) or not severity.strip():
            raise GoldenDataError(f"{path.name}:{case_id}: {key} entries must map rule -> severity")
        converted[rule] = severity.strip()
    return converted


def _optional_positive_float(value: Any, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GoldenDataError(f"tolerance must be a number, got {value!r}")
    number = float(value)
    if number <= 0:
        raise GoldenDataError(f"tolerance must be positive, got {number!r}")
    return number


def _expected_value(value: Any, path: Path, case_id: str, function: str) -> float | list[float]:
    """Validate the expected result: a number or a non-empty list of numbers.

    Shape is *not* pinned to the function's return type here (a
    pair-returning function may legally store a scalar expectation in a
    hand-built case); the judge compares shapes against the actual output
    at scoring time and fails with a clear message on a mismatch.
    """
    if isinstance(value, list):
        if not value:
            raise GoldenDataError(f"{path.name}:{case_id}: expected must not be an empty list")
        numbers: list[float] = []
        for index, item in enumerate(value):
            number = _scalar(item, path, case_id, f"expected[{index}]")
            if number is None:
                raise GoldenDataError(
                    f"{path.name}:{case_id}: expected entries must be numbers (got {value!r})"
                )
            numbers.append(number)
        return numbers
    scalar = _scalar(value, path, case_id, "expected")
    if scalar is None:
        raise GoldenDataError(
            f"{path.name}:{case_id}: expected for {function} must be a number "
            f"or a list of numbers (got {value!r})"
        )
    return scalar


def _scalar(value: Any, path: Path, case_id: str, label: str) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
