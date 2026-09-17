"""Tests for the deterministic anti-hallucination grounding.

Pure unit tests — no network, no LLM. ``ToolCall`` steps are built by hand
with the real read-layer result shapes so the checks run against authentic
evidence (`{r_ohms, p_watts}`, component lists, audit findings).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pcbai.agent.grounding import (
    REF_DENYLIST,
    GroundingReport,
    _rc_p_unit_multiplier,
    _value_claims,
    validate_answer,
)
from pcbai.agent.react_agent import ToolCall
from pcbai.kicad.netlist import Design, Net, parse_netlist

FIXTURES = Path(__file__).parent / "fixtures"
SIMPLE_LED = FIXTURES / "simple-led.kicad_net"

SIZING_RESULT = {"r_ohms": 150, "p_watts": 0.06}


def _step(name: str, result: object, arguments: dict[str, object] | None = None) -> ToolCall:
    return ToolCall(name=name, arguments=arguments or {}, result=result)


def _assert_unsupported(report: GroundingReport, check: str, fragment: str) -> None:
    assert report.ok is False
    assert any(check in entry and fragment in entry for entry in report.unsupported)


def _single_claim(text: str) -> tuple[str, float]:
    """The one r/c/p claim ``text`` must produce (fails loudly otherwise)."""
    claims = _value_claims(text)
    assert len(claims) == 1, claims
    return claims[0]


# --------------------------------------------------------------------------- #
# REF_IN_ANSWER
# --------------------------------------------------------------------------- #


class TestRefInAnswer:
    def test_real_ref_from_design_passes(self) -> None:
        design = parse_netlist(SIMPLE_LED)
        report = validate_answer(
            "Pin 1 of J1 connects to R1 through the 5V net.",
            steps=[_step("load_design", {"components": 3, "nets": 3})],
            design=design,
        )
        assert report.ok is True

    def test_invented_ref_is_unsupported(self) -> None:
        design = parse_netlist(SIMPLE_LED)
        report = validate_answer(
            "The missing U99 needs a bypass capacitor.",
            steps=[_step("load_design", {"components": 3})],
            design=design,
        )
        _assert_unsupported(report, "REF_IN_ANSWER", "U99")
        assert report.ok is False

    def test_ref_grounded_through_tool_result_without_design(self) -> None:
        report = validate_answer(
            "R1 is a 330 Ω resistor.",
            steps=[
                _step(
                    "list_components",
                    {"components": [{"ref": "R1", "value": "330", "footprint": None}]},
                )
            ],
            design=None,
        )
        assert report.ok is True

    def test_ref_in_audit_evidence_counts_as_known(self) -> None:
        report = validate_answer(
            "LED1 has no series limiter.",
            steps=[
                _step(
                    "audit_design",
                    {
                        "findings": [
                            {
                                "rule": "LED_NO_LIMITER",
                                "severity": "warning",
                                "evidence": ["LED1", "net:LED_A"],
                            }
                        ],
                        "summary": {"total": 1},
                    },
                )
            ],
            design=None,
        )
        assert report.ok is True

    def test_net_name_ref_is_known_from_design(self) -> None:
        design = Design(components={}, nets={"NET1": Net(name="NET1")})
        report = validate_answer(
            "NET1 is floating.",
            steps=[_step("size_resistor", SIZING_RESULT)],
            design=design,
        )
        assert report.ok is True

    def test_peripheral_names_are_not_refs(self) -> None:
        assert "UART1" in REF_DENYLIST
        assert "I2C2" in REF_DENYLIST
        report = validate_answer(
            "UART1 uses a 150 Ω resistor and the E12 series.",
            steps=[_step("size_resistor", SIZING_RESULT)],
            design=None,
        )
        assert report.ok is True  # neither UART1 nor E12 is treated as a component ref

    def test_i2c_substring_not_matched(self) -> None:
        report = validate_answer(
            "The I2C bus needs a 150 Ω pull-up.",
            steps=[_step("size_resistor", SIZING_RESULT)],
            design=None,
        )
        assert report.ok is True


# --------------------------------------------------------------------------- #
# NUMBERS_FROM_SIZING
# --------------------------------------------------------------------------- #


class TestNumbersFromSizing:
    def test_number_not_backed_by_sizing_is_unsupported(self) -> None:
        report = validate_answer(
            "Use a 180 Ω series resistor.",
            steps=[_step("size_resistor", SIZING_RESULT)],
        )
        _assert_unsupported(report, "NUMBERS_FROM_SIZING", "180")
        assert report.ok is False

    def test_backed_number_passes(self) -> None:
        report = validate_answer(
            "Use a 150 Ω series resistor (0.06 W).",
            steps=[_step("size_resistor", SIZING_RESULT)],
        )
        assert report.ok is True

    def test_rounding_tolerance(self) -> None:
        report = validate_answer(
            "Use a 152 Ω series resistor.",
            steps=[_step("size_resistor", SIZING_RESULT)],
        )
        assert report.ok is True  # 152 is within 5% of the 150 result

    def test_decimal_format_normalised(self) -> None:
        report = validate_answer(
            "Use 150.0 Ω, dissipating 0.06 W.",
            steps=[_step("size_resistor", SIZING_RESULT)],
        )
        assert report.ok is True

    def test_kilo_prefix_normalised_and_wrong_value_flagged(self) -> None:
        report = validate_answer(
            "Use a 2.2 kΩ resistor here.",
            steps=[_step("size_resistor", SIZING_RESULT)],
        )
        _assert_unsupported(report, "NUMBERS_FROM_SIZING", "2.2")
        assert report.ok is False

    def test_bare_number_with_vocabulary_is_a_claim(self) -> None:
        report = validate_answer(
            "The resistor should be 150.",
            steps=[_step("size_resistor", SIZING_RESULT)],
        )
        assert report.ok is True

    def test_voltage_is_not_an_r_c_p_claim(self) -> None:
        report = validate_answer(
            "The supply is 5 V and the series resistor is 150 Ω.",
            steps=[_step("size_resistor", SIZING_RESULT)],
        )
        assert report.ok is True  # 5 V is contextual, 150 Ω is backed

    def test_sizing_not_called_skips_the_check(self) -> None:
        report = validate_answer(
            "R1 is a 330 Ω resistor.",
            steps=[_step("list_components", {"components": [{"ref": "R1", "value": "330"}]})],
        )
        assert report.ok is True  # v1: 330 is not verified without size_resistor


# --------------------------------------------------------------------------- #
# NUMBERS_FROM_SIZING — case-insensitive alphabetic units
# --------------------------------------------------------------------------- #


class TestUnitCaseInsensitive:
    """Alphabetic unit names are case-insensitive (``150 Ohms``), while SI
    symbols keep their case (``mΩ`` ≠ ``MΩ``, ``mW`` ≠ ``MW``)."""

    def test_capitalised_ohm_passes_grounding(self) -> None:
        report = validate_answer(
            "The series resistor for the LED requires 150 Ohms to limit the current to 20 mA.",
            steps=[_step("size_resistor", SIZING_RESULT)],
        )
        assert report.ok is True
        assert any("NUMBERS_FROM_SIZING: 1 numeric claim(s)" in check for check in report.checks)

    def test_capitalised_ohm_wrong_value_is_unsupported(self) -> None:
        report = validate_answer(
            "The series resistor for the LED requires 200 Ohms to limit the current to 20 mA.",
            steps=[_step("size_resistor", SIZING_RESULT)],
        )
        _assert_unsupported(report, "NUMBERS_FROM_SIZING", "200")
        assert report.ok is False

    def test_ohm_case_variants_are_detected(self) -> None:
        assert _single_claim("Use 150 ohm") == ("150 ohm", pytest.approx(150.0))
        assert _single_claim("Use 150 Ohms") == ("150 Ohms", pytest.approx(150.0))
        assert _single_claim("Use 4.7 kOhms") == ("4.7 kOhms", pytest.approx(4700.0))

    def test_milli_vs_mega_ohm_keep_distinct_multipliers(self) -> None:
        assert _single_claim("shunt 50 mΩ") == ("50 mΩ", pytest.approx(0.05))
        assert _single_claim("shunt 50 MΩ") == ("50 MΩ", pytest.approx(5e7))
        assert _single_claim("shunt 50 mOhms") == ("50 mOhms", pytest.approx(0.05))
        assert _single_claim("shunt 50 MOhms") == ("50 MOhms", pytest.approx(5e7))

    def test_milli_vs_mega_watt_keep_distinct_multipliers(self) -> None:
        assert _single_claim("idle 30 mW") == ("30 mW", pytest.approx(0.03))
        assert _single_claim("idle 30 MW") == ("30 MW", pytest.approx(3e7))

    def test_alphabetic_watt_and_farad_case_variants(self) -> None:
        assert _single_claim("dissipates 0.06 Watts") == (
            "0.06 Watts",
            pytest.approx(0.06),
        )
        assert _single_claim("needs a 1000 Farad capacitor") == (
            "1000 Farad",
            pytest.approx(1000.0),
        )

    def test_unit_multiplier_normalisation_keys(self) -> None:
        assert _rc_p_unit_multiplier("Ohms") == pytest.approx(1.0)
        assert _rc_p_unit_multiplier("kOHMS") == pytest.approx(1e3)
        assert _rc_p_unit_multiplier("mOhms") == pytest.approx(1e-3)
        assert _rc_p_unit_multiplier("MOhms") == pytest.approx(1e6)
        assert _rc_p_unit_multiplier("WATTS") == pytest.approx(1.0)
        assert _rc_p_unit_multiplier("Farads") == pytest.approx(1.0)
        assert _rc_p_unit_multiplier("mW") == pytest.approx(1e-3)
        assert _rc_p_unit_multiplier("MW") == pytest.approx(1e6)
        assert _rc_p_unit_multiplier(None) == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# NO_DATA_NO_CLAIM
# --------------------------------------------------------------------------- #


class TestNoDataNoClaim:
    def test_no_tools_with_numbers_is_unsupported(self) -> None:
        report = validate_answer("There are 3 nets.", steps=[])
        _assert_unsupported(report, "NO_DATA_NO_CLAIM", "numbers")
        assert report.ok is False

    def test_no_tools_with_refs_is_unsupported(self) -> None:
        design = parse_netlist(SIMPLE_LED)
        report = validate_answer("R1 is a 330 resistor.", steps=[], design=design)
        _assert_unsupported(report, "NO_DATA_NO_CLAIM", "references")
        assert report.ok is False

    def test_no_tools_clean_prose_passes(self) -> None:
        report = validate_answer("I cannot answer that without loading the design.", steps=[])
        assert report.ok is True

    def test_no_tools_empty_answer_passes(self) -> None:
        report = validate_answer("", steps=[])
        assert report.ok is True

    def test_numbers_from_the_question_are_not_grounded_without_tools(self) -> None:
        # The agent repeated a numeric claim but never called a tool: still
        # flagged even though the number came from the user's question.
        report = validate_answer("The LED current is 20 mA.", steps=[])
        assert report.ok is False


# --------------------------------------------------------------------------- #
# Robustness & transparency
# --------------------------------------------------------------------------- #


class TestRobustness:
    def test_url_and_port_tokens_are_ignored(self) -> None:
        design = parse_netlist(SIMPLE_LED)
        report = validate_answer(
            "The model host is http://ollama:11434; R1 needs a 150 Ω resistor.",
            steps=[_step("size_resistor", SIZING_RESULT)],
            design=design,
        )
        assert report.ok is True  # 11434 (port) and the URL are not claims

    def test_checks_list_is_transparent(self) -> None:
        report = validate_answer(
            "Use a 150 Ω series resistor.",
            steps=[_step("size_resistor", SIZING_RESULT)],
        )
        assert len(report.checks) == 3
        assert any(check.startswith("REF_IN_ANSWER") for check in report.checks)
        assert any(check.startswith("NUMBERS_FROM_SIZING") for check in report.checks)
        assert any(check.startswith("NO_DATA_NO_CLAIM") for check in report.checks)
        assert isinstance(report, GroundingReport)

    def test_report_is_repr_able(self) -> None:
        report = validate_answer(
            "Use a 180 Ω resistor.", steps=[_step("size_resistor", SIZING_RESULT)]
        )
        assert "UNSUPPORTED" in repr(report)

    def test_steps_missing_attributes_raise(self) -> None:
        with pytest.raises(AttributeError):
            validate_answer("hello", steps=[object()])  # no name/arguments/result
