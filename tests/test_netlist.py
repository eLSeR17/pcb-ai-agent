"""Parser and model tests for KiCad netlists and the schematic subset."""

from __future__ import annotations

from pathlib import Path

import pytest

from pcbai.kicad.netlist import (
    Component,
    Design,
    Label,
    Net,
    NetlistError,
    SchematicDesign,
    SchematicError,
    Wire,
    parse_netlist,
    parse_schematic,
)

FIXTURES = Path(__file__).parent / "fixtures"
NETLIST = FIXTURES / "simple-led.kicad_net"
SCHEMATIC = FIXTURES / "simple-led.kicad_sch"


class TestNetlistParser:
    """The committed ``simple-led.kicad_net`` fixture, parsed exactly."""

    def test_components_exact(self) -> None:
        design = parse_netlist(NETLIST)
        assert set(design.components) == {"J1", "R1", "LED1"}
        assert design.components["R1"] == Component(
            ref="R1",
            value="330",
            footprint="Resistor_SMD:R_0603_1608Metric",
            pins={"1": "5V", "2": "LED_A"},
        )
        assert design.components["J1"] == Component(
            ref="J1",
            value="Conn_01x02",
            footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
            pins={"1": "5V", "2": "GND"},
        )
        assert design.components["LED1"] == Component(
            ref="LED1",
            value="LED",
            footprint="LED_SMD:LED_0603_1608Metric",
            pins={"1": "LED_A", "2": "GND"},
        )

    def test_nets_exact(self) -> None:
        design = parse_netlist(NETLIST)
        assert set(design.nets) == {"GND", "5V", "LED_A"}
        assert design.nets["GND"] == Net("GND", [("J1", "2"), ("LED1", "2")])
        assert design.nets["5V"] == Net("5V", [("J1", "1"), ("R1", "1")])
        assert design.nets["LED_A"] == Net("LED_A", [("R1", "2"), ("LED1", "1")])

    def test_returns_a_design(self) -> None:
        assert isinstance(parse_netlist(NETLIST), Design)

    def test_accepts_raw_text(self) -> None:
        design = parse_netlist(NETLIST.read_text(encoding="utf-8"))
        assert set(design.nets) == {"GND", "5V", "LED_A"}

    def test_missing_file_path_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            parse_netlist("/no/such/dir/simple-led.kicad_net")


class TestNetlistEdgeCases:
    def test_component_without_footprint(self) -> None:
        text = (
            '(export (version "E")'
            '  (components (comp (ref "R1") (value "10k")))'
            '  (nets (net (code 0) (name "N1") (node (ref "R1") (pin "1"))))'
            ")"
        )
        design = parse_netlist(text)
        assert design.components["R1"] == Component("R1", "10k", None, {"1": "N1"})
        assert design.nets["N1"] == Net("N1", [("R1", "1")])

    def test_empty_value_normalised_to_none(self) -> None:
        text = '(export (version "E") (components (comp (ref "R1") (value ""))))'
        assert parse_netlist(text).components["R1"].value is None

    def test_unquoted_value_treated_as_text(self) -> None:
        text = '(export (version "E") (components (comp (ref "R1") (value 330))))'
        assert parse_netlist(text).components["R1"].value == "330"

    def test_duplicate_pin_connections_are_deduplicated(self) -> None:
        text = (
            '(export (version "E")'
            '  (components (comp (ref "R1") (value "10k")))'
            '  (nets (net (code 0) (name "N1")'
            '    (node (ref "R1") (pin "1")) (node (ref "R1") (pin "1"))))'
            ")"
        )
        design = parse_netlist(text)
        assert design.nets["N1"].connections == [("R1", "1")]


