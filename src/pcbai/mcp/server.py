"""Read-only MCP server exposing the design read layer to an LLM agent.

Tool contract (field names and shapes are stable — do not change them):

=================== ===================== =========================================
tool                input                 output
=================== ===================== =========================================
load_design         {path}                {path, format, components, nets,
                                           component_types}
list_components     {path}                {components: [{ref, value, footprint}]}
list_nets           {path}                {nets: [{name, connections:
                                           [{ref, pin}]}]}
check_connectivity  {path}                {findings: [{rule, severity, message,
                                           evidence, position}]}
size_resistor       {v_supply, v_led,     {r_ohms, p_watts}
                     i_led}
generate_bom        {path}                {rows: [{ref, value, footprint, pins,
                                           en_bom}], table}
audit_design        {path}                {findings, summary}
=================== ===================== =========================================

READ-ONLY BY DESIGN: no tool in this module writes to disk, opens a network
connection or mutates the parsed design. Every tool is a thin wrapper around a
pure, deterministic function defined here, so the seven functions can be
unit-tested without the MCP protocol or the SDK.

SDK note: the official ``mcp`` package (modelcontextprotocol/python-sdk
``>= 2.0``, class ``MCPServer``) is imported lazily inside
:class:`PcbMcpServer.__init__`. Importing this module therefore succeeds even
when the optional ``mcp`` extra is not installed, which keeps the pure
function tests dependency-free.

Run the server over stdio::

    PYTHONPATH=src python3 -m pcbai.mcp.server
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from pcbai.design.audit import AuditReport
from pcbai.design.audit import audit_design as _audit_design
from pcbai.design.bom import bom_table
from pcbai.design.bom import generate_bom as _generate_bom
from pcbai.design.sizing import led_series_resistor
from pcbai.kicad.netlist import Design, NetlistError, SchematicError, parse_netlist, parse_schematic
from pcbai.kicad.sexpr import SExprError

__all__ = [
    "DesignToolError",
    "PcbMcpServer",
    "audit_design",
    "check_connectivity",
    "generate_bom",
    "list_components",
    "list_nets",
    "load_design",
    "main",
    "size_resistor",
]

#: File suffixes routed to the netlist parser (source of truth for connectivity).
_NETLIST_SUFFIXES: frozenset[str] = frozenset({".kicad_net", ".net"})

#: File suffixes routed to the schematic parser (documented .kicad_sch subset).
_SCHEMATIC_SUFFIXES: frozenset[str] = frozenset({".kicad_sch"})

_REF_PREFIX = re.compile(r"^[A-Za-z]+")
"""Leading letters of a reference designator, e.g. ``"LED1"`` -> ``"LED"``."""


class DesignToolError(ValueError):
    """Raised by the read-only tools for a missing or invalid design file.

    The MCP wrapper catches it and returns ``{"error": {"type", "message"}}``
    instead of leaking a raw traceback to the agent.
    """


def _load_design(path: str | Path) -> tuple[Design, str]:
    """Load ``path`` with the parser selected by its extension.

    Returns ``(design, format_name)`` where ``format_name`` is ``"netlist"``
    or ``"schematic"``. Raises :class:`DesignToolError` with a clear message
    when the file is missing, has an unsupported extension or is malformed.
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise DesignToolError(f"design file not found: {str(path)!r}")

    suffix = file_path.suffix.lower()
    parser: Callable[[str | Path], Design]
    if suffix in _NETLIST_SUFFIXES:
        parser, format_name = parse_netlist, "netlist"
    elif suffix in _SCHEMATIC_SUFFIXES:
        parser, format_name = parse_schematic, "schematic"
    else:
        supported = ", ".join(sorted(_NETLIST_SUFFIXES | _SCHEMATIC_SUFFIXES))
        raise DesignToolError(
            f"unsupported design extension {suffix!r} for {str(path)!r} (supported: {supported})"
        )

    try:
        return parser(file_path), format_name
    except (NetlistError, SchematicError, SExprError, UnicodeDecodeError) as exc:
        raise DesignToolError(f"invalid design file {str(path)!r}: {exc}") from exc


