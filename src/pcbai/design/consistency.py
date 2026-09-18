"""Netlist <-> schematic cross-check (design consistency layer).

The exported KiCad netlist is the source of truth for connectivity, but a
board is edited in the schematic. When the two drift apart a design can look
clean in the audit (which reads the netlist) while the drawing a human will
fabricate from no longer matches it. This module adds that missing read-layer
check: it compares the component identity of a parsed netlist and a parsed
schematic and reports the differences under the same evidence contract as
:mod:`pcbai.design.audit` — **no finding without a concrete ref**.

Scope (v1, deliberately conservative):

- Only component *identity* is compared: reference, value and footprint.
  Pin-level connectivity is never compared: the schematic parser cannot
  resolve pin nets without the KiCad symbol libraries, so ``pins`` is always
  ``None`` there and a comparison would be noise, not signal. Nets are not
  compared either (netlist nets carry pin connections, schematic nets are
  label-derived).
- Single-pin mechanical parts (mounting holes ``H``/``MH``, test points
  ``TP``, fiducials ``FID``) and KiCad power symbols (``#...``) are ignored:
  their footprint is a property of the board, not of the drawing, and KiCad
  frequently writes them empty.
- Unannotated references (``R?``, ``U?``) get a dedicated
  ``UNANNOTATED_REF`` warning. The netlist parser drops them (no stable
  identity), so a schematic placeholder would otherwise be misreported as
  ``MISSING_IN_NETLIST``.
- The check is pure and deterministic: no file I/O, no global state;
  findings are sorted with the audit severity order (error > warning > info).

Rule catalogue:

- ``MISSING_IN_SCHEMATIC`` warning — a netlist component with no schematic
  counterpart: the exported board is ahead of the drawing.
- ``MISSING_IN_NETLIST``    error   — a schematic component that never
  reached the netlist: the drawing is ahead of the exported board, the most
  dangerous drift (the board cannot be built as drawn).
- ``VALUE_MISMATCH``        warning — same ref, different value.
- ``FOOTPRINT_MISMATCH``    info    — same ref, different footprint.
- ``UNANNOTATED_REF``       warning — reference placeholder (``R?``).
- ``NO_SCHEMATIC``          info    — no schematic supplied, so the check
  cannot run (single informational finding).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pcbai.design.audit import SEVERITY_RANK, Finding
from pcbai.kicad.netlist import Component, Design

__all__ = ["CHECKS", "ConsistencyReport", "cross_check"]

#: Rule ids executed on every real cross-check (in report order). Used as the
#: ``checks_run`` value of a :class:`ConsistencyReport` when a schematic was
#: supplied, so a caller can tell "no findings" apart from "no check ran".
CHECKS: tuple[str, ...] = (
    "MISSING_IN_SCHEMATIC",
    "MISSING_IN_NETLIST",
    "VALUE_MISMATCH",
    "FOOTPRINT_MISMATCH",
    "UNANNOTATED_REF",
)

#: Reference kinds that are a property of the board rather than the drawing:
#: mounting holes (``H``/``MH``), test points (``TP``) and fiducials (``FID``).
#: Mirrors ``pcbai.design.audit.SINGLE_PIN_PART_KINDS``.
_IGNORED_REF_KINDS: frozenset[str] = frozenset({"H", "MH", "TP", "FID"})

#: Namespace separators of hierarchical references (``sheet1.R5``,
#: ``MB-R12``); same convention as :mod:`pcbai.design.audit`.
_REF_NAMESPACE_SPLIT_RE = re.compile(r"[/.\-]")

#: Leading alphabetic run of a reference kind (``R1`` -> ``"R"``).
_REF_KIND_RE = re.compile(r"[A-Za-z]+")


@dataclass(frozen=True)
class ConsistencyReport:
    """The result of :func:`cross_check`.

    Attributes:
        findings: Findings sorted by severity then evidence/rule id, using
            the same :class:`pcbai.design.audit.Finding` model (and therefore
            the same severity vocabulary and evidence contract).
        checks_run: Rule ids that were actually executed, so a caller can
            distinguish a clean report from a check that never ran.
    """

    findings: list[Finding]
    checks_run: list[str]


def cross_check(
    netlist: Design,
    schematic: Design | None,
    *,
    source: str = "cross-check",
) -> ConsistencyReport:
    """Compare a parsed netlist and a parsed schematic.

    ``netlist`` and ``schematic`` are already-parsed designs (see
    :func:`pcbai.kicad.netlist.parse_netlist` /
    :func:`pcbai.kicad.netlist.parse_schematic`); this function never reads
    files, so callers stay in control of I/O and error handling. Pass
    ``schematic=None`` when no drawing is available: the report then holds a
    single ``NO_SCHEMATIC`` info finding instead of pretending the design is
    consistent.

    ``source`` is carried into every finding's ``position`` field, matching
    :func:`pcbai.design.audit.audit_design`.
    """
    if schematic is None:
        return ConsistencyReport(
            findings=[
                Finding(
                    rule="NO_SCHEMATIC",
                    severity="info",
                    message=(
                        "no schematic supplied: cannot cross-check the netlist against the drawing"
                    ),
                    evidence=["schematic"],
                    position=source,
                )
            ],
            checks_run=["NO_SCHEMATIC"],
        )

    net_components = _comparable_components(netlist)
    schematic_components = _comparable_components(schematic)

    findings: list[Finding] = []
    findings.extend(_unannotated_refs(net_components, schematic_components, source))
    findings.extend(_missing_in_schematic(net_components, schematic_components, source))
    findings.extend(_missing_in_netlist(net_components, schematic_components, source))
    findings.extend(_attribute_mismatches(net_components, schematic_components, source))
    findings.sort(key=_sort_key)

    return ConsistencyReport(findings=findings, checks_run=list(CHECKS))


def _comparable_components(design: Design) -> dict[str, Component]:
    """Components that participate in the cross-check (ignored kinds dropped).

    Reference identity is stable enough to anchor evidence only for
    annotated refs, but unannotated placeholders are kept here so
    :func:`_unannotated_refs` can report them explicitly. Mechanical/power
    refs are dropped entirely (:data:`_IGNORED_REF_KINDS`).
    """
    return {ref: component for ref, component in design.components.items() if not _is_ignored(ref)}


def _unannotated_refs(
    net_components: dict[str, Component],
    schematic_components: dict[str, Component],
    source: str,
) -> list[Finding]:
    """``UNANNOTATED_REF`` for every ``R?``-style placeholder on either side.

    Reported once per placeholder, regardless of which side it appears on,
    and excluded from the missing-in-* checks (the netlist parser drops these
    refs, so they must never be misread as a netlist/drawing drift).
    """
    placeholders = sorted(
        ref for ref in set(net_components) | set(schematic_components) if _is_unannotated(ref)
    )
    return [
        Finding(
            rule="UNANNOTATED_REF",
            severity="warning",
            message=(
                f"reference {ref!r} is an unannotated KiCad placeholder; annotate "
                "the schematic before comparing it with the netlist"
            ),
            evidence=[ref],
            position=source,
        )
        for ref in placeholders
    ]


def _missing_in_schematic(
    net_components: dict[str, Component],
    schematic_components: dict[str, Component],
    source: str,
) -> list[Finding]:
    """Netlist components absent from the drawing (warning)."""
    findings: list[Finding] = []
    for ref in sorted(net_components):
        if _is_unannotated(ref) or ref in schematic_components:
            continue
        component = net_components[ref]
        detail = f" (value {component.value!r})" if component.value is not None else ""
        findings.append(
            Finding(
                rule="MISSING_IN_SCHEMATIC",
                severity="warning",
                message=(
                    f"component {ref}{detail} is in the netlist but missing from the schematic"
                ),
                evidence=[ref, "netlist"],
                position=source,
            )
        )
    return findings


def _missing_in_netlist(
    net_components: dict[str, Component],
    schematic_components: dict[str, Component],
    source: str,
) -> list[Finding]:
    """Schematic components that never reached the netlist (error)."""
    findings: list[Finding] = []
    for ref in sorted(schematic_components):
        if _is_unannotated(ref) or ref in net_components:
            continue
        component = schematic_components[ref]
        detail = f" (value {component.value!r})" if component.value is not None else ""
        findings.append(
            Finding(
                rule="MISSING_IN_NETLIST",
                severity="error",
                message=(
                    f"component {ref}{detail} is in the schematic but missing from "
                    "the netlist: export the netlist again before manufacturing"
                ),
                evidence=[ref, "schematic"],
                position=source,
            )
        )
    return findings


def _attribute_mismatches(
    net_components: dict[str, Component],
    schematic_components: dict[str, Component],
    source: str,
) -> list[Finding]:
    """Value (warning) and footprint (info) drift for refs present on both sides."""
    findings: list[Finding] = []
    for ref in sorted(set(net_components) & set(schematic_components)):
        if _is_unannotated(ref):
            continue
        net_component = net_components[ref]
        schematic_component = schematic_components[ref]
        if net_component.value != schematic_component.value:
            findings.append(
                Finding(
                    rule="VALUE_MISMATCH",
                    severity="warning",
                    message=(
                        f"component {ref} value differs: netlist "
                        f"{net_component.value!r} vs schematic "
                        f"{schematic_component.value!r}"
                    ),
                    evidence=[ref, f"netlist={net_component.value!r}"],
                    position=source,
                )
            )
        if net_component.footprint != schematic_component.footprint:
            findings.append(
                Finding(
                    rule="FOOTPRINT_MISMATCH",
                    severity="info",
                    message=(
                        f"component {ref} footprint differs: netlist "
                        f"{net_component.footprint!r} vs schematic "
                        f"{schematic_component.footprint!r}"
                    ),
                    evidence=[ref, f"netlist={net_component.footprint!r}"],
                    position=source,
                )
            )
    return findings


def _ref_kind(ref: str) -> str | None:
    """Leading alphabetic token of the final namespace segment of a ref.

    Same convention as :func:`pcbai.design.audit._ref_kind` (kept local so
    this module does not depend on another module's private helpers):
    ``motherboard/R18`` -> ``"R"``, ``sheet1.TP3`` -> ``"TP"``, ``#PWR01`` ->
    ``None``.
    """
    if not ref:
        return None
    token = _REF_NAMESPACE_SPLIT_RE.split(ref)[-1]
    match = _REF_KIND_RE.match(token)
    return match.group(0) if match is not None else None


def _is_unannotated(ref: str) -> bool:
    """True for KiCad's unannotated reference placeholder (``R?``)."""
    return ref.endswith("?")


def _is_ignored(ref: str) -> bool:
    """True for refs that are board properties, not drawing identities."""
    return ref.startswith("#") or _ref_kind(ref) in _IGNORED_REF_KINDS


def _sort_key(finding: Finding) -> tuple[int, str, str]:
    """Deterministic sort key: severity rank, first evidence, rule id."""
    anchor = finding.evidence[0] if finding.evidence else finding.message
    return (SEVERITY_RANK[finding.severity], anchor, finding.rule)
