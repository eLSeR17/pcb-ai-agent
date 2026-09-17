"""BOM generator tests against the committed netlist fixtures."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from pcbai.design.bom import bom_table, generate_bom
from pcbai.kicad.netlist import Design, parse_netlist

FIXTURES = Path(__file__).parent / "fixtures"
SIMPLE_LED = FIXTURES / "simple-led.kicad_net"
BAD_LED = FIXTURES / "bad-led.kicad_net"

BOM_SCHEMA = {"ref", "value", "footprint", "pins", "en_bom"}


class TestGenerateBom:
    def test_simple_led_three_rows_sorted_by_ref(self) -> None:
        bom = generate_bom(parse_netlist(SIMPLE_LED))
        assert [row["ref"] for row in bom] == ["J1", "LED1", "R1"]

    def test_row_fields_exact(self) -> None:
        bom = generate_bom(parse_netlist(SIMPLE_LED))
        assert bom[2] == {
            "ref": "R1",
            "value": "330",
            "footprint": "Resistor_SMD:R_0603_1608Metric",
            "pins": 2,
            "en_bom": True,
        }

    def test_every_row_has_the_bom_schema(self) -> None:
        bom = generate_bom(parse_netlist(SIMPLE_LED))
        assert all(set(row.keys()) == BOM_SCHEMA for row in bom)

    def test_empty_design_yields_empty_bom(self) -> None:
        assert generate_bom(Design()) == []

    def test_pins_counts_wired_pins(self) -> None:
        # In bad-led, R2 is wired through a single pin; LED1 through two.
        bom = generate_bom(parse_netlist(BAD_LED))
        by_ref = {row["ref"]: row for row in bom}
        assert by_ref["R2"]["pins"] == 1
        assert by_ref["LED1"]["pins"] == 2

    def test_missing_value_stays_null(self) -> None:
        bom = generate_bom(parse_netlist(BAD_LED))
        by_ref = {row["ref"]: row for row in bom}
        assert by_ref["R2"]["value"] is None
        assert by_ref["R2"]["en_bom"] is True

    def test_null_footprint_propagates(self) -> None:
        design = parse_netlist(SIMPLE_LED)
        design.components["LED1"] = replace(design.components["LED1"], footprint=None)
        by_ref = {row["ref"]: row for row in generate_bom(design)}
        assert by_ref["LED1"]["footprint"] is None


class TestBomTable:
    def test_contains_all_refs_and_values(self) -> None:
        table = bom_table(generate_bom(parse_netlist(SIMPLE_LED)))
        for token in ("ref", "J1", "R1", "LED1", "330", "LED"):
            assert token in table

    def test_lines_are_aligned(self) -> None:
        table = bom_table(generate_bom(parse_netlist(SIMPLE_LED)))
        lines = table.splitlines()
        assert len(lines) == 5  # header + separator + 3 rows
        assert len({len(line) for line in lines}) == 1  # fixed column width

    def test_none_renders_as_placeholder(self) -> None:
        design = parse_netlist(SIMPLE_LED)
        design.components["LED1"] = replace(design.components["LED1"], value=None)
        table = bom_table(generate_bom(design))
        assert "None" not in table
        assert "-" in table
