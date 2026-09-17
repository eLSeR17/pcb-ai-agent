"""Deterministic bill-of-materials (BOM) generation.

One BOM row per component, sorted by reference. The BOM is derived from
the parsed netlist only — nothing here is probabilistic and no component
is ever guessed.

Known limitation (v1): rows are **not** consolidated by value/footprint;
grouping identical parts and computing quantities is planned.
``en_bom`` is ``True`` for every row because v1 has no exclusion rules
(KiCad ``exclude_from_bom``/``dnp`` handling is future work).
"""

from __future__ import annotations

from pcbai.kicad.netlist import Design

__all__ = ["BOM_KEYS", "bom_table", "generate_bom"]

#: Column schema of a BOM row.
BOM_KEYS: tuple[str, ...] = ("ref", "value", "footprint", "pins", "en_bom")


def generate_bom(design: Design) -> list[dict]:
    """Generate one BOM row per component, ordered alphabetically by ref.

    Row keys:
        ref: Reference designator.
        value: Component value (``None`` when absent in the design).
        footprint: KiCad footprint id (``None`` when absent).
        pins: Number of pins wired to nets in the netlist; for designs
            without pin-level connectivity (schematic-derived) this falls
            back to the number of declared pins.
        en_bom: ``True`` for every row in v1 (no exclusion rules yet).

    Returns a list of dicts; use :func:`bom_table` to render it.
    """
    rows: list[dict] = []
    for ref in sorted(design.components):
        component = design.components[ref]
        wired = [pin for pin, net in component.pins.items() if net is not None]
        pins = len(wired) if wired else len(component.pins)
        rows.append(
            {
                "ref": ref,
                "value": component.value,
                "footprint": component.footprint,
                "pins": pins,
                "en_bom": True,
            }
        )
    return rows


def bom_table(bom: list[dict]) -> str:
    """Render a BOM as an aligned ASCII table (terminal/docs friendly).

    ``None`` cells render as ``-`` so a missing value/footprint is visible
    at a glance. Columns are padded to the widest cell; the table is
    deterministic for a given BOM.
    """
    width = {key: len(key) for key in BOM_KEYS}
    for row in bom:
        for key in BOM_KEYS:
            width[key] = max(width[key], len(_cell(row.get(key))))
    header = "  ".join(key.ljust(width[key]) for key in BOM_KEYS)
    separator = "  ".join("-" * width[key] for key in BOM_KEYS)
    body = ["  ".join(_cell(row.get(key)).ljust(width[key]) for key in BOM_KEYS) for row in bom]
    return "\n".join([header, separator, *body])


def _cell(value: object) -> str:
    """Render one BOM value for the ASCII table."""
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "True" if value else "False"
    return str(value)
