"""Tests for the netlist <-> schematic cross-check.

The check must be pure and deterministic, reuse the audit ``Finding``
model, and never compare pin-level connectivity (unresolved on the
schematic side). Synthetic one-rule designs keep every case isolated; the
real ``simple-led`` fixture pair proves that a coherent design produces
zero findings.
"""

from __future__ import annotations

from pathlib import Path

from pcbai.design import ConsistencyReport, cross_check
from pcbai.design.consistency import CHECKS
from pcbai.kicad.netlist import Component, Design, Net, parse_netlist, parse_schematic

FIXTURES = Path(__file__).parent / "fixtures"
SIMPLE_LED_NET = FIXTURES / "simple-led.kicad_net"
SIMPLE_LED_SCH = FIXTURES / "simple-led.kicad_sch"


def _component(
    ref: str,
    value: str | None = "1k",
    footprint: str | None = "Resistor_SMD:R_0603_1608Metric",
    pins: dict[str, str | None] | None = None,
) -> Component:
    return Component(ref=ref, value=value, footprint=footprint, pins=dict(pins or {}))


def _design(*components: Component, nets: dict[str, Net] | None = None) -> Design:
    return Design(
        components={component.ref: component for component in components}, nets=nets or {}
    )


def _rules(findings: list) -> list[str]:
    return [finding.rule for finding in findings]


# --------------------------------------------------------------------------- #
# No schematic supplied
# --------------------------------------------------------------------------- #


class TestNoSchematic:
    def test_returns_a_consistency_report(self) -> None:
        report = cross_check(_design(_component("R1")), None)
        assert isinstance(report, ConsistencyReport)

    def test_reports_exactly_one_finding(self) -> None:
        report = cross_check(_design(_component("R1")), None)
        assert len(report.findings) == 1
        assert report.findings[0].rule == "NO_SCHEMATIC"

    def test_finding_contract(self) -> None:
        finding = cross_check(_design(_component("R1")), None).findings[0]
        assert finding.severity == "info"
        assert finding.evidence == ["schematic"]
        assert finding.position == "cross-check"
        assert "schematic" in finding.message

    def test_checks_run_is_only_no_schematic(self) -> None:
        report = cross_check(_design(_component("R1")), None)
        assert report.checks_run == ["NO_SCHEMATIC"]

    def test_does_not_pretend_drift(self) -> None:
        report = cross_check(_design(_component("R1")), None)
        assert set(_rules(report.findings)).isdisjoint(
            {"MISSING_IN_SCHEMATIC", "MISSING_IN_NETLIST", "VALUE_MISMATCH"}
        )


# --------------------------------------------------------------------------- #
# Coherent fixture pair
# --------------------------------------------------------------------------- #


class TestFixturePair:
    def test_coherent_pair_has_no_findings(self) -> None:
        report = cross_check(parse_netlist(SIMPLE_LED_NET), parse_schematic(SIMPLE_LED_SCH))
        assert report.findings == []

    def test_checks_run_lists_every_rule(self) -> None:
        report = cross_check(parse_netlist(SIMPLE_LED_NET), parse_schematic(SIMPLE_LED_SCH))
        assert report.checks_run == list(CHECKS)

    def test_values_and_footprints_match(self) -> None:
        netlist = parse_netlist(SIMPLE_LED_NET)
        schematic = parse_schematic(SIMPLE_LED_SCH)
        assert set(netlist.components) == set(schematic.components) == {"J1", "R1", "LED1"}
        for ref, component in netlist.components.items():
            assert component.value == schematic.components[ref].value
            assert component.footprint == schematic.components[ref].footprint

    def test_pins_are_not_compared(self) -> None:
        netlist = parse_netlist(SIMPLE_LED_NET)
        schematic = parse_schematic(SIMPLE_LED_SCH)
        # The netlist resolves pin nets; the schematic only knows pin numbers
        # (every net value is None), so a pin comparison would be meaningless.
        assert all(
            net is not None
            for component in netlist.components.values()
            for net in component.pins.values()
        )
        assert all(
            net is None
            for component in schematic.components.values()
            for net in component.pins.values()
        )
        assert cross_check(netlist, schematic).findings == []

    def test_deterministic_across_calls(self) -> None:
        netlist = parse_netlist(SIMPLE_LED_NET)
        schematic = parse_schematic(SIMPLE_LED_SCH)
        first = cross_check(netlist, schematic).findings
        second = cross_check(netlist, schematic).findings
        assert first == second


# --------------------------------------------------------------------------- #
# MISSING_IN_SCHEMATIC
# --------------------------------------------------------------------------- #


