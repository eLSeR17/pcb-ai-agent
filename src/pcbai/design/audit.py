"""Evidence-based design audit over a parsed KiCad design.

Read-layer audit rules. Every finding is a :class:`Finding` dataclass
with a stable rule id, a severity, a specific human message and
``evidence`` — the concrete refs/nets of the design that triggered the
rule. This is the grounding contract of the project: **no finding without
evidence**, and no numeric or connectivity claim that does not come from
the parsed netlist.

Rule catalogue (v1):

- ``FLOATING_NET``     warning — a net with a single connection (likely
  left unterminated). Explicit KiCad no-connect labels
  (``unconnected-(...)``) are excluded: they are schematic intent.
- ``UNCONNECTED_PIN``  warning — a component wired through exactly one
  pin (count heuristic: netlist pins are derived from nets, so a single
  wired pin on an otherwise multi-pin part means one or more pins are
  open). Single-pin mechanical parts (mounting holes ``H*``/``MH*``,
  test points ``TP*``, fiducials ``FID*``) and power symbols
  (``#PWR01``) are valid one-connection components and are excluded.
- ``MISSING_VALUE``    warning — R/C/D/L/Q/U component with no value,
  except the power-symbol pattern (pin 1 on GND/VCC).
- ``MISSING_FOOTPRINT`` error  — component without footprint (the board is
  not manufacturable).
- ``NO_DRIVER``        info   — a net whose connections are only passives
  (refs starting with R/C/L/D) with no driver (ref starting with U/J/Q)
  and no power net name (5V/VCC/GND).
- ``LED_NO_LIMITER``   warning — an LED whose nets contain no pin of a
  series resistor: unbounded current, part will be destroyed.

References and namespaces: KiCad hierarchical sheets can prefix a
reference (``motherboard/R18``, ``sheet1.U5``) and footprints/net names
may contain ``-`` (``MB-R12``); every rule resolves the part kind through
the last namespace segment (:func:`_ref_kind`) instead of reading
``ref[0]``, so namespaced refs behave exactly like their plain
counterparts.

Rule catalogue (v2, deterministic netlist-only checks):

- ``E_SERIES_COMPLIANCE`` warning — R/C value is not a preferred
  E6/E12/E24/E96 number (IEC 60063). A value like ``333`` (mantissa
  3.33) instead of the standard ``330`` is flagged; decade multiples and
  exact preferred numbers pass (see :func:`_is_e_series`).
- ``LED_SERIES_RESISTOR`` warning — an LED series resistor that is 0 ohm
  (a short) or below ~22 ohm (cannot limit the current for a 3.3-5 V
  supply) is flagged. ``LED_NO_LIMITER`` keeps owning the resistorless
  case.
- ``CAP_DERATING``     info    — an electrolytic capacitor with an explicit
  voltage rating (e.g. ``"10uF 50V"``) on a numeric power rail
  (5V/3V3/12V...) rated below 1.5x the rail voltage. Bonus control: fires
  only when both numbers are present in the netlist.

Pending V2.1 (documented, not implemented in this sprint): I2C pull-up
detection by value (an R ~4.7k on SDA/SCL-like nets) is a netlist-verifiable
check but needs net-name semantics beyond the current scope; it stays a
documented roadmap item.

Findings are returned severity-first (error > warning > info) and then in
alphabetical order of the first evidence entry and rule id, so the report
is fully deterministic.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from pcbai.design.sizing import E12_SERIES
from pcbai.kicad.netlist import Design

__all__ = ["AuditReport", "Finding", "audit_design"]

#: Order used to sort findings (lower rank = more severe).
SEVERITY_RANK: dict[str, int] = {"error": 0, "warning": 1, "info": 2}

#: Reference prefixes treated as passive parts (NO_DRIVER rule).
PASSIVE_PREFIXES: tuple[str, ...] = ("R", "C", "L", "D")

#: Reference prefixes treated as active drivers / sources (NO_DRIVER rule).
DRIVER_PREFIXES: tuple[str, ...] = ("U", "J", "Q")

#: Net names that imply a power source (NO_DRIVER rule).
POWER_NET_NAMES: frozenset[str] = frozenset({"5V", "VCC", "GND"})

#: Reference prefixes that must always carry a value (MISSING_VALUE rule).
VALUE_REQUIRED_PREFIXES: tuple[str, ...] = ("R", "C", "D", "L", "Q", "U")

#: Reference kinds treated as valid single-connection parts (UNCONNECTED_PIN
#: rule): ``H``/``MH`` mounting holes, ``TP`` test points and ``FID``
#: fiducials. One wired pin is their normal operating point.
SINGLE_PIN_PART_KINDS: frozenset[str] = frozenset({"H", "MH", "TP", "FID"})

#: Net-name prefix of KiCad explicit no-connect labels, e.g.
#: ``unconnected-(U1-Pad3)`` (FLOATING_NET rule).
NO_CONNECT_NET_PREFIX: str = "unconnected-"

#: E-series rows (IEC 60063) keyed by series name. E12 reuses the row
#: maintained by :mod:`pcbai.design.sizing` so the project has a single
#: source of truth; E6 is every other E12 step; E24 adds the fine 5 %
#: steps; E96 (1 % parts) is the full 96-mantissa grid.
E_SERIES_ROWS: dict[str, tuple[float, ...]] = {
    "E6": tuple(E12_SERIES[::2]),
    "E12": E12_SERIES,
    "E24": (
        1.0,
        1.1,
        1.2,
        1.3,
        1.5,
        1.6,
        1.8,
        2.0,
        2.2,
        2.4,
        2.7,
        3.0,
        3.3,
        3.6,
        3.9,
        4.3,
        4.7,
        5.1,
        5.6,
        6.2,
        6.8,
        7.5,
        8.2,
        9.1,
    ),
    "E96": (
        1.00,
        1.02,
        1.05,
        1.07,
        1.10,
        1.13,
        1.15,
        1.18,
        1.21,
        1.24,
        1.27,
        1.30,
        1.33,
        1.37,
        1.40,
        1.43,
        1.47,
        1.50,
        1.54,
        1.58,
        1.62,
        1.65,
        1.69,
        1.74,
        1.78,
        1.82,
        1.87,
        1.91,
        1.96,
        2.00,
        2.05,
        2.10,
        2.15,
        2.21,
        2.26,
        2.32,
        2.37,
        2.43,
        2.49,
        2.55,
        2.61,
        2.67,
        2.74,
        2.80,
        2.87,
        2.94,
        3.01,
        3.09,
        3.16,
        3.24,
        3.32,
        3.40,
        3.48,
        3.57,
        3.65,
        3.74,
        3.83,
        3.92,
        4.02,
        4.12,
        4.22,
        4.32,
        4.42,
        4.53,
        4.64,
        4.75,
        4.87,
        4.99,
        5.11,
        5.23,
        5.36,
        5.49,
        5.62,
        5.76,
        5.90,
        6.04,
        6.19,
        6.34,
        6.49,
        6.65,
        6.81,
        6.98,
        7.15,
        7.32,
        7.50,
        7.68,
        7.87,
        8.06,
        8.25,
        8.45,
        8.66,
        8.87,
        9.09,
        9.31,
        9.54,
        9.76,
    ),
}

#: Relative tolerance for :func:`_is_e_series`. 0.25 % accepts exact
#: preferred numbers (and the binary floating-point wobble around them)
#: while rejecting non-preferred mantissas even next to the dense E96
#: grid: ``333`` (3.33) sits 0.30 % off the E96 step 3.32 and ``2.5k``
#: (2.5) sits 0.40 % off 2.49, so both are flagged. No two consecutive
#: E96 steps are ever closer than ~0.75 %, so a non-standard value can
#: never hide within 0.25 % of a step.
E_SERIES_MATCH_TOLERANCE: float = 0.0025

#: Resistor values below this are too weak to limit an LED current on a
#: 3.3-5 V supply (LED_SERIES_RESISTOR rule).
LED_LIMITER_MIN_OHMS: float = 22.0

#: Capacitor derating factor: the working voltage must stay below the
#: rating by this margin (DC-bias loss of MLCC/electrolytics).
CAP_DERATING_FACTOR: float = 1.5

#: ``4R7``-style decimal-point resistance notation (EIA convention). Only a
#: single integer digit is accepted: the notation exists to express sub-10
#: ohm values, so a form like ``330R0`` (which would be plain ``330``) is
#: treated as ambiguous and left unparsed.
_RES_DECIMAL_RE = re.compile(r"(\d)r(\d+(?:\.\d+)?)\s*", re.IGNORECASE)

#: Plain resistance value: mantissa + optional unit (empty, R, k, M, G, Meg).
_RES_VALUE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*((?:meg)|[rgkm])?\s*", re.IGNORECASE)

#: Capacitance value: mantissa + p/n/u(µ)/m unit (farad suffix optional).
_CAP_VALUE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([pnumµμ])(?:f)?", re.IGNORECASE)

#: Voltage rating embedded in a value, e.g. ``"10uF 50V"`` -> ``50``.
_VOLTAGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*V\b")

#: Plain numeric rail name, e.g. ``5V`` -> 5.0, ``3.3V`` -> 3.3.
_RAIL_PLAIN_RE = re.compile(r"^(\d+(?:\.\d+)?)V$")

#: Short numeric rail name, e.g. ``3V3`` -> 3.3, ``5V0`` -> 5.0.
_RAIL_SHORT_RE = re.compile(r"^(\d+)V(\d)$")

#: Capacitance suffix that suggests an electrolytic (by value alone).
_ELECTROLYTIC_SUFFIX_RE = re.compile(r"\d+(?:\.\d+)?\s*[muµμ]f?", re.IGNORECASE)

_STRIP_TOLERANCE_RE = re.compile(r"\s*\d+(?:\.\d+)?%")

#: Namespace separators of hierarchical references (``motherboard/R18``,
#: ``sheet1.U5``, ``MB-R12``).
_REF_NAMESPACE_SPLIT_RE = re.compile(r"[/.\-]")

#: Leading alphabetic run of a reference kind (``R1`` -> ``"R"``).
_REF_KIND_RE = re.compile(r"[A-Za-z]+")


def _ref_kind(ref: str) -> str | None:
    """Leading alphabetic token of the final namespace segment of a ref.

    KiCad allows hierarchical/namespaced references, so rules must never
    read ``ref[0]`` of the raw designator. The kind is the leading alpha
    run of the segment after the last ``/``, ``.`` or ``-`` separator:
    ``R1`` -> ``"R"``, ``LED1`` -> ``"LED"``, ``motherboard/R18`` ->
    ``"R"``, ``sheet1.U5`` -> ``"U"``, ``MB-R12`` -> ``"R"``, ``TP1`` ->
    ``"TP"``. References with no alphabetic head (e.g. the ``#PWR01``
    power symbol) return ``None`` — callers treat unknown kinds
    conservatively. No case folding: prefix matching stays
    case-sensitive, exactly as it was on plain refs.
    """
    if not ref:
        return None
    token = _REF_NAMESPACE_SPLIT_RE.split(ref)[-1]
    match = _REF_KIND_RE.match(token)
    return match.group(0) if match is not None else None


def _ref_class(ref: str) -> str | None:
    """First-letter class of a reference kind (``LED1`` -> ``"L"``).

    The historical rules used ``ref[0]`` (``R`` resistors, ``C``
    capacitors, ``L`` LEDs/inductors, ``U``/``J``/``Q`` drivers);
    ``_ref_class`` keeps that single-letter semantics while resolving the
    kind through :func:`_ref_kind`, so namespaced refs behave exactly
    like their plain counterparts (``motherboard/R18`` -> ``"R"``).
    Returns ``None`` when the ref has no alphabetic kind.
    """
    kind = _ref_kind(ref)
    return kind[0] if kind else None


@dataclass(frozen=True)
class Finding:
    """One audit finding anchored to concrete design evidence.

    Attributes:
        rule: Stable rule id, e.g. ``"LED_NO_LIMITER"``.
        severity: ``"error"``, ``"warning"`` or ``"info"``.
        message: Human-readable, specific description.
        evidence: Concrete refs/nets of the design that triggered the
            rule (e.g. ``["R1", "net:LED_A"]``).
        position: Source context (e.g. ``"netlist"``), if any.
    """

    rule: str
    severity: str
    message: str
    evidence: list[str]
    position: str | None = None


@dataclass(frozen=True)
class AuditReport:
    """The result of :func:`audit_design`.

    Attributes:
        findings: Findings sorted by severity then evidence/rule.
        summary: Counts per severity, total and the set of rules that
            fired (keys: ``source``, ``total``, ``errors``, ``warnings``,
            ``infos``, ``rules``).
    """

    findings: list[Finding]
    summary: dict[str, object]


def audit_design(design: Design, source: str = "netlist") -> AuditReport:
    """Run every v1/v2 audit rule over ``design``.

    ``source`` is carried into each finding's ``position`` field (the
    design origin, e.g. ``"netlist"`` or ``"schematic"``). The returned
    report is deterministic: findings are sorted by severity
    (error > warning > info) and then by the first evidence entry and
    rule id.
    """
    findings: list[Finding] = []
    findings.extend(_floating_net(design, source))
    findings.extend(_unconnected_pin(design, source))
    findings.extend(_missing_value(design, source))
    findings.extend(_missing_footprint(design, source))
    findings.extend(_no_driver(design, source))
    findings.extend(_led_no_limiter(design, source))
    findings.extend(_e_series_compliance(design, source))
    findings.extend(_led_series_resistor(design, source))
    findings.extend(_cap_derating(design, source))
    findings.sort(key=_sort_key)
    return AuditReport(findings=findings, summary=_summarize(findings, source))


def _floating_net(design: Design, source: str) -> list[Finding]:
    """``FLOATING_NET``: a net with exactly one connection (warning).

    KiCad no-connect labels export as ``unconnected-(...)`` nets with a
    single connection; they are explicit schematic intent, so they are
    excluded (prefix :data:`NO_CONNECT_NET_PREFIX`). A net that is
    legitimately mono-connected — a mounting hole, fiducial, test point
    or lone power symbol — is still reported: the warning asks to confirm
    the intent.
    """
    findings: list[Finding] = []
    for net in sorted(design.nets.values(), key=lambda item: item.name):
        if len(net.connections) != 1:
            continue
        if net.name.lower().startswith(NO_CONNECT_NET_PREFIX):
            continue  # explicit KiCad no-connect: schematic intent
        ref, pin = net.connections[0]
        findings.append(
            Finding(
                rule="FLOATING_NET",
                severity="warning",
                message=(
                    f"net '{net.name}' has a single connection ({ref}.{pin}); "
                    "it may be left unterminated"
                ),
                evidence=[net.name, ref],
                position=source,
            )
        )
    return findings


def _is_single_pin_part(ref: str) -> bool:
    """True for refs of valid single-connection parts (UNCONNECTED_PIN).

    Covers KiCad power symbols (``#PWR01``) and the mechanical
    single-pin families ``H``/``MH`` (mounting holes), ``TP`` (test
    points) and ``FID`` (fiducials) — see :data:`SINGLE_PIN_PART_KINDS`.
    The ref kind is resolved through :func:`_ref_kind`, so namespaced
    refs (``sheet1.TP3``) are handled too.
    """
    return ref.startswith("#") or _ref_kind(ref) in SINGLE_PIN_PART_KINDS


def _unconnected_pin(design: Design, source: str) -> list[Finding]:
    """``UNCONNECTED_PIN``: component wired through exactly one pin.

    Count heuristic (v1): in a netlist, ``component.pins`` is derived from
    the nets, so the number of wired pins is the only pin information
    available. A part wired through exactly one pin means one or more of
    its other pins are not connected to anything. Single-pin mechanical
    parts (mounting holes, test points, fiducials) and KiCad power
    symbols are valid one-connection components and are excluded
    (:func:`_is_single_pin_part`). Designs without pin-level connectivity
    (schematic-derived, all pin nets ``None``) are skipped because the
    parser cannot resolve pin connectivity there.
    """
    findings: list[Finding] = []
    for ref, component in sorted(design.components.items()):
        wired = [pin for pin, net in component.pins.items() if net is not None]
        if not wired:
            continue  # no pin-level connectivity in this design
        if len(wired) == 1:
            if _is_single_pin_part(ref):
                continue  # H*/MH*/TP*/FID*/#PWR01: one pin is their normal state
            findings.append(
                Finding(
                    rule="UNCONNECTED_PIN",
                    severity="warning",
                    message=(
                        f"component {ref} is wired through a single pin ({wired[0]}); "
                        "check for pins left unconnected"
                    ),
                    evidence=[ref, wired[0]],
                    position=source,
                )
            )
    return findings


def _missing_value(design: Design, source: str) -> list[Finding]:
    """``MISSING_VALUE``: R/C/D/L/Q/U component without a value.

    Heuristic (v1): passives/actives (refs whose kind starts with R, C,
    D, L, Q, U — resolved through :func:`_ref_class` so namespaced refs
    like ``motherboard/R18`` work) always need a value to be meaningful
    and purchaseable. Documented exceptions: connectors and
    mechanical/power symbols are not flagged — in particular the KiCad
    power-symbol pattern (pin 1 on a power net ``GND``/``VCC``) is
    skipped even when its ref starts with one of the prefixes above.
    """
    findings: list[Finding] = []
    for ref, component in sorted(design.components.items()):
        if component.value is not None:
            continue
        kind = _ref_class(ref)
        if kind is None or kind not in VALUE_REQUIRED_PREFIXES:
            continue  # connector / mechanical symbol: value often absent
        if component.pins.get("1") in POWER_NET_NAMES:
            continue  # power-symbol pattern (pin 1 on a power rail)
        findings.append(
            Finding(
                rule="MISSING_VALUE",
                severity="warning",
                message=f"component {ref} has no value (a {kind}-class device needs one)",
                evidence=[ref],
                position=source,
            )
        )
    return findings


def _missing_footprint(design: Design, source: str) -> list[Finding]:
    """``MISSING_FOOTPRINT``: component without footprint (error)."""
    findings: list[Finding] = []
    for ref, component in sorted(design.components.items()):
        if component.footprint is None:
            findings.append(
                Finding(
                    rule="MISSING_FOOTPRINT",
                    severity="error",
                    message=f"component {ref} has no footprint — the board is not manufacturable",
                    evidence=[ref],
                    position=source,
                )
            )
    return findings


def _no_driver(design: Design, source: str) -> list[Finding]:
    """``NO_DRIVER``: net made only of passives with no driver anywhere.

    Consultative (info) rule: a net whose connections are exclusively
    passive parts (reference classes R/C/L/D, resolved through
    :func:`_ref_class` so namespaced refs work), with no driver (classes
    U/J/Q) and not named like a power net (5V/VCC/GND), cannot source
    the energy it would need to do anything useful. Unknown reference
    prefixes (e.g. test points, power symbols) silence the rule.
    """
    findings: list[Finding] = []
    for net in sorted(design.nets.values(), key=lambda item: item.name):
        if not net.connections:
            continue
        if net.name.upper() in POWER_NET_NAMES:
            continue  # named power rail = source present
        refs = sorted({ref for ref, _ in net.connections})
        if not refs:
            continue
        classes = [_ref_class(ref) for ref in refs]
        passive_only = all(cls is not None and cls in PASSIVE_PREFIXES for cls in classes)
        has_driver = any(cls is not None and cls in DRIVER_PREFIXES for cls in classes)
        if passive_only and not has_driver:
            findings.append(
                Finding(
                    rule="NO_DRIVER",
                    severity="info",
                    message=(
                        f"net '{net.name}' connects only passive components "
                        f"({', '.join(refs)}); no active driver (U/J/Q) or power net found"
                    ),
                    evidence=[net.name, *refs],
                    position=source,
                )
            )
    return findings


def _led_no_limiter(design: Design, source: str) -> list[Finding]:
    """``LED_NO_LIMITER``: LED without a series resistor on any of its nets.

    The star rule of the auditor: an LED whose value mentions ``"LED"``
    and whose nets contain no pin of a series resistor (reference class
    ``"R"``, resolved through :func:`_ref_class` so namespaced refs like
    ``motherboard/R3`` count) has unbounded forward current — the part is
    destroyed in normal operation. Detection is purely structural:
    collect the nets of the LED's wired pins and look for a resistor pin
    among their connections. LEDs without pin-level connectivity
    (schematic-derived designs) are skipped.
    """
    findings: list[Finding] = []
    for ref, component in sorted(design.components.items()):
        if "led" not in (component.value or "").lower():
            continue
        led_nets = sorted({net for net in component.pins.values() if net is not None})
        if not led_nets:
            continue  # pin connectivity unresolved in this design
        has_limiter = any(
            _ref_class(other_ref) == "R"
            for net_name in led_nets
            if (net := design.nets.get(net_name)) is not None
            for other_ref, _ in net.connections
            if other_ref != ref
        )
        if has_limiter:
            continue
        nets_text = ", ".join(led_nets) if led_nets else "none"
        findings.append(
            Finding(
                rule="LED_NO_LIMITER",
                severity="warning",
                message=(
                    f"LED {ref} ('{component.value}') has no series resistor on any of "
                    f"its nets ({nets_text}) — the LED current is not limited"
                ),
                evidence=[ref, *led_nets],
                position=source,
            )
        )
    return findings


def _e_series_compliance(design: Design, source: str) -> list[Finding]:
    """``E_SERIES_COMPLIANCE``: R/C value is not a preferred E6/E12/E24/E96 number.

    Preferred numbers (IEC 60063) are the values the component industry
    actually manufactures and stocks; a hand-picked value such as ``333``
    (instead of the standard ``330``) means longer lead times, higher cost
    and often a substituted part. The check normalises the parsed value to
    its mantissa (1.0 <= m < 10) and compares it against the E6/E12/E24/E96
    rows with a 0.25 % tolerance (:func:`_is_e_series`): exact preferred
    numbers and decade multiples pass, non-standard mantissas fail. Values
    that cannot be parsed (empty, EIA code, exotic notation) are skipped —
    other rules own missing values.

    Technical justification: E12 (10 %) covers general-purpose parts, E24
    (5 %) the fine-tolerance ones and E96 (1 %) precision parts such as
    sense resistors and precision dividers; a value is accepted when it
    belongs to any of the four series. The 0.25 % tolerance keeps genuine
    E96 members (e.g. ``4.99k``, ``24.9k``, ``1.15nF``) while still
    flagging near-misses like ``333`` (0.30 % off the E96 step 3.32).
    The reference class is resolved through :func:`_ref_class`, so
    namespaced refs (``motherboard/R18``) are checked like plain ones.
    """
    findings: list[Finding] = []
    for ref, component in sorted(design.components.items()):
        kind = _ref_class(ref)
        if kind not in ("R", "C"):
            continue
        if component.value is None:
            continue  # MISSING_VALUE already reports the absent value
        if kind == "R":
            parsed = _parse_resistance(component.value)
            kind_label = "resistor"
        else:
            parsed = _parse_capacitance(component.value)
            kind_label = "capacitor"
        if parsed is None or parsed <= 0:
            continue  # unparseable (e.g. bare EIA code): cannot verify
        if _is_e_series(parsed):
            continue
        findings.append(
            Finding(
                rule="E_SERIES_COMPLIANCE",
                severity="warning",
                message=(
                    f"{kind_label} {ref} value '{component.value}' is not a preferred "
                    f"E6/E12/E24/E96 number (mantissa {_mantissa(parsed):.4g}); pick a "
                    "standard value (e.g. 330 instead of 333) for availability "
                    "and cost"
                ),
                evidence=[f"{ref}={component.value}"],
                position=source,
            )
        )
    return findings


def _led_series_resistor(design: Design, source: str) -> list[Finding]:
    """``LED_SERIES_RESISTOR``: LED series resistor that does not limit current.

    Extension of :func:`_led_no_limiter` for the case where a series
    resistor *is* present. Two failure modes are deterministic from the
    netlist value alone:

    - 0 ohm (``"0R"``, a solder bridge/jumper): a dead short, the LED
      current is bounded only by the supply impedance.
    - below ``LED_LIMITER_MIN_OHMS`` (~22 ohm): too weak to limit the
      current for a 3.3-5 V supply, where the expected order is >= 47 ohm
      (a 5 V rail with a 2 V LED drop needs ~150 ohm for 20 mA).

    Values of 22 ohm and above pass; the resistorless case stays with
    ``LED_NO_LIMITER`` (this rule is silent when no series resistor
    exists). Resistors without a value are skipped (MISSING_VALUE owns
    them) and unparseable values cannot be verified.
    """
    findings: list[Finding] = []
    for ref, component in sorted(design.components.items()):
        if "led" not in (component.value or "").lower():
            continue
        led_nets = sorted({net for net in component.pins.values() if net is not None})
        if not led_nets:
            continue  # pin connectivity unresolved in this design
        series_resistors = sorted(
            {
                other_ref
                for net_name in led_nets
                if (net := design.nets.get(net_name)) is not None
                for other_ref, _ in net.connections
                if other_ref != ref and _ref_class(other_ref) == "R"
            }
        )
        if not series_resistors:
            continue  # LED_NO_LIMITER owns the no-resistor case
        for resistor_ref in series_resistors:
            resistor = design.components.get(resistor_ref)
            if resistor is None or resistor.value is None:
                continue  # MISSING_VALUE already reports the absent value
            ohms = _parse_resistance(resistor.value)
            if ohms is None:
                continue
            if ohms == 0:
                message = (
                    f"LED {ref} series resistor {resistor_ref} is 0 ohm "
                    f"('{resistor.value}') — effectively a short: the LED current "
                    "is not limited"
                )
            elif ohms < LED_LIMITER_MIN_OHMS:
                message = (
                    f"LED {ref} series resistor {resistor_ref} is {ohms:g} ohm "
                    f"('{resistor.value}') — below a reasonable minimum "
                    f"(~{LED_LIMITER_MIN_OHMS:g} ohm) for a 3.3-5 V supply (expect "
                    ">= 47 ohm); the LED current may not be limited"
                )
            else:
                continue
            findings.append(
                Finding(
                    rule="LED_SERIES_RESISTOR",
                    severity="warning",
                    message=message,
                    evidence=[ref, f"{resistor_ref}={resistor.value}"],
                    position=source,
                )
            )
    return findings


def _cap_derating(design: Design, source: str) -> list[Finding]:
    """``CAP_DERATING``: electrolytic rating below 1.5x the rail voltage.

    Electrolytic capacitors (and MLCCs) lose effective capacitance under a
    DC bias and age faster near their rated voltage, so a common guideline
    is to keep the working voltage at or below 1/1.5 of the rating
    (``CAP_DERATING_FACTOR``). This is an *informational bonus control*: it
    only fires when the netlist carries both numbers — an explicit voltage
    in the value (``"10uF 50V"``) and a numeric power-net name (``5V``,
    ``3V3``, ``12V``). Capacitors without a rating, non-electrolytic parts
    (no uF/mF suffix and no electrolytic footprint) and rails whose voltage
    cannot be derived from the name (``VCC``/``VDD``) are silently skipped,
    so absence of data never produces a finding.

    Technical justification: the netlist value is the only place a voltage
    rating can appear in this data model; the 1.5x factor matches the
    checklist derating guideline for electrolytics.
    """
    findings: list[Finding] = []
    for ref, component in sorted(design.components.items()):
        if _ref_class(ref) != "C" or component.value is None:
            continue
        if not _is_electrolytic(component.value, component.footprint):
            continue
        rating = _parse_voltage(component.value)
        if rating is None:
            continue  # no explicit rating: nothing to compare
        for net_name in sorted(net for net in component.pins.values() if net is not None):
            rail = _rail_voltage(net_name)
            if rail is None:
                continue  # rail voltage not knowable from the net name
            if rating >= CAP_DERATING_FACTOR * rail:
                continue
            findings.append(
                Finding(
                    rule="CAP_DERATING",
                    severity="info",
                    message=(
                        f"electrolytic capacitor {ref} ('{component.value}') is rated "
                        f"{rating:g} V but sits on {net_name} ({rail:g} V); the 1.5x "
                        f"derating guideline needs at least "
                        f"{CAP_DERATING_FACTOR * rail:g} V"
                    ),
                    evidence=[ref, component.value, net_name],
                    position=source,
                )
            )
            break  # one finding per capacitor: first offending rail
    return findings


def _parse_resistance(text: str) -> float | None:
    """Parse a KiCad resistor value into ohms, or ``None`` if ambiguous.

    Supported notations: plain (``330``), unit suffix (``1k``, ``4.7k``,
    ``33R``, ``10M``, ``1Meg``), the EIA decimal-point form (``4R7`` =
    4.7 ohm) and a trailing tolerance token (``10k 1%``). ``R`` alone
    means ohms; ``M``/``Meg`` mean megaohm (milliohm values are not used
    as component values). Unsupported notations (``330R0``, non-numeric
    strings such as ``LED``) return ``None`` so callers stay silent.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    normalized = _STRIP_TOLERANCE_RE.sub("", text.strip())
    decimal = _RES_DECIMAL_RE.fullmatch(normalized)
    if decimal is not None:
        return float(f"{decimal.group(1)}.{decimal.group(2)}")
    match = _RES_VALUE_RE.fullmatch(normalized)
    if match is None:
        return None
    multipliers = {"": 1.0, "r": 1.0, "g": 1e9, "k": 1e3, "m": 1e6, "meg": 1e6}
    unit = (match.group(2) or "").lower()
    return float(match.group(1)) * multipliers[unit]


def _parse_capacitance(text: str) -> float | None:
    """Parse a KiCad capacitor value into farads, or ``None`` if ambiguous.

    Supported notations: micro/nano/pico/milli suffixes, with or without
    the ``F`` (``100nF``, ``47uF``, ``47µF``, ``1u``, ``22pF``, ``1mF``)
    and embedded in a larger string (``10uF 50V``, ``470uF/25V``). Bare
    numbers (EIA codes such as ``104``) return ``None`` because their
    meaning is ambiguous without the code convention.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    normalized = _STRIP_TOLERANCE_RE.sub("", text.strip())
    match = _CAP_VALUE_RE.search(normalized)
    if match is None:
        return None
    multipliers = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "m": 1e-3}
    return float(match.group(1)) * multipliers[match.group(2).lower()]


def _parse_voltage(text: str) -> float | None:
    """Parse the voltage rating embedded in a component value.

    Looks for a number immediately followed by ``V`` (``"10uF 50V"`` ->
    50.0, ``"6.3V"`` -> 6.3). Returns ``None`` when the value states no
    rating — the CAP_DERATING control needs this number to fire.
    """
    if not isinstance(text, str):
        return None
    match = _VOLTAGE_RE.search(text)
    if match is None:
        return None
    return float(match.group(1))


def _rail_voltage(net_name: str) -> float | None:
    """Voltage of a numerically named power rail, or ``None`` if unknown.

    Handles ``5V``/``3.3V``/``12V`` and the shorthand ``3V3``/``5V0``.
    Symbolic rails (``VCC``, ``VDD``, ``GND``) and signal nets return
    ``None``: their voltage cannot be derived from the name, so the
    derating comparison must not run.
    """
    name = (net_name or "").strip().upper()
    plain = _RAIL_PLAIN_RE.match(name)
    if plain is not None:
        return float(plain.group(1))
    short = _RAIL_SHORT_RE.match(name)
    if short is not None:
        return float(f"{short.group(1)}.{short.group(2)}")
    return None


def _mantissa(value: float) -> float:
    """Normalise a positive value to its mantissa (1.0 <= m < 10)."""
    return value / 10.0 ** math.floor(math.log10(value))


def _is_e_series(value: float, series: tuple[str, ...] = ("E6", "E12", "E24", "E96")) -> bool:
    """True if ``value`` is a preferred number of any requested E series.

    The value is normalised to its mantissa and compared against the union
    of the requested series' rows (:data:`E_SERIES_ROWS`) with a relative
    tolerance of :data:`E_SERIES_MATCH_TOLERANCE` (0.25 %: exact E96
    members pass, 0.3 %+ near-misses fail). Non-positive, boolean and
    non-finite inputs are never preferred numbers.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if not math.isfinite(value) or value <= 0:
        return False
    mantissa = _mantissa(value)
    steps = {step for name in series for step in E_SERIES_ROWS.get(name, ())}
    return any(abs(mantissa - step) / step <= E_SERIES_MATCH_TOLERANCE for step in steps)


def _is_electrolytic(value: str | None, footprint: str | None) -> bool:
    """True if value or footprint suggests an electrolytic capacitor.

    The value suffix (``uF``/``µF``/``mF``) is the primary signal; a
    footprint naming a polarised/electrolytic part (``CP_``, ``Elec``,
    ``Electrolytic``) is the fallback when the value uses another unit.
    """
    if value is not None and _ELECTROLYTIC_SUFFIX_RE.search(value):
        return True
    if footprint is not None:
        normalized = footprint.lower()
        if "cp_" in normalized or "elec" in normalized:
            return True
    return False


def _sort_key(finding: Finding) -> tuple[int, str, str]:
    """Deterministic sort key: severity rank, first evidence, rule id."""
    anchor = finding.evidence[0] if finding.evidence else finding.message
    return (SEVERITY_RANK[finding.severity], anchor, finding.rule)


def _summarize(findings: list[Finding], source: str) -> dict[str, object]:
    """Severity counts, total and the set of rules that fired."""
    counts = {"error": 0, "warning": 0, "info": 0}
    for finding in findings:
        counts[finding.severity] += 1
    return {
        "source": source,
        "total": len(findings),
        "errors": counts["error"],
        "warnings": counts["warning"],
        "infos": counts["info"],
        "rules": sorted({finding.rule for finding in findings}),
    }
