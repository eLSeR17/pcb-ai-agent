"""Deterministic firmware generation from validated template params.

``generate_firmware(spec)`` is the only entry point between the
agent layer (the LLM) and the template catalog. It never writes code
itself: it looks up the requested template, fails loudly on any contract
violation, renders the template with the caller's params and — when a
design path is given — *grounds* the params against the KiCad netlist
(anti-hallucination: refs/nets/pins that do not exist in the design are
rejected instead of being silently emitted).

Every artifact is born with ``requires_human_review=True``: in v1 nothing
leaves the generator considered flash-ready (see ``docs/FIRMWARE.md`` for
the human review gate).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pcbai.firmware.templates import (
    Template,
    get_template,
)
from pcbai.kicad.netlist import Design, parse_netlist

__all__ = [
    "FirmwareArtifact",
    "FirmwareSpec",
    "GroundingError",
    "TargetMismatchError",
    "generate_firmware",
]

#: Output filename per target (part of the artifact contract).
_FILENAMES: dict[str, str] = {"stm32-hal": "main.c", "arduino": "sketch.ino"}

#: Param keys that reference design objects (grounding contract).
_REF_KEY = "ref"
_REF_PIN_KEY = "ref_pin"
_NET_KEY = "net"


class GroundingError(ValueError):
    """A grounding param references something missing from the design."""


class TargetMismatchError(ValueError):
    """The spec target does not match the chosen template's target."""


@dataclass(frozen=True)
class FirmwareSpec:
    """What the agent wants generated.

    Attributes:
        target: ``"stm32-hal"`` or ``"arduino"`` — must match the chosen
            template's target.
        template: Name of a template in the catalog.
        params: Template parameters (validated by the template contract;
            ``ref``/``ref_pin``/``net`` are optional grounding hints).
        design_path: Path to a KiCad netlist to ground the params against;
            ``None`` skips grounding (pure code generation).
    """

    target: str
    template: str
    params: dict[str, Any]
    design_path: str | None = None


@dataclass(frozen=True)
class FirmwareArtifact:
    """A generated, grounded firmware artifact.

    Attributes:
        filename: Output filename for the target (``main.c``/``sketch.ino``).
        code: Rendered C/C++ (deterministic output of the template).
        target: The spec target (equals the template's target).
        template: The template name.
        params: Parameters as accepted by the template (a copy).
        grounding: ``"LED1 (ref)"``-style entries actually present in the
            design; empty when ``design_path`` was not provided.
        requires_human_review: Always ``True`` in v1 (human review gate).
        review_notes: Concrete items a human must check before flashing.
    """

    filename: str
    code: str
    target: str
    template: str
    params: dict[str, Any]
    grounding: list[str] = field(default_factory=list)
    requires_human_review: bool = True
    review_notes: list[str] = field(default_factory=list)


def generate_firmware(spec: FirmwareSpec) -> FirmwareArtifact:
    """Generate a firmware artifact from a validated spec.

    Steps:

    1. Resolve and validate the template (exists, target matches the spec).
    2. Render it with ``spec.params`` (the template validates the contract).
    3. If ``design_path`` is set, load the netlist and ground every
       ``ref``/``ref_pin``/``net`` hint — a missing object is an error.
    4. Build human-review notes and return the artifact.

    Raises:
        UnknownTemplateError: unknown template name.
        TargetMismatchError: spec target vs template target mismatch.
        TemplateParamError: params violate the template contract.
        GroundingError: a grounding hint is missing from the design or the
            design file cannot be loaded.
    """
    template = get_template(spec.template)
    if template.target != spec.target:
        raise TargetMismatchError(
            f"template {spec.template!r} targets {template.target!r} "
            f"but spec.target={spec.target!r}"
        )
    code = template.render(spec.params)

    design: Design | None = None
    if spec.design_path is not None:
        design = _load_design(spec.design_path)
    grounding = _ground(spec.params, design, spec.template) if design is not None else []
    notes = _review_notes(spec, template, grounding)

    return FirmwareArtifact(
        filename=_FILENAMES[spec.target],
        code=code,
        target=spec.target,
        template=spec.template,
        params=dict(spec.params),
        grounding=grounding,
        requires_human_review=True,
        review_notes=notes,
    )


