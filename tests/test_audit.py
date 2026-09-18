"""Audit rule tests: the healthy ``simple-led`` fixture must stay clean and
the deliberately broken ``bad-led`` fixture must trigger the expected rules
with grounded evidence.

v2 rule tests (deterministic, netlist-only):

- ``E_SERIES_COMPLIANCE``: resistor/capacitor values must be preferred
  E6/E12/E24/E96 numbers (``non-e-series`` fixture: ``333`` and ``333pF``
  flagged, standard values silent; ``e96-values`` fixture: exact E96
  members accepted).
- ``LED_SERIES_RESISTOR``: an LED series resistor that is 0 ohm (short) or
  below ~22 ohm is flagged (``led-weak-limiter`` fixture); a proper 330 ohm
  limiter stays silent and ``LED_NO_LIMITER`` keeps owning the
  no-resistor case.
- ``CAP_DERATING``: an electrolytic capacitor with an explicit voltage
  rating on a numeric power rail must respect the 1.5x derating guideline
  (``cap-undervoltage`` fixture); cases without enough data are silent.

v2-regression tests (triggered by a 1060-component board audit):

- ``unannotated-refs``: KiCad ``R?`` placeholders must be omitted, not
  crash the parser; the remaining design is a clean 0-finding board.
- ``namespaced-refs`` / ``namespaced-simple-led``: hierarchical refs
  (``motherboard/R18``, ``sheet1.LED2``) must behave like plain refs.
- ``no-connect``: nets named ``unconnected-(...)`` are excluded from
  ``FLOATING_NET``.
- ``single-pin-parts``: mechanical/power symbols (``H*``, ``MH*``,
  ``TP*``, ``FID*``, ``#PWR01``) are excluded from ``UNCONNECTED_PIN``.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from pcbai.design.audit import audit_design
from pcbai.kicad.netlist import parse_netlist

FIXTURES = Path(__file__).parent / "fixtures"
SIMPLE_LED = FIXTURES / "simple-led.kicad_net"
BAD_LED = FIXTURES / "bad-led.kicad_net"
MINIMAL_MCU = FIXTURES / "minimal-mcu.kicad_net"
NON_E_SERIES = FIXTURES / "non-e-series.kicad_net"
LED_WEAK_LIMITER = FIXTURES / "led-weak-limiter.kicad_net"
CAP_UNDERVOLTAGE = FIXTURES / "cap-undervoltage.kicad_net"
UNANNOTATED_REFS = FIXTURES / "unannotated-refs.kicad_net"
NAMESPACED_REFS = FIXTURES / "namespaced-refs.kicad_net"
NAMESPACED_SIMPLE_LED = FIXTURES / "namespaced-simple-led.kicad_net"
NO_CONNECT = FIXTURES / "no-connect.kicad_net"
SINGLE_PIN_PARTS = FIXTURES / "single-pin-parts.kicad_net"
E96_VALUES = FIXTURES / "e96-values.kicad_net"

SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2}


def rules(report) -> set[str]:
    return {finding.rule for finding in report.findings}


class TestHealthyDesign:
    """simple-led: 3 nets, all with 2 connections; all parts populated."""

    def test_no_floating_nets(self) -> None:
        report = audit_design(parse_netlist(SIMPLE_LED))
        assert "FLOATING_NET" not in rules(report)

    def test_no_errors_or_warnings(self) -> None:
        report = audit_design(parse_netlist(SIMPLE_LED))
        assert report.summary["errors"] == 0
        assert report.summary["warnings"] == 0

    def test_single_advisory_driver_note(self) -> None:
        # LED_A is a passive-only net (R1 + LED1) with no U/J/Q and no power
        # name: exactly one info-level NO_DRIVER advisory.
        report = audit_design(parse_netlist(SIMPLE_LED))
        assert [finding.rule for finding in report.findings] == ["NO_DRIVER"]
        assert report.summary["infos"] == 1
        assert report.summary["total"] == 1

    def test_finding_is_grounded(self) -> None:
        finding = audit_design(parse_netlist(SIMPLE_LED)).findings[0]
        assert finding.evidence[0] == "LED_A"
        assert finding.evidence[1:] == ["LED1", "R1"]
        assert finding.position == "netlist"

    def test_led_with_series_resistor_is_not_flagged(self) -> None:
        # The star rule must be silent when a series resistor is present
        # (R1 sits on the same net as LED1 in simple-led).
        report = audit_design(parse_netlist(SIMPLE_LED))
        assert not any(finding.rule == "LED_NO_LIMITER" for finding in report.findings)


class TestBrokenDesign:
    """bad-led: LED straight to 5V/GND, a valu-less R2, a dangling net NC."""

    def test_led_no_limiter_detected(self) -> None:
        report = audit_design(parse_netlist(BAD_LED))
        hits = [f for f in report.findings if f.rule == "LED_NO_LIMITER"]
        assert len(hits) == 1
        assert hits[0].severity == "warning"
        assert hits[0].evidence[0] == "LED1"
        assert set(hits[0].evidence[1:]) == {"5V", "GND"}

    def test_floating_net_detected(self) -> None:
        report = audit_design(parse_netlist(BAD_LED))
        hits = [f for f in report.findings if f.rule == "FLOATING_NET"]
        assert len(hits) == 1
        assert hits[0].evidence[0] == "NC"
        assert "R2" in hits[0].evidence

    def test_missing_value_detected(self) -> None:
        report = audit_design(parse_netlist(BAD_LED))
        hits = [f for f in report.findings if f.rule == "MISSING_VALUE"]
        assert len(hits) == 1
        assert hits[0].evidence == ["R2"]

    def test_unconnected_pin_count_heuristic(self) -> None:
        # R2 is wired through a single pin: the other terminal is open.
        report = audit_design(parse_netlist(BAD_LED))
        hits = [f for f in report.findings if f.rule == "UNCONNECTED_PIN"]
        assert len(hits) == 1
        assert hits[0].evidence[0] == "R2"

    def test_findings_sorted_by_severity_then_evidence(self) -> None:
        report = audit_design(parse_netlist(BAD_LED))
        expected = [
            "LED_NO_LIMITER",
            "FLOATING_NET",
            "MISSING_VALUE",
            "UNCONNECTED_PIN",
            "NO_DRIVER",
        ]
        assert [finding.rule for finding in report.findings] == expected
        ranks = [SEVERITY_RANK[finding.severity] for finding in report.findings]
        assert ranks == sorted(ranks)

    def test_summary_counts_and_rules(self) -> None:
        report = audit_design(parse_netlist(BAD_LED))
        assert report.summary == {
            "source": "netlist",
            "total": 5,
            "errors": 0,
            "warnings": 4,
            "infos": 1,
            "rules": [
                "FLOATING_NET",
                "LED_NO_LIMITER",
                "MISSING_VALUE",
                "NO_DRIVER",
                "UNCONNECTED_PIN",
            ],
        }


class TestRuleEdgeCases:
    def test_missing_value_heuristic_skips_connectors_and_power_symbols(self) -> None:
        text = (
            '(export (version "E")'
            "  (components"
            '    (comp (ref "J1") (value "Conn_01x02"))'
            '    (comp (ref "U1") (value "74HC00"))'
            '    (comp (ref "#PWR01"))'
            '    (comp (ref "R9"))'
            "  )"
            '  (nets (net (code 0) (name "VCC") (node (ref "#PWR01") (pin "1"))))'
            ")"
        )
        report = audit_design(parse_netlist(text))
        hits = [f for f in report.findings if f.rule == "MISSING_VALUE"]
        assert [finding.evidence[0] for finding in hits] == ["R9"]

    def test_missing_footprint_is_fabrication_blocking(self) -> None:
        design = parse_netlist(SIMPLE_LED)
        design.components["R1"] = replace(design.components["R1"], footprint=None)
        report = audit_design(design)
        hits = [f for f in report.findings if f.rule == "MISSING_FOOTPRINT"]
        assert len(hits) == 1
        assert hits[0].severity == "error"
        assert hits[0].evidence == ["R1"]

    def test_power_named_net_silences_no_driver(self) -> None:
        report = audit_design(parse_netlist(SIMPLE_LED))
        assert not any(
            finding.evidence[0] == "GND" and finding.rule == "NO_DRIVER"
            for finding in report.findings
        )


class TestESeriesCompliance:
    """non-e-series: R1=333 and C1=333pF are not preferred numbers; every
    other R/C value is a standard E12/E24 value and must stay silent."""

    def test_non_preferred_values_flagged(self) -> None:
        report = audit_design(parse_netlist(NON_E_SERIES))
        hits = [finding for finding in report.findings if finding.rule == "E_SERIES_COMPLIANCE"]
        assert len(hits) == 2
        assert {finding.evidence[0] for finding in hits} == {"R1=333", "C1=333pF"}
        assert all(finding.severity == "warning" for finding in hits)

    def test_standard_values_not_flagged(self) -> None:
        report = audit_design(parse_netlist(NON_E_SERIES))
        hits = [finding for finding in report.findings if finding.rule == "E_SERIES_COMPLIANCE"]
        assert not any("R2=" in f.evidence[0] for f in hits)  # 10k
        assert not any("R3=" in f.evidence[0] for f in hits)  # 4.7k
        assert not any("C2=" in f.evidence[0] for f in hits)  # 100nF
        assert not any("C3=" in f.evidence[0] for f in hits)  # 47uF
        assert not any("R1" in f.message and "333" not in f.message for f in hits)

    def test_message_names_expected_series(self) -> None:
        report = audit_design(parse_netlist(NON_E_SERIES))
        hits = [finding for finding in report.findings if finding.rule == "E_SERIES_COMPLIANCE"]
        assert all("E12" in finding.message and "E24" in finding.message for finding in hits)
        assert any("333" in finding.message for finding in hits)

    def test_findings_deterministic(self) -> None:
        report = audit_design(parse_netlist(NON_E_SERIES))
        assert [finding.evidence[0] for finding in report.findings] == ["C1=333pF", "R1=333"]
        assert report.summary == {
            "source": "netlist",
            "total": 2,
            "errors": 0,
            "warnings": 2,
            "infos": 0,
            "rules": ["E_SERIES_COMPLIANCE"],
        }

    def test_pure_series_membership(self) -> None:
        from pcbai.design.audit import _is_e_series

        assert _is_e_series(330)
        assert _is_e_series(1000)  # 1k
        assert _is_e_series(4700)  # 4.7k
        assert _is_e_series(100e-9)  # 100nF
        assert _is_e_series(47e-6)  # 47uF
        assert _is_e_series(1e-7)  # 0.1uF / 100nF
        assert _is_e_series(10)
        assert not _is_e_series(333)
        assert not _is_e_series(333e-12)  # 333pF
        assert not _is_e_series(0.0)
        assert not _is_e_series(-5)

    def test_silent_on_v1_fixtures(self) -> None:
        # simple-led R1=330 is E12; bad-led R2 has no value at all.
        assert "E_SERIES_COMPLIANCE" not in rules(audit_design(parse_netlist(SIMPLE_LED)))
        assert "E_SERIES_COMPLIANCE" not in rules(audit_design(parse_netlist(BAD_LED)))
        assert "E_SERIES_COMPLIANCE" not in rules(audit_design(parse_netlist(MINIMAL_MCU)))


class TestLedSeriesResistor:
    """led-weak-limiter: LED1 has a 10 ohm limiter (too low), LED2 has a
    0R limiter (a short); LED3 has a proper 330 ohm limiter (silent)."""

    def test_too_low_and_short_flagged(self) -> None:
        report = audit_design(parse_netlist(LED_WEAK_LIMITER))
        hits = [finding for finding in report.findings if finding.rule == "LED_SERIES_RESISTOR"]
        assert len(hits) == 2
        assert {finding.evidence[0] for finding in hits} == {"LED1", "LED2"}
        assert all(finding.severity == "warning" for finding in hits)

    def test_short_resistor_message(self) -> None:
        report = audit_design(parse_netlist(LED_WEAK_LIMITER))
        short = [
            f
            for f in report.findings
            if f.rule == "LED_SERIES_RESISTOR" and f.evidence[0] == "LED2"
        ]
        assert len(short) == 1
        assert short[0].evidence[1] == "R2=0R"
        assert "0" in short[0].message
        assert "short" in short[0].message.lower()

    def test_low_but_nonzero_message(self) -> None:
        report = audit_design(parse_netlist(LED_WEAK_LIMITER))
        low = [
            f
            for f in report.findings
            if f.rule == "LED_SERIES_RESISTOR" and f.evidence[0] == "LED1"
        ]
        assert len(low) == 1
        assert low[0].evidence[1] == "R1=10"
        assert "22" in low[0].message and "47" in low[0].message

    def test_adequate_resistor_not_flagged(self) -> None:
        report = audit_design(parse_netlist(LED_WEAK_LIMITER))
        assert not any(
            finding.evidence[0] == "LED3" and finding.rule == "LED_SERIES_RESISTOR"
            for finding in report.findings
        )

    def test_led_no_limiter_keeps_the_resistorless_case(self) -> None:
        report = audit_design(parse_netlist(LED_WEAK_LIMITER))
        assert "LED_NO_LIMITER" not in rules(report)  # every LED has a series R
        assert "LED_NO_LIMITER" in rules(audit_design(parse_netlist(BAD_LED)))
        assert "LED_SERIES_RESISTOR" not in rules(audit_design(parse_netlist(BAD_LED)))

    def test_series_resistor_compliance_untouched(self) -> None:
        # The 10 ohm and 0R limiters are E12/zero-resistance values: the
        # E-Series rule must not double-report them.
        report = audit_design(parse_netlist(LED_WEAK_LIMITER))
        assert "E_SERIES_COMPLIANCE" not in rules(report)

    def test_parse_resistance(self) -> None:
        from pcbai.design.audit import _parse_resistance

        assert _parse_resistance("330") == 330.0
        assert _parse_resistance("4.7k") == 4700.0
        assert _parse_resistance("1K") == 1000.0
        assert _parse_resistance("33R") == 33.0
        assert _parse_resistance("4R7") == 4.7
        assert _parse_resistance("0R") == 0.0
        assert _parse_resistance("0") == 0.0
        assert _parse_resistance("10M") == 10_000_000.0
        assert _parse_resistance("1Meg") == 1_000_000.0
        assert _parse_resistance("10k 1%") == 10_000.0
        assert _parse_resistance("LED") is None
        assert _parse_resistance("10uF") is None
        assert _parse_resistance("") is None
        assert _parse_resistance("330R0") is None  # exotic notation: unsupported

    def test_parse_capacitance(self) -> None:
        from pcbai.design.audit import _parse_capacitance

        assert _parse_capacitance("100nF") == pytest.approx(100e-9)
        assert _parse_capacitance("47uF") == pytest.approx(47e-6)
        assert _parse_capacitance("47µF") == pytest.approx(47e-6)
        assert _parse_capacitance("0.1uF") == pytest.approx(0.1e-6)
        assert _parse_capacitance("1u") == pytest.approx(1e-6)
        assert _parse_capacitance("22pF") == pytest.approx(22e-12)
        assert _parse_capacitance("333pF") == pytest.approx(333e-12)
        assert _parse_capacitance("1mF") == pytest.approx(1e-3)
        assert _parse_capacitance("10uF 50V") == pytest.approx(10e-6)
        assert _parse_capacitance("470uF/25V") == pytest.approx(470e-6)
        assert _parse_capacitance("330") is None  # bare EIA code: ambiguous
        assert _parse_capacitance("LED") is None

    def test_parse_voltage(self) -> None:
        from pcbai.design.audit import _parse_voltage

        assert _parse_voltage("10uF 50V") == 50.0
        assert _parse_voltage("470uF/25V") == 25.0
        assert _parse_voltage("100uF 16V") == 16.0
        assert _parse_voltage("6.3V") == 6.3
        assert _parse_voltage("50V 10uF") == 50.0
        assert _parse_voltage("100nF") is None
        assert _parse_voltage("10uF") is None
        assert _parse_voltage("") is None

    def test_rail_voltage(self) -> None:
        from pcbai.design.audit import _rail_voltage

        assert _rail_voltage("5V") == 5.0
        assert _rail_voltage("3V3") == 3.3
        assert _rail_voltage("3.3V") == 3.3
        assert _rail_voltage("12V") == 12.0
        assert _rail_voltage("5V0") == 5.0
        assert _rail_voltage("VCC") is None
        assert _rail_voltage("VDD") is None
        assert _rail_voltage("GND") is None
        assert _rail_voltage("LED_A") is None


class TestCapDerating:
    """cap-undervoltage: C1 (6.3V on 5V) and C4 (16V on 12V) violate the
    1.5x derating guideline; every other cap either respects it, is a
    ceramic, lacks a rating, or sits on a rail without a known voltage."""

    def test_undervoltage_electrolytics_flagged(self) -> None:
        report = audit_design(parse_netlist(CAP_UNDERVOLTAGE))
        hits = [finding for finding in report.findings if finding.rule == "CAP_DERATING"]
        assert len(hits) == 2
        assert {finding.evidence[0] for finding in hits} == {"C1", "C4"}
        assert all(finding.severity == "info" for finding in hits)
        assert all(finding.position == "netlist" for finding in hits)

    def test_adequate_rating_not_flagged(self) -> None:
        report = audit_design(parse_netlist(CAP_UNDERVOLTAGE))
        hits = [finding for finding in report.findings if finding.rule == "CAP_DERATING"]
        assert not any(f.evidence[0] in {"C2", "C3", "C8", "C9"} for f in hits)

    def test_ceramic_without_rating_not_flagged(self) -> None:
        report = audit_design(parse_netlist(CAP_UNDERVOLTAGE))
        assert not any(
            finding.evidence[0] == "C5" and finding.rule == "CAP_DERATING"
            for finding in report.findings
        )

    def test_missing_rating_skipped(self) -> None:
        # C7 ("10uF", no voltage in the value): the bonus control needs the
        # rating to fire, so it must stay silent.
        report = audit_design(parse_netlist(CAP_UNDERVOLTAGE))
        assert not any(
            finding.evidence[0] == "C7" and finding.rule == "CAP_DERATING"
            for finding in report.findings
        )

    def test_unknown_rail_voltage_skipped(self) -> None:
        # C6/C10 sit on VCC: the rail voltage is not knowable from the
        # netlist, so the 1.5x comparison cannot run.
        report = audit_design(parse_netlist(CAP_UNDERVOLTAGE))
        assert not any(
            finding.evidence[0] in {"C6", "C10"} and finding.rule == "CAP_DERATING"
            for finding in report.findings
        )

    def test_evidence_carries_value_and_rail(self) -> None:
        report = audit_design(parse_netlist(CAP_UNDERVOLTAGE))
        hits = {
            finding.evidence[0]: finding.evidence
            for finding in report.findings
            if finding.rule == "CAP_DERATING"
        }
        assert hits["C1"] == ["C1", "10uF 6.3V", "5V"]
        assert hits["C4"] == ["C4", "220uF 16V", "12V"]

    def test_message_contains_derating_factor(self) -> None:
        report = audit_design(parse_netlist(CAP_UNDERVOLTAGE))
        hits = [finding for finding in report.findings if finding.rule == "CAP_DERATING"]
        assert all("1.5" in finding.message for finding in hits)
        assert any("6.3" in finding.message for finding in hits)
        assert any("7.5" in finding.message for finding in hits)

    def test_is_electrolytic_helper(self) -> None:
        from pcbai.design.audit import _is_electrolytic

        assert _is_electrolytic("10uF 50V", "Capacitor_THT:CP_Radial_D5.0mm_P2.50mm")
        assert _is_electrolytic("10uF 50V", "Capacitor_SMD:C_0603_1608Metric")  # by value
        assert _is_electrolytic("100nF", "Capacitor_THT:CP_Radial_D5.0mm_P2.50mm")  # by footprint
        assert not _is_electrolytic("100nF", "Capacitor_SMD:C_0603_1608Metric")

    def test_silent_on_v1_fixtures(self) -> None:
        assert "CAP_DERATING" not in rules(audit_design(parse_netlist(SIMPLE_LED)))
        assert "CAP_DERATING" not in rules(audit_design(parse_netlist(MINIMAL_MCU)))
        assert "CAP_DERATING" not in rules(audit_design(parse_netlist(BAD_LED)))


def _pairs(report) -> list[tuple[str, list[str]]]:
    return [(finding.rule, finding.evidence) for finding in report.findings]


class TestUnannotatedReferences:
    """A board with Eeschema ``R?``/``U?`` placeholders audits clean."""

    def test_zero_findings(self) -> None:
        report = audit_design(parse_netlist(UNANNOTATED_REFS))
        assert report.findings == []
        assert report.summary["total"] == 0

    def test_omitted_placeholder_components(self) -> None:
        design = parse_netlist(UNANNOTATED_REFS)
        assert set(design.components) == {"R1", "R2", "C1", "U1"}


class TestNamespacedReferences:
    """Hierarchical refs must be resolved, never matched by raw prefix."""

    def test_exact_findings_on_namespaced_board(self) -> None:
        report = audit_design(parse_netlist(NAMESPACED_REFS))
        assert _pairs(report) == [
            ("E_SERIES_COMPLIANCE", ["motherboard/R18=333"]),
            ("LED_NO_LIMITER", ["sheet1.LED2", "LED2_A", "LED2_K"]),
            ("NO_DRIVER", ["LED2_A", "MB-C3", "sheet1.LED2"]),
            ("NO_DRIVER", ["LED2_K", "MB-C2", "sheet1.LED2"]),
            ("NO_DRIVER", ["LED_A", "MB-R3", "motherboard/LED1"]),
        ]

    def test_led_with_limiter_through_namespace_stays_silent(self) -> None:
        # motherboard/LED1 sits on LED_A together with MB-R3 (class R):
        # the limiter is found through the namespaced class, and only
        # sheet1.LED2 (no resistor) is flagged by LED_NO_LIMITER.
        report = audit_design(parse_netlist(NAMESPACED_REFS))
        leds = [f for f in report.findings if f.rule == "LED_NO_LIMITER"]
        assert leds[0].evidence[0] == "sheet1.LED2"

    def test_e_series_message_namespaced_and_e96(self) -> None:
        report = audit_design(parse_netlist(NAMESPACED_REFS))
        hit = next(f for f in report.findings if f.rule == "E_SERIES_COMPLIANCE")
        assert "motherboard/R18" in hit.message
        assert "E96" in hit.message

    def test_namespaced_simple_led_mirror(self) -> None:
        # Same topology as simple-led but with namespaced refs: the only
        # difference must be the NO_DRIVER evidence names.
        report = audit_design(parse_netlist(NAMESPACED_SIMPLE_LED))
        assert _pairs(report) == [("NO_DRIVER", ["LED_A", "motherboard/LED1", "motherboard/R1"])]


class TestRefHelpers:
    """_ref_kind/_ref_class: the namespaced-reference resolution units."""

    def test_ref_kind(self) -> None:
        from pcbai.design.audit import _ref_kind

        assert _ref_kind("R1") == "R"
        assert _ref_kind("R18") == "R"
        assert _ref_kind("LED1") == "LED"
        assert _ref_kind("motherboard/R18") == "R"
        assert _ref_kind("sheet1.U5") == "U"
        assert _ref_kind("MB-R12") == "R"
        assert _ref_kind("TP1") == "TP"
        assert _ref_kind("FID1") == "FID"
        assert _ref_kind("MH1") == "MH"
        assert _ref_kind("H1") == "H"
        assert _ref_kind("#PWR01") is None
        assert _ref_kind("1R") is None

    def test_ref_class(self) -> None:
        from pcbai.design.audit import _ref_class

        assert _ref_class("R1") == "R"
        assert _ref_class("motherboard/R18") == "R"
        assert _ref_class("LED1") == "L"
        assert _ref_class("sheet1.LED2") == "L"
        assert _ref_class("#PWR01") is None

    def test_ref_kind_case_sensitive(self) -> None:
        from pcbai.design.audit import _ref_kind

        assert _ref_kind("led1") == "led"  # no case folding


class TestNoConnectNets:
    """KiCad ``unconnected-(...)`` nets are deliberate non-connections."""

    def test_floating_excludes_unconnected_nets(self) -> None:
        report = audit_design(parse_netlist(NO_CONNECT))
        floating = [f.evidence for f in report.findings if f.rule == "FLOATING_NET"]
        assert floating == [["DANGLING", "R2"]]

    def test_unconnected_net_leaves_no_trace(self) -> None:
        report = audit_design(parse_netlist(NO_CONNECT))
        assert all("unconnected-(U1-Pad3)" not in f.evidence for f in report.findings)

    def test_exact_findings(self) -> None:
        report = audit_design(parse_netlist(NO_CONNECT))
        assert _pairs(report) == [
            ("FLOATING_NET", ["DANGLING", "R2"]),
            ("NO_DRIVER", ["DANGLING", "R2"]),
        ]


class TestSinglePinParts:
    """Mechanical/power symbols are legitimately single-connection."""

    def test_unconnected_pin_only_real_part(self) -> None:
        report = audit_design(parse_netlist(SINGLE_PIN_PARTS))
        unconnected = [f.evidence for f in report.findings if f.rule == "UNCONNECTED_PIN"]
        assert unconnected == [["R1", "1"]]

    def test_floating_still_flags_single_wire_net(self) -> None:
        report = audit_design(parse_netlist(SINGLE_PIN_PARTS))
        floating = {f.evidence[0] for f in report.findings if f.rule == "FLOATING_NET"}
        assert floating == {"H1_NET", "MH1_NET", "TP1_NET", "FID1_NET", "VCC", "R1_LOOSE"}

    def test_exact_findings(self) -> None:
        report = audit_design(parse_netlist(SINGLE_PIN_PARTS))
        assert _pairs(report) == [
            ("FLOATING_NET", ["FID1_NET", "FID1"]),
            ("FLOATING_NET", ["H1_NET", "H1"]),
            ("FLOATING_NET", ["MH1_NET", "MH1"]),
            ("UNCONNECTED_PIN", ["R1", "1"]),
            ("FLOATING_NET", ["R1_LOOSE", "R1"]),
            ("FLOATING_NET", ["TP1_NET", "TP1"]),
            ("FLOATING_NET", ["VCC", "#PWR01"]),
            ("NO_DRIVER", ["R1_LOOSE", "R1"]),
        ]


class TestESeriesE96:
    """E_SERIES_COMPLIANCE accepts exact E96 numbers, rejects near-misses."""

    def test_exact_e96_hits(self) -> None:
        report = audit_design(parse_netlist(E96_VALUES))
        assert _pairs(report) == [
            ("E_SERIES_COMPLIANCE", ["C3=333pF"]),
            ("E_SERIES_COMPLIANCE", ["R6=333"]),
        ]

    def test_e96_standard_values_silent(self) -> None:
        # 4.99k, 24.9k, 49.9k, 127k, 27.4, 10k, 1.15nF and 4.99uF are exact
        # E96 members (0.0 % off); 333/333pF are 0.30 % off 3.32 -> flagged.
        report = audit_design(parse_netlist(E96_VALUES))
        assert report.summary["warnings"] == 2

    def test_message_mentions_e96(self) -> None:
        report = audit_design(parse_netlist(E96_VALUES))
        assert all("E96" in f.message for f in report.findings)

    def test_is_e_series_e96_membership(self) -> None:
        from pcbai.design.audit import _is_e_series

        assert _is_e_series(4990.0)
        assert _is_e_series(24900.0)
        assert _is_e_series(49900.0)
        assert _is_e_series(127000.0)
        assert _is_e_series(27.4)
        assert _is_e_series(1.15e-9)
        assert _is_e_series(4.99e-6)
        assert not _is_e_series(333.0)
        assert not _is_e_series(333e-12)
        assert not _is_e_series(0.0)
        assert not _is_e_series(-10.0)
