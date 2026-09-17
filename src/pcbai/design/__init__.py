"""Design-audit read layer: sizing math, evidence-based audit and BOM.

Public API:

- :func:`pcbai.design.e12_round` and the pure sizing calculators
  (:func:`pcbai.design.led_series_resistor`,
  :func:`pcbai.design.pull_up_resistor`,
  :func:`pcbai.design.voltage_divider`,
  :func:`pcbai.design.switch_diode_forward_check`,
  :func:`pcbai.design.decoupling_capacitor`,
  :func:`pcbai.design.led_current_with_series_r`) — closed-form, fully
  deterministic math (the project's anti-hallucination invariant).
- :func:`pcbai.design.audit_design` — evidence-based audit rules over a
  :class:`pcbai.kicad.netlist.Design`; every finding cites the concrete
  refs/nets that triggered it.
- :func:`pcbai.design.generate_bom` / :func:`pcbai.design.bom_table` —
  deterministic bill of materials.
"""

from pcbai.design.audit import AuditReport, Finding, audit_design
from pcbai.design.bom import BOM_KEYS, bom_table, generate_bom
from pcbai.design.sizing import (
    DIODE_DISSIPATION_LIMIT_W,
    E12_SERIES,
    PULL_UP_BAND_OHMS,
    decoupling_capacitor,
    e12_round,
    led_current_with_series_r,
    led_series_resistor,
    pull_up_resistor,
    switch_diode_forward_check,
    voltage_divider,
)

__all__ = [
    "AuditReport",
    "BOM_KEYS",
    "DIODE_DISSIPATION_LIMIT_W",
    "E12_SERIES",
    "Finding",
    "PULL_UP_BAND_OHMS",
    "audit_design",
    "bom_table",
    "decoupling_capacitor",
    "e12_round",
    "generate_bom",
    "led_current_with_series_r",
    "led_series_resistor",
    "pull_up_resistor",
    "switch_diode_forward_check",
    "voltage_divider",
]