def _load_design(path: str | Path) -> Design:
    """Parse a KiCad netlist design, wrapping every failure as GroundingError."""
    try:
        return parse_netlist(path)
    except FileNotFoundError:
        raise GroundingError(f"design file not found: {path!r}") from None
    except ValueError as exc:
        raise GroundingError(f"cannot load design {path!r}: {exc}") from None


def _ground(params: dict[str, Any], design: Design, template_name: str) -> list[str]:
    """Validate grounding hints against the design; return the evidence list.

    Only the documented grounding keys are considered (``ref``,
    ``ref_pin``, ``net``). Every referenced object must exist in the
    design; the returned list contains the entries that do, sorted for
    deterministic output.
    """
    ref = params.get(_REF_KEY)
    ref_pin = params.get(_REF_PIN_KEY)
    net = params.get(_NET_KEY)

    if ref_pin is not None and ref is None:
        raise GroundingError(f"template {template_name!r}: 'ref_pin' requires 'ref'")

    grounded: list[str] = []
    if ref is not None:
        ref_text = str(ref)
        component = design.components.get(ref_text)
        if component is None:
            raise GroundingError(
                f"template {template_name!r}: ref {ref!r} is not present in the design "
                f"(components: {sorted(design.components)!r})"
            )
        grounded.append(f"{ref_text} (ref)")
        if ref_pin is not None:
            pin_text = str(ref_pin)
            if pin_text not in component.pins:
                raise GroundingError(
                    f"template {template_name!r}: component {ref_text!r} has no pin "
                    f"{pin_text!r} (pins: {sorted(component.pins)!r})"
                )
            grounded.append(f"{ref_text} pin {pin_text} (pin)")
    if net is not None:
        net_text = str(net)
        if net_text not in design.nets:
            raise GroundingError(
                f"template {template_name!r}: net {net!r} is not present in the design "
                f"(nets: {sorted(design.nets)!r})"
            )
        grounded.append(f"{net_text} (net)")
    return sorted(grounded)


def _review_notes(spec: FirmwareSpec, template: Template, grounding: list[str]) -> list[str]:
    """Concrete, target-aware items for the human review gate."""
    notes: list[str] = [
        "Human review required before flashing: this artifact was generated "
        "from a fixed template and has NOT been compiled or tested on hardware.",
        "Static validation only - compile with your toolchain and verify pin "
        "assignments before flashing.",
    ]
    params = spec.params
    if spec.target == "stm32-hal":
        port = params.get("port")
        pin = params.get("pin")
        if isinstance(port, str) and isinstance(pin, str):
            notes.append(
                f"human check: verify {port}/{pin} matches the schematic net "
                "and is not used by another peripheral."
            )
        if template.category == "uart":
            baudrate = params.get("baudrate")
            if baudrate is not None:
                notes.append(f"human check: confirm baudrate {baudrate} matches the peer device.")
        elif template.category == "adc":
            notes.append(
                "human check: confirm the ADC input voltage range vs the VREF "
                "scaling used in the sample code."
            )
        elif template.category == "pwm":
            prescaler = params.get("prescaler")
            period = params.get("period")
            if prescaler is not None and period is not None:
                notes.append(
                    "human check: pwm frequency = timer_clock / "
                    f"({prescaler} + 1) / ({period} + 1); confirm it matches the "
                    "intended application."
                )
    else:
        pin = params.get("pin")
        if isinstance(pin, str):
            notes.append(f"human check: verify Arduino pin {pin} matches the physical wiring.")
        if template.category == "i2c":
            notes.append(
                "human check: confirm SDA/SCL pins and that pull-up resistors exist on the I2C bus."
            )
    if spec.design_path is not None and grounding:
        notes.append(f"grounded against design {spec.design_path}: {', '.join(grounding)}.")
        notes.append(
            "Grounding only proves the refs/nets/pins exist in the netlist - "
            "not electrical compatibility."
        )
    return notes