def _component_types(design: Design) -> dict[str, int]:
    """Count components by reference prefix (``R``, ``C``, ``LED``, ``J``...)."""
    counts: dict[str, int] = {}
    for ref in design.components:
        match = _REF_PREFIX.match(ref)
        prefix = match.group(0) if match else ref
        counts[prefix] = counts.get(prefix, 0) + 1
    return dict(sorted(counts.items()))


def _finding_dicts(report: AuditReport) -> list[dict[str, Any]]:
    """Audit findings as plain JSON-serializable dicts."""
    return [asdict(finding) for finding in report.findings]


# --------------------------------------------------------------------------- #
# Pure tool functions (the tested read-only API)
# --------------------------------------------------------------------------- #
def load_design(path: str | Path) -> dict[str, Any]:
    """Summarise a KiCad design: format, component/net counts and types.

    The parser is auto-detected from the extension (``.kicad_net``/``.net``
    -> netlist, ``.kicad_sch`` -> schematic). Returns ``path``, ``format``,
    ``components``/``nets`` counts and ``component_types`` (ref prefix ->
    count).
    """
    design, format_name = _load_design(path)
    return {
        "path": str(path),
        "format": format_name,
        "components": len(design.components),
        "nets": len(design.nets),
        "component_types": _component_types(design),
    }


def list_components(path: str | Path) -> list[dict[str, Any]]:
    """List every component as ``{ref, value, footprint}``, ordered by ref."""
    design, _ = _load_design(path)
    return [
        {
            "ref": design.components[ref].ref,
            "value": design.components[ref].value,
            "footprint": design.components[ref].footprint,
        }
        for ref in sorted(design.components)
    ]


def list_nets(path: str | Path) -> list[dict[str, Any]]:
    """List every net as ``{name, connections}``, ordered by net name.

    Connections are ``{ref, pin}`` pairs sorted by ref then pin.
    """
    design, _ = _load_design(path)
    nets: list[dict[str, Any]] = []
    for name in sorted(design.nets):
        net = design.nets[name]
        connections = sorted(net.connections)
        nets.append(
            {
                "name": net.name,
                "connections": [{"ref": ref, "pin": pin} for ref, pin in connections],
            }
        )
    return nets


def check_connectivity(path: str | Path) -> dict[str, Any]:
    """Run the audited connectivity rules and return the raw findings.

    Reuses the read-layer ``audit_design`` rules; no rule logic is duplicated
    here. Returns ``{"findings": [{rule, severity, message, evidence,
    position}]}``.
    """
    design, source = _load_design(path)
    report = _audit_design(design, source=source)
    return {"findings": _finding_dicts(report)}


def size_resistor(v_supply: float, v_led: float, i_led: float) -> dict[str, float]:
    """Series resistor for an LED, with E12 rounding and dissipation.

    Thin wrapper over :func:`pcbai.design.sizing.led_series_resistor` (which
    already rounds to the E12 series). Returns ``{"r_ohms", "p_watts"}``;
    raises ``ValueError`` for non-physical inputs.
    """
    resistor, power = led_series_resistor(v_supply, v_led, i_led)
    return {"r_ohms": resistor, "p_watts": power}


def generate_bom(path: str | Path) -> dict[str, Any]:
    """Deterministic bill of materials: ``{"rows": [...], "table": "..."}``.

    One row per component (sorted by ref) plus the aligned ASCII table
    from the BOM builder. No component is ever guessed.
    """
    design, _ = _load_design(path)
    rows = _generate_bom(design)
    return {"rows": rows, "table": bom_table(rows)}


def audit_design(path: str | Path) -> dict[str, Any]:
    """Full audit report over a design: all findings plus the summary.

    Returns ``{"findings": [...], "summary": {source, total, errors,
    warnings, infos, rules}}``.
    """
    design, source = _load_design(path)
    report = _audit_design(design, source=source)
    return {"findings": _finding_dicts(report), "summary": report.summary}