class TestMissingInSchematic:
    def test_netlist_only_component_is_reported(self) -> None:
        report = cross_check(_design(_component("R1")), _design())
        assert _rules(report.findings) == ["MISSING_IN_SCHEMATIC"]

    def test_severity_is_warning(self) -> None:
        finding = cross_check(_design(_component("R1")), _design()).findings[0]
        assert finding.severity == "warning"

    def test_evidence_names_the_netlist_side(self) -> None:
        finding = cross_check(_design(_component("R1")), _design()).findings[0]
        assert finding.evidence == ["R1", "netlist"]

    def test_message_mentions_schematic(self) -> None:
        finding = cross_check(_design(_component("R1")), _design()).findings[0]
        assert "missing from the schematic" in finding.message

    def test_value_is_included_when_present(self) -> None:
        finding = cross_check(_design(_component("R1", value="330")), _design()).findings[0]
        assert "330" in finding.message

    def test_multiple_missing_refs_are_sorted(self) -> None:
        report = cross_check(_design(_component("R2"), _component("R1")), _design())
        assert [finding.evidence[0] for finding in report.findings] == ["R1", "R2"]

    def test_mechanical_parts_are_not_reported(self) -> None:
        report = cross_check(
            _design(_component("H1"), _component("TP1"), _component("FID1")), _design()
        )
        assert report.findings == []


# --------------------------------------------------------------------------- #
# MISSING_IN_NETLIST
# --------------------------------------------------------------------------- #


class TestMissingInNetlist:
    def test_schematic_only_component_is_reported(self) -> None:
        report = cross_check(_design(), _design(_component("U1")))
        assert _rules(report.findings) == ["MISSING_IN_NETLIST"]

    def test_severity_is_error(self) -> None:
        finding = cross_check(_design(), _design(_component("U1"))).findings[0]
        assert finding.severity == "error"

    def test_evidence_names_the_schematic_side(self) -> None:
        finding = cross_check(_design(), _design(_component("U1"))).findings[0]
        assert finding.evidence == ["U1", "schematic"]

    def test_message_mentions_the_netlist(self) -> None:
        finding = cross_check(_design(), _design(_component("U1"))).findings[0]
        assert "missing from the netlist" in finding.message

    def test_power_symbols_are_ignored(self) -> None:
        report = cross_check(_design(), _design(_component("#PWR01", value=None, footprint=None)))
        assert report.findings == []

    def test_unannotated_placeholder_is_not_reported_here(self) -> None:
        report = cross_check(_design(), _design(_component("R?", value="1k")))
        assert _rules(report.findings) == ["UNANNOTATED_REF"]

    def test_multiple_missing_refs_are_sorted(self) -> None:
        report = cross_check(_design(), _design(_component("U2"), _component("U1")))
        assert [finding.evidence[0] for finding in report.findings] == ["U1", "U2"]


# --------------------------------------------------------------------------- #
# VALUE_MISMATCH
# --------------------------------------------------------------------------- #


class TestValueMismatch:
    def test_different_value_is_reported(self) -> None:
        report = cross_check(
            _design(_component("R1", value="330")), _design(_component("R1", value="470"))
        )
        assert _rules(report.findings) == ["VALUE_MISMATCH"]

    def test_identical_value_is_silent(self) -> None:
        report = cross_check(
            _design(_component("R1", value="330")), _design(_component("R1", value="330"))
        )
        assert report.findings == []

    def test_missing_value_vs_present_value_is_reported(self) -> None:
        report = cross_check(
            _design(_component("R1", value=None)), _design(_component("R1", value="330"))
        )
        assert _rules(report.findings) == ["VALUE_MISMATCH"]

    def test_both_missing_values_are_silent(self) -> None:
        netlist = _design(_component("R1", value=None))
        schematic = _design(_component("R1", value=None))
        assert cross_check(netlist, schematic).findings == []

    def test_message_shows_both_values(self) -> None:
        finding = cross_check(
            _design(_component("R1", value="330")), _design(_component("R1", value="470"))
        ).findings[0]
        assert "330" in finding.message and "470" in finding.message

    def test_namespaced_refs_are_compared(self) -> None:
        report = cross_check(
            _design(_component("sheet1.R1", value="330")),
            _design(_component("sheet1.R1", value="470")),
        )
        assert _rules(report.findings) == ["VALUE_MISMATCH"]


# --------------------------------------------------------------------------- #
# FOOTPRINT_MISMATCH
# --------------------------------------------------------------------------- #


class TestFootprintMismatch:
    def test_different_footprint_is_reported(self) -> None:
        report = cross_check(
            _design(_component("R1", footprint="R_0603")),
            _design(_component("R1", footprint="R_0805")),
        )
        assert _rules(report.findings) == ["FOOTPRINT_MISMATCH"]

    def test_identical_footprint_is_silent(self) -> None:
        report = cross_check(
            _design(_component("R1", footprint="R_0603")),
            _design(_component("R1", footprint="R_0603")),
        )
        assert report.findings == []

    def test_missing_footprint_vs_present_is_reported(self) -> None:
        report = cross_check(
            _design(_component("R1", footprint=None)),
            _design(_component("R1", footprint="R_0603")),
        )
        assert _rules(report.findings) == ["FOOTPRINT_MISMATCH"]

    def test_severity_is_info(self) -> None:
        finding = cross_check(
            _design(_component("R1", footprint="R_0603")),
            _design(_component("R1", footprint="R_0805")),
        ).findings[0]
        assert finding.severity == "info"

    def test_value_and_footprint_drift_both_reported(self) -> None:
        report = cross_check(
            _design(_component("R1", value="330", footprint="R_0603")),
            _design(_component("R1", value="470", footprint="R_0805")),
        )
        assert sorted(_rules(report.findings)) == ["FOOTPRINT_MISMATCH", "VALUE_MISMATCH"]


