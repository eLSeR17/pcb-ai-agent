"""Audit rule tests: the healthy ``simple-led`` fixture must stay clean and
the deliberately broken ``bad-led`` fixture must trigger the expected rules
with grounded evidence."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from pcbai.design.audit import audit_design
from pcbai.kicad.netlist import parse_netlist

FIXTURES = Path(__file__).parent / "fixtures"
SIMPLE_LED = FIXTURES / "simple-led.kicad_net"
BAD_LED = FIXTURES / "bad-led.kicad_net"

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