# --------------------------------------------------------------------------- #
# MCP wrapper
# --------------------------------------------------------------------------- #
def _guarded(fn: Callable[..., Any], *args: Any) -> dict[str, Any]:
    """Call a pure tool and turn known failures into a clear error payload.

    A missing/invalid design becomes ``{"error": {"type": "design_error",
    ...}}`` and a non-physical sizing input becomes ``invalid_argument`` —
    never a raw traceback as tool output.
    """
    try:
        return fn(*args)
    except DesignToolError as exc:
        return {"error": {"type": "design_error", "message": str(exc)}}
    except ValueError as exc:
        return {"error": {"type": "invalid_argument", "message": str(exc)}}


class PcbMcpServer:
    """MCP server bundle: ``MCPServer`` + the seven read-only design tools.

    The SDK is imported lazily inside :meth:`__init__` so this module (and its
    pure functions) can be imported and unit-tested without the optional
    ``mcp`` dependency installed. The wrapper offers ``call_tool``/``list_tools``
    for in-process use; the stdio transport is available through
    ``self.mcp.run_stdio_async()`` (see :func:`main`).
    """

    def __init__(self) -> None:
        from mcp.server.mcpserver import MCPServer  # lazy: optional dependency

        self.mcp = MCPServer(
            name="pcb-ai-agent",
            version="0.1.0",
            description="Read-only KiCad design assistant: parse netlists and "
            "schematics, audit connectivity, size passives and generate a BOM.",
        )
        self._register_tools()

    def _register_tools(self) -> None:
        mcp = self.mcp

        @mcp.tool(
            name="load_design",
            description="Summarise a KiCad design file (netlist .kicad_net/.net "
            "or schematic .kicad_sch): format, component and net counts and "
            "component types by reference prefix.",
        )
        def load_design_tool(path: str) -> dict[str, Any]:
            return _guarded(load_design, path)

        @mcp.tool(
            name="list_components",
            description="List every component as {ref, value, footprint}, ordered "
            "by reference designator.",
        )
        def list_components_tool(path: str) -> dict[str, Any]:
            return _guarded(lambda p: {"components": list_components(p)}, path)

        @mcp.tool(
            name="list_nets",
            description="List every net as {name, connections} where connections "
            "are {ref, pin} pairs, ordered by net name.",
        )
        def list_nets_tool(path: str) -> dict[str, Any]:
            return _guarded(lambda p: {"nets": list_nets(p)}, path)

        @mcp.tool(
            name="check_connectivity",
            description="Run the evidence-based audit rules and return the raw "
            "findings ({rule, severity, message, evidence, position}).",
        )
        def check_connectivity_tool(path: str) -> dict[str, Any]:
            return _guarded(check_connectivity, path)

        @mcp.tool(
            name="size_resistor",
            description="Size the series resistor for an LED (Ohm's law, E12 "
            "rounded). Returns r_ohms and the chosen resistor's dissipation "
            "p_watts.",
        )
        def size_resistor_tool(v_supply: float, v_led: float, i_led: float) -> dict[str, Any]:
            return _guarded(size_resistor, v_supply, v_led, i_led)

        @mcp.tool(
            name="generate_bom",
            description="Generate a deterministic bill of materials from a design: "
            "one row per component plus an aligned ASCII table.",
        )
        def generate_bom_tool(path: str) -> dict[str, Any]:
            return _guarded(generate_bom, path)

        @mcp.tool(
            name="audit_design",
            description="Full design audit: all findings plus a severity summary "
            "(errors/warnings/infos and the rules that fired).",
        )
        def audit_design_tool(path: str) -> dict[str, Any]:
            return _guarded(audit_design, path)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Invoke a registered tool in-process (returns the SDK result)."""
        return await self.mcp.call_tool(name, arguments)

    async def list_tools(self) -> list[Any]:
        """Registered tools with their JSON input schemas."""
        return await self.mcp.list_tools()


def main() -> None:
    """Run the read-only server over stdio (default MCP transport)."""
    server = PcbMcpServer()

    async def _serve() -> None:
        await server.mcp.run_stdio_async()

    asyncio.run(_serve())


if __name__ == "__main__":  # pragma: no cover - exercised via stdio clients
    main()
