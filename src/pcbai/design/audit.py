"""Evidence-based design audit over a parsed KiCad design.

Read-layer audit rules. Every finding is a :class:`Finding` dataclass
with a stable rule id, a severity, a specific human message and
``evidence`` — the concrete refs/nets of the design that triggered the
rule. This is the grounding contract of the project: **no finding without
evidence**, and no numeric or connectivity claim that does not come from
the parsed netlist.

Rule catalogue (v1):

- ``FLOATING_NET``     warning — a net with a single connection (likely
  left unterminated).
- ``UNCONNECTED_PIN``  warning — a component wired through exactly one
  pin (count heuristic: netlist pins are derived from nets, so a single
  wired pin on an otherwise multi-pin part means one or more pins are
  open).
- ``MISSING_VALUE``    warning — R/C/D/L/Q/U component with no value,
  except the power-symbol pattern (pin 1 on GND/VCC).
- ``MISSING_FOOTPRINT`` error  — component without footprint (the board is
  not manufacturable).
- ``NO_DRIVER``        info   — a net whose connections are only passives
  (refs starting with R/C/L/D) with no driver (ref starting with U/J/Q)
  and no power net name (5V/VCC/GND).
- ``LED_NO_LIMITER``   warning — an LED whose nets contain no pin of a
  series resistor: unbounded current, part will be destroyed.

Findings are returned severity-first (error > warning > info) and then in
alphabetical order of the first evidence entry and rule id, so the report
is fully deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass

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
    """Run every v1 audit rule over ``design``.

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
    findings.sort(key=_sort_key)
    return AuditReport(findings=findings, summary=_summarize(findings, source))


def _floating_net(design: Design, source: str) -> list[Finding]:
    """``FLOATING_NET``: a net with exactly one connection (warning)."""
    findings: list[Finding] = []
    for net in sorted(design.nets.values(), key=lambda item: item.name):
        if len(net.connections) != 1:
            continue
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


def _unconnected_pin(design: Design, source: str) -> list[Finding]:
    """``UNCONNECTED_PIN``: component wired through exactly one pin.

    Count heuristic (v1): in a netlist, ``component.pins`` is derived from
    the nets, so the number of wired pins is the only pin information
    available. A part wired through exactly one pin means one or more of
    its other pins are not connected to anything. Designs without
    pin-level connectivity (schematic-derived, all pin nets ``None``) are
    skipped because the parser cannot resolve pin connectivity there.
    """
    findings: list[Finding] = []
    for ref, component in sorted(design.components.items()):
        wired = [pin for pin, net in component.pins.items() if net is not None]
        if not wired:
            continue  # no pin-level connectivity in this design
        if len(wired) == 1:
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

    Heuristic (v1): passives/actives (refs starting with R, C, D, L, Q, U)
    always need a value to be meaningful and purchaseable. Documented
    exceptions: connectors and mechanical/power symbols are not flagged —
    in particular the KiCad power-symbol pattern (pin 1 on a power net
    ``GND``/``VCC``) is skipped even when its ref starts with one of the
    prefixes above.
    """
    findings: list[Finding] = []
    for ref, component in sorted(design.components.items()):
        if component.value is not None:
            continue
        if not ref.startswith(VALUE_REQUIRED_PREFIXES):
            continue  # connector / mechanical symbol: value often absent
        if component.pins.get("1") in POWER_NET_NAMES:
            continue  # power-symbol pattern (pin 1 on a power rail)
        findings.append(
            Finding(
                rule="MISSING_VALUE",
                severity="warning",
                message=f"component {ref} has no value (a {ref[0]}-class device needs one)",
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
    passive parts (refs starting with R/C/L/D), with no driver (ref
    starting with U/J/Q) and not named like a power net (5V/VCC/GND),
    cannot source the energy it would need to do anything useful. Unknown
    reference prefixes (e.g. test points) silence the rule.
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
        if all(ref[0] in PASSIVE_PREFIXES for ref in refs) and not any(
            ref[0] in DRIVER_PREFIXES for ref in refs
        ):
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
    and whose nets contain no pin of a series resistor (ref starting with
    ``"R"``) has unbounded forward current — the part is destroyed in
    normal operation. Detection is purely structural: collect the nets of
    the LED's wired pins and look for a resistor pin among their
    connections. LEDs without pin-level connectivity (schematic-derived
    designs) are skipped.
    """
    findings: list[Finding] = []
    for ref, component in sorted(design.components.items()):
        if "led" not in (component.value or "").lower():
            continue
        led_nets = sorted({net for net in component.pins.values() if net is not None})
        if not led_nets:
            continue  # pin connectivity unresolved in this design
        has_limiter = any(
            other_ref.startswith("R")
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
