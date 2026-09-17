"""KiCad file parsing: S-expressions, netlists and a schematic subset.

Public API:

- :func:`pcbai.kicad.parse` / :func:`pcbai.kicad.parse_file` — S-expression
  parsing with line/column error reporting.
- :func:`pcbai.kicad.parse_netlist` — KiCad ``.net`` files -> :class:`Design`.
- :func:`pcbai.kicad.parse_schematic` — ``.kicad_sch`` subset ->
  :class:`SchematicDesign`.
"""

from pcbai.kicad.netlist import (
    Component,
    Design,
    Label,
    Net,
    NetlistError,
    Point,
    SchematicDesign,
    SchematicError,
    Wire,
    parse_netlist,
    parse_schematic,
)
from pcbai.kicad.sexpr import SExprError, parse, parse_file

__all__ = [
    "Component",
    "Design",
    "Label",
    "Net",
    "NetlistError",
    "Point",
    "SExprError",
    "SchematicDesign",
    "SchematicError",
    "Wire",
    "parse",
    "parse_file",
    "parse_netlist",
    "parse_schematic",
]
