"""Tests for the read-only MCP tools.

All tests exercise the pure tool functions defined in
:mod:`pcbai.mcp.server` — no MCP protocol, no network and no server process.
This is possible because the ``mcp`` SDK is imported lazily and the seven
tools are thin wrappers around deterministic functions.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pcbai.mcp.server import (
    DesignToolError,
    audit_design,
    check_connectivity,
    generate_bom,
    list_components,
    list_nets,
    load_design,
    size_resistor,
)

FIXTURES = Path(__file__).parent / "fixtures"
SIMPLE_LED = FIXTURES / "simple-led.kicad_net"
BAD_LED = FIXTURES / "bad-led.kicad_net"


class TestLoadDesign:
    def test_netlist_summary(self) -> None:
        result = load_design(SIMPLE_LED)
        assert result["format"] == "netlist"
        assert result["components"] == 3
        assert result["nets"] == 3
        assert result["component_types"] == {"J": 1, "LED": 1, "R": 1}
        assert result["path"] == str(SIMPLE_LED)

    def test_accepts_path_object(self) -> None:
        assert load_design(Path(SIMPLE_LED))["components"] == 3

    def test_unsupported_extension_raises(self, tmp_path: Path) -> None:
        unknown = tmp_path / "board.txt"
        unknown.write_text('(export (version "E"))', encoding="utf-8")
        with pytest.raises(DesignToolError, match="unsupported design extension"):
            load_design(unknown)


class TestListComponents:
    def test_ordered_by_ref(self) -> None:
        components = list_components(SIMPLE_LED)
        assert [c["ref"] for c in components] == ["J1", "LED1", "R1"]

    def test_fields_and_values(self) -> None:
        components = {c["ref"]: c for c in list_components(SIMPLE_LED)}
        assert set(components["R1"]) == {"ref", "value", "footprint"}
        assert components["R1"]["value"] == "330"
        assert components["R1"]["footprint"] == "Resistor_SMD:R_0603_1608Metric"
        assert components["LED1"]["value"] == "LED"

    def test_missing_value_is_none(self) -> None:
        components = {c["ref"]: c for c in list_components(BAD_LED)}
        assert components["R2"]["value"] is None


class TestListNets:
    def test_ordered_by_name(self) -> None:
        nets = list_nets(SIMPLE_LED)
        assert [net["name"] for net in nets] == ["5V", "GND", "LED_A"]

    def test_connections_sorted_and_structured(self) -> None:
        nets = {net["name"]: net for net in list_nets(SIMPLE_LED)}
        assert nets["5V"]["connections"] == [
            {"ref": "J1", "pin": "1"},
            {"ref": "R1", "pin": "1"},
        ]
        assert nets["LED_A"]["connections"] == [
            {"ref": "LED1", "pin": "1"},
            {"ref": "R1", "pin": "2"},
        ]


class TestCheckConnectivity:
    def test_healthy_design_has_single_advisory(self) -> None:
        result = check_connectivity(SIMPLE_LED)
        assert [finding["rule"] for finding in result["findings"]] == ["NO_DRIVER"]

    def test_bad_design_findings_have_evidence(self) -> None:
        result = check_connectivity(BAD_LED)
        assert len(result["findings"]) == 5
        for finding in result["findings"]:
            assert set(finding) == {"rule", "severity", "message", "evidence", "position"}
            assert finding["evidence"]
            assert finding["position"] == "netlist"

    def test_reuses_audit_design(self) -> None:
        # check_connectivity must not duplicate rule logic: same findings.
        assert check_connectivity(BAD_LED)["findings"] == audit_design(BAD_LED)["findings"]


class TestSizeResistor:
    def test_reference_led(self) -> None:
        assert size_resistor(5.0, 2.0, 0.02) == {"r_ohms": 150.0, "p_watts": 0.06}

    def test_e12_rounding(self) -> None:
        # 3.3 V / 2.0 V / 0.01 A -> raw 130 ohm -> nearest E12 is 120 ohm.
        assert size_resistor(3.3, 2.0, 0.01)["r_ohms"] == 120.0

    def test_non_physical_input_raises(self) -> None:
        with pytest.raises(ValueError, match="must exceed"):
            size_resistor(2.0, 3.3, 0.02)


class TestGenerateBom:
    def test_rows_sorted_by_ref(self) -> None:
        result = generate_bom(SIMPLE_LED)
        assert [row["ref"] for row in result["rows"]] == ["J1", "LED1", "R1"]
        assert set(result["rows"][0]) == {"ref", "value", "footprint", "pins", "en_bom"}

    def test_table_lists_every_ref(self) -> None:
        table = generate_bom(SIMPLE_LED)["table"]
        assert table.splitlines()[0].startswith("ref")
        for ref in ("J1", "LED1", "R1"):
            assert ref in table


class TestAuditDesign:
    def test_healthy_design_summary(self) -> None:
        summary = audit_design(SIMPLE_LED)["summary"]
        assert summary["source"] == "netlist"
        assert (summary["errors"], summary["warnings"], summary["infos"]) == (0, 0, 1)
        assert summary["total"] == 1
        assert summary["rules"] == ["NO_DRIVER"]

    def test_bad_design_summary(self) -> None:
        summary = audit_design(BAD_LED)["summary"]
        assert summary["total"] == 5
        assert (summary["errors"], summary["warnings"], summary["infos"]) == (0, 4, 1)
        assert summary["rules"] == [
            "FLOATING_NET",
            "LED_NO_LIMITER",
            "MISSING_VALUE",
            "NO_DRIVER",
            "UNCONNECTED_PIN",
        ]


class TestErrors:
    def test_missing_file(self) -> None:
        with pytest.raises(DesignToolError, match="design file not found"):
            load_design(FIXTURES / "does-not-exist.kicad_net")

    def test_malformed_file(self, tmp_path: Path) -> None:
        broken = tmp_path / "broken.kicad_net"
        broken.write_text("this is not an s-expression", encoding="utf-8")
        with pytest.raises(DesignToolError, match="invalid design file"):
            list_components(broken)

    def test_wrong_top_level_form(self, tmp_path: Path) -> None:
        wrong = tmp_path / "wrong.kicad_net"
        wrong.write_text("(kicad_sch (version 20231120))", encoding="utf-8")
        with pytest.raises(DesignToolError, match="invalid design file"):
            list_nets(wrong)


class TestJsonSerializable:
    @pytest.mark.parametrize(
        "call",
        [
            lambda: load_design(SIMPLE_LED),
            lambda: list_components(SIMPLE_LED),
            lambda: list_nets(SIMPLE_LED),
            lambda: check_connectivity(BAD_LED),
            lambda: size_resistor(5.0, 2.0, 0.02),
            lambda: generate_bom(SIMPLE_LED),
            lambda: audit_design(BAD_LED),
        ],
    )
    def test_every_tool_result_round_trips_through_json(self, call: object) -> None:
        payload = call()  # type: ignore[operator]
        assert json.loads(json.dumps(payload)) == payload