# --------------------------------------------------------------------------- #
# UNANNOTATED_REF
# --------------------------------------------------------------------------- #


class TestUnannotatedRef:
    def test_schematic_placeholder_is_reported(self) -> None:
        report = cross_check(_design(), _design(_component("R?", value="1k")))
        assert _rules(report.findings) == ["UNANNOTATED_REF"]

    def test_severity_is_warning(self) -> None:
        finding = cross_check(_design(), _design(_component("R?", value="1k"))).findings[0]
        assert finding.severity == "warning"

    def test_message_asks_to_annotate(self) -> None:
        finding = cross_check(_design(), _design(_component("R?", value="1k"))).findings[0]
        assert "annotate" in finding.message

    def test_absent_placeholder_produces_no_finding(self) -> None:
        assert cross_check(_design(_component("R1")), _design(_component("R1"))).findings == []

    def test_multiple_placeholders_are_each_reported(self) -> None:
        report = cross_check(_design(), _design(_component("R?"), _component("U?")))
        assert [finding.evidence[0] for finding in report.findings] == ["R?", "U?"]

    def test_netlist_placeholder_is_reported_defensively(self) -> None:
        report = cross_check(_design(_component("R?", value="1k")), _design())
        assert _rules(report.findings) == ["UNANNOTATED_REF"]


# --------------------------------------------------------------------------- #
# Ignored reference kinds
# --------------------------------------------------------------------------- #


class TestIgnoredRefs:
    def test_mounting_hole_is_ignored(self) -> None:
        assert cross_check(_design(_component("H1")), _design()).findings == []

    def test_mounting_hole_namespace_variant_is_ignored(self) -> None:
        assert cross_check(_design(_component("MH2")), _design()).findings == []

    def test_test_point_is_ignored(self) -> None:
        assert cross_check(_design(_component("TP3")), _design()).findings == []

    def test_fiducial_is_ignored(self) -> None:
        assert cross_check(_design(_component("FID1")), _design()).findings == []

    def test_namespaced_ignored_kind_is_ignored(self) -> None:
        assert cross_check(_design(), _design(_component("sheet1.TP9"))).findings == []

    def test_ignored_ref_is_not_compared_for_value(self) -> None:
        report = cross_check(
            _design(_component("H1", value="A")), _design(_component("H1", value="B"))
        )
        assert report.findings == []


# --------------------------------------------------------------------------- #
# Ordering, checks_run and source
# --------------------------------------------------------------------------- #


class TestOrderingAndChecks:
    def test_errors_sort_before_warnings(self) -> None:
        report = cross_check(
            _design(_component("R1")),
            _design(_component("U1")),
        )
        assert _rules(report.findings) == ["MISSING_IN_NETLIST", "MISSING_IN_SCHEMATIC"]

    def test_findings_are_sorted_by_evidence(self) -> None:
        report = cross_check(_design(), _design(_component("U2"), _component("U1")))
        assert [finding.evidence[0] for finding in report.findings] == ["U1", "U2"]

    def test_checks_run_is_the_documented_constant(self) -> None:
        report = cross_check(_design(_component("R1")), _design(_component("R1")))
        assert report.checks_run == list(CHECKS)
        assert CHECKS == (
            "MISSING_IN_SCHEMATIC",
            "MISSING_IN_NETLIST",
            "VALUE_MISMATCH",
            "FOOTPRINT_MISMATCH",
            "UNANNOTATED_REF",
        )

    def test_nets_are_not_compared(self) -> None:
        netlist = _design(_component("R1"), nets={"A": Net(name="A", connections=[("R1", "1")])})
        schematic = _design(_component("R1"), nets={"B": Net(name="B")})
        assert cross_check(netlist, schematic).findings == []

    def test_source_is_propagated_to_every_finding(self) -> None:
        report = cross_check(
            _design(_component("R1", value="330")),
            _design(_component("R1", value="470"), _component("U1")),
            source="drawing-check",
        )
        assert report.findings
        assert all(finding.position == "drawing-check" for finding in report.findings)

    def test_default_source(self) -> None:
        finding = cross_check(_design(), _design(_component("U1"))).findings[0]
        assert finding.position == "cross-check"


# --------------------------------------------------------------------------- #
# Purity
# --------------------------------------------------------------------------- #


class TestPurity:
    def test_does_not_mutate_the_inputs(self) -> None:
        netlist = _design(_component("R1", value="330"), _component("R2", value="1k"))
        schematic = _design(_component("R1", value="470"))
        netlist_snapshot = dict(netlist.components)
        schematic_snapshot = dict(schematic.components)
        cross_check(netlist, schematic)
        assert netlist.components == netlist_snapshot
        assert schematic.components == schematic_snapshot

    def test_repeated_calls_return_equal_findings(self) -> None:
        netlist = _design(_component("R1", value="330"))
        schematic = _design(_component("R1", value="470"), _component("U1"))
        assert cross_check(netlist, schematic).findings == cross_check(netlist, schematic).findings
