# MCP server (read layer over stdio)

`pcbai.mcp.server` exposes the read layer (KiCad parsers, sizing
math, evidence-based audit, BOM) as **seven read-only MCP tools** that an LLM
agent can call over stdio. It follows the same pattern as
[plc-ai-agent](https://github.com/eLSeR17/plc-ai-agent): the official `mcp`
SDK (modelcontextprotocol/python-sdk, `>= 2.0`, class `MCPServer`).

> **Read-only by design.** No tool writes to disk, opens a network connection
> or mutates the parsed design. Every tool is a thin wrapper around a pure,
> deterministic function — the numeric values and findings always come from
> code, never from the LLM.

## Tools

| Tool | Input | Output |
|---|---|---|
| `load_design(path)` | design file path | `{path, format, components, nets, component_types}` |
| `list_components(path)` | design file path | `{components: [{ref, value, footprint}]}` (ordered by ref) |
| `list_nets(path)` | design file path | `{nets: [{name, connections: [{ref, pin}]}]}` (ordered by name) |
| `check_connectivity(path)` | design file path | `{findings: [{rule, severity, message, evidence, position}]}` |
| `size_resistor(v_supply, v_led, i_led)` | volts, volts, amps | `{r_ohms, p_watts}` (E12 rounded) |
| `generate_bom(path)` | design file path | `{rows: [{ref, value, footprint, pins, en_bom}], table}` |
| `audit_design(path)` | design file path | `{findings, summary}` (`summary` = severity counts + rules) |

`path` accepts KiCad netlists (`.kicad_net`, `.net`) or schematics
(`.kicad_sch`); the parser is auto-detected from the extension. All results are
JSON-serializable.

Errors are returned, not raised into the protocol:
`{"error": {"type": "design_error" | "invalid_argument", "message": "..."}}`
for a missing/malformed design or non-physical sizing input.

## Run the server

```bash
pip install -e ".[mcp]"        # optional extra: mcp>=2.0,<3.0
PYTHONPATH=src python3 -m pcbai.mcp.server
```

The server speaks MCP over stdio; point your MCP client at that command. The
`mcp` dependency is optional: it is imported lazily by `PcbMcpServer`, so the
pure tool functions can be imported and tested without it.
