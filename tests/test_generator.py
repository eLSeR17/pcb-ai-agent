"""Tests for the deterministic firmware generator.

The generator only renders catalog templates and grounds the params
against a real KiCad netlist: refs/nets/pins that do not exist in the
design must be rejected instead of silently embedded in the code.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pcbai.firmware import (
    FirmwareSpec,
    GroundingError,
    TargetMismatchError,
    TemplateParamError,
    UnknownTemplateError,
    generate_firmware,
)

FIXTURES = Path(__file__).parent / "fixtures"
SIMPLE_LED = FIXTURES / "simple-led.kicad_net"

_GROUNDED_PARAMS: dict[str, object] = {
    "port": "GPIOA",
    "pin": "GPIO_PIN_5",
    "ref": "LED1",
    "ref_pin": "1",
    "net": "LED_A",
}


def _spec(
    template: str = "stm32_gpio_output",
    target: str = "stm32-hal",
    params: dict[str, object] | None = None,
    design_path: Path | None = None,
) -> FirmwareSpec:
    return FirmwareSpec(
        target=target,
        template=template,
        params=dict(_GROUNDED_PARAMS) if params is None else params,
        design_path=str(design_path) if design_path is not None else None,
    )


class TestGrounding:
    def test_grounds_stm32_params_against_the_netlist(self) -> None:
        artifact = generate_firmware(_spec(design_path=SIMPLE_LED))
        assert artifact.grounding == ["LED1 (ref)", "LED1 pin 1 (pin)", "LED_A (net)"]
        assert artifact.filename == "main.c"
        assert artifact.target == "stm32-hal"
        assert artifact.template == "stm32_gpio_output"

    def test_grounding_works_for_arduino_targets_too(self) -> None:
        artifact = generate_firmware(
            _spec(
                template="arduino_gpio_output",
                target="arduino",
                params={"pin": "13", "ref": "LED1", "net": "LED_A"},
                design_path=SIMPLE_LED,
            )
        )
        assert artifact.grounding == ["LED1 (ref)", "LED_A (net)"]
        assert artifact.filename == "sketch.ino"

    def test_grounding_hint_is_embedded_in_the_code(self) -> None:
        artifact = generate_firmware(_spec(design_path=SIMPLE_LED))
        assert "Grounding: LED1 pin 1 on net LED_A" in artifact.code

    def test_no_design_path_means_no_grounding(self) -> None:
        artifact = generate_firmware(_spec())
        assert artifact.grounding == []
        assert "HAL_GPIO_WritePin" in artifact.code

    def test_unknown_ref_raises(self) -> None:
        with pytest.raises(GroundingError, match="ref 'U99' is not present"):
            generate_firmware(
                _spec(params={**_GROUNDED_PARAMS, "ref": "U99"}, design_path=SIMPLE_LED)
            )

    def test_unknown_pin_raises(self) -> None:
        with pytest.raises(GroundingError, match="has no pin '9'"):
            generate_firmware(
                _spec(params={**_GROUNDED_PARAMS, "ref_pin": "9"}, design_path=SIMPLE_LED)
            )

    def test_unknown_net_raises(self) -> None:
        with pytest.raises(GroundingError, match="net 'NOT_A_NET' is not present"):
            generate_firmware(
                _spec(params={**_GROUNDED_PARAMS, "net": "NOT_A_NET"}, design_path=SIMPLE_LED)
            )

    def test_ref_pin_without_ref_raises(self) -> None:
        params = {"port": "GPIOA", "pin": "GPIO_PIN_5", "ref_pin": "1"}
        with pytest.raises(GroundingError, match="requires 'ref'"):
            generate_firmware(_spec(params=params, design_path=SIMPLE_LED))

    def test_missing_design_file_raises(self) -> None:
        with pytest.raises(GroundingError, match="not found"):
            generate_firmware(_spec(design_path=FIXTURES / "does-not-exist.kicad_net"))

    def test_unparseable_design_raises_grounding_error(self, tmp_path: Path) -> None:
        broken = tmp_path / "broken.kicad_net"
        broken.write_text("this is not an s-expression", encoding="utf-8")
        with pytest.raises(GroundingError, match="cannot load design"):
            generate_firmware(_spec(design_path=broken))


class TestSpecValidation:
    def test_target_mismatch_raises(self) -> None:
        with pytest.raises(TargetMismatchError, match="arduino"):
            generate_firmware(_spec(target="arduino"))

    def test_unknown_template_raises(self) -> None:
        with pytest.raises(UnknownTemplateError, match="available"):
            generate_firmware(_spec(template="does_not_exist"))

    def test_template_param_errors_propagate(self) -> None:
        with pytest.raises(TemplateParamError, match="missing required"):
            generate_firmware(_spec(params={"port": "GPIOA"}))  # 'pin' is required


class TestArtifact:
    def test_requires_human_review_always(self) -> None:
        artifact = generate_firmware(_spec())
        assert artifact.requires_human_review is True

    def test_review_notes_are_concrete(self) -> None:
        artifact = generate_firmware(_spec(design_path=SIMPLE_LED))
        assert artifact.review_notes
        joined = " ".join(artifact.review_notes)
        assert "review" in joined
        assert "GPIOA/GPIO_PIN_5" in joined
        assert "LED1 (ref), LED1 pin 1 (pin), LED_A (net)" in joined

    def test_params_are_copied(self) -> None:
        params = dict(_GROUNDED_PARAMS)
        artifact = generate_firmware(_spec(params=params))
        params["pin"] = "MUTATED"
        assert artifact.params["pin"] == "GPIO_PIN_5"

    def test_generation_is_deterministic(self) -> None:
        first = generate_firmware(_spec(design_path=SIMPLE_LED))
        second = generate_firmware(_spec(design_path=SIMPLE_LED))
        assert first.code == second.code
        assert first.grounding == second.grounding
        assert first.review_notes == second.review_notes