class TestNetlistErrors:
    def test_missing_version(self) -> None:
        with pytest.raises(NetlistError, match="version"):
            parse_netlist("(export (design))")

    def test_unsupported_version(self) -> None:
        with pytest.raises(NetlistError, match="unsupported netlist version 'Z'"):
            parse_netlist('(export (version "Z"))')

    def test_unknown_component_in_node(self) -> None:
        text = (
            '(export (version "E")'
            "  (components)"
            '  (nets (net (code 0) (name "N1") (node (ref "ZZ9") (pin "1"))))'
            ")"
        )
        with pytest.raises(NetlistError, match="ZZ9"):
            parse_netlist(text)

    def test_duplicate_component_reference(self) -> None:
        text = (
            '(export (version "E")'
            '  (components (comp (ref "R1") (value "a")) (comp (ref "R1") (value "b")))'
            ")"
        )
        with pytest.raises(NetlistError, match="duplicate component reference"):
            parse_netlist(text)


class TestSchematicParser:
    """The committed ``simple-led.kicad_sch`` fixture (documented subset)."""

    def test_components_exact(self) -> None:
        design = parse_schematic(SCHEMATIC)
        assert set(design.components) == {"J1", "R1", "LED1"}
        assert design.components["R1"] == Component(
            ref="R1",
            value="330",
            footprint="Resistor_SMD:R_0603_1608Metric",
            pins={"1": None, "2": None},
        )
        assert design.components["J1"].value == "Conn_01x02"
        assert (
            design.components["J1"].footprint
            == "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"
        )
        assert design.components["LED1"].value == "LED"
        assert design.components["LED1"].footprint == "LED_SMD:LED_0603_1608Metric"

    def test_returns_schematic_design_with_geometry(self) -> None:
        design = parse_schematic(SCHEMATIC)
        assert isinstance(design, SchematicDesign)
        assert isinstance(design, Design)
        assert len(design.wires) == 7
        assert len(design.labels) == 3
        assert len(design.junctions) == 1
        assert Label(name="5V", position=(2.54, 6.35)) in design.labels
        assert Wire(start=(12.7, 5.08), end=(10.16, 5.08)) in design.wires
        assert (12.7, 5.08) in design.junctions

    def test_nets_from_labels(self) -> None:
        design = parse_schematic(SCHEMATIC)
        assert set(design.nets) == {"GND", "5V", "LED_A"}
        for net in design.nets.values():
            assert net.connections == []

    def test_coherent_with_netlist(self) -> None:
        """Same mini-design: refs and values must match across both formats."""
        netlist = parse_netlist(NETLIST)
        schematic = parse_schematic(SCHEMATIC)
        assert set(schematic.components) == set(netlist.components)
        for ref, component in netlist.components.items():
            assert schematic.components[ref].value == component.value
            assert schematic.components[ref].footprint == component.footprint

    def test_multi_unit_symbols_merge_pins(self) -> None:
        text = (
            "(kicad_sch (version 20231120)"
            ' (symbol (lib_id "74xx:74HC00") (at 5.08 5.08) (unit 1)'
            '   (property "Reference" "U1") (property "Value" "74HC00") (pin "1") (pin "2"))'
            ' (symbol (lib_id "74xx:74HC00") (at 5.08 5.08) (unit 2)'
            '   (property "Reference" "U1") (property "Value" "74HC00") (pin "3") (pin "4")))'
        )
        design = parse_schematic(text)
        assert set(design.components["U1"].pins) == {"1", "2", "3", "4"}


class TestSchematicErrors:
    def test_missing_version(self) -> None:
        with pytest.raises(SchematicError, match="version"):
            parse_schematic('(kicad_sch (generator "eeschema"))')

    def test_unsupported_version(self) -> None:
        with pytest.raises(SchematicError, match="unsupported schematic version"):
            parse_schematic("(kicad_sch (version 99999999))")

    def test_short_circuit_between_labels(self) -> None:
        text = (
            "(kicad_sch (version 20231120)"
            ' (label "A" (at 1.27 1.27))'
            ' (label "B" (at 2.54 1.27))'
            " (wire (pts (xy 1.27 1.27) (xy 2.54 1.27))))"
        )
        with pytest.raises(SchematicError, match="short circuit"):
            parse_schematic(text)

    def test_symbol_without_reference_rejected(self) -> None:
        text = '(kicad_sch (version 20231120) (symbol (lib_id "Device:R")))'
        with pytest.raises(SchematicError, match="Reference"):
            parse_schematic(text)
