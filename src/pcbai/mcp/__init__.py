"""Read-only MCP server (Layer 1 read layer over MCP).

Exposes the seven read-only design tools (``load_design``, ``list_components``,
``list_nets``, ``check_connectivity``, ``size_resistor``, ``generate_bom``,
``audit_design``) to an LLM agent over MCP. Built on the official ``mcp`` SDK
(modelcontextprotocol/python-sdk ``>= 2.0``, class ``MCPServer``).

The SDK is imported lazily by :class:`PcbMcpServer`, so the pure tool functions
can be imported and tested without the optional dependency installed.
"""

from pcbai.mcp.server import (
    DesignToolError,
    PcbMcpServer,
    audit_design,
    check_connectivity,
    generate_bom,
    list_components,
    list_nets,
    load_design,
    main,
    size_resistor,
)

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
