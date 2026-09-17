"""Tests for the deterministic firmware template catalog.

Every template must render valid code from valid params, reject any
contract violation before rendering, and — through the generator — produce
an artifact that passes the validator with zero issues.
"""

from __future__ import annotations

import pytest

from pcbai.firmware import (
    FirmwareSpec,
    generate_firmware,
    validate_firmware,
)
from pcbai.firmware.templates import (
    CATEGORIES,
    TARGETS,
    TEMPLATES,
    TemplateParamError,
    UnknownTemplateError,
    get_template,
    list_templates,
)

#: One valid parameter set per template (the catalog contract in practice).
VALID_PARAMS: dict[str, dict[str, object]] = {
    "stm32_gpio_output": {"port": "GPIOA", "pin": "GPIO_PIN_5", "label": "LED"},
    "stm32_pwm_timer": {
        "timer": "TIM2",
        "channel": "TIM_CHANNEL_1",
        "port": "GPIOA",
        "pin": "GPIO_PIN_0",
        "prescaler": 71,
        "period": 999,
        "pulse_percent": 50,
    },
    "stm32_adc_poll": {
        "adc": "ADC1",
        "channel": "ADC_CHANNEL_0",
        "port": "GPIOA",
        "pin": "GPIO_PIN_0",
        "vref_mv": 3300,
    },
    "stm32_uart_init": {"uart": "USART2", "baudrate": 115200},
    "arduino_gpio_output": {"pin": "13", "initial": "HIGH"},
    "arduino_pwm_analogwrite": {"pin": "9", "duty": 128},
    "arduino_adc_read": {"pin": "A0", "vref_mv": 5000},
    "arduino_i2c_scan": {"sda_pin": "A4", "scl_pin": "A5", "clock_hz": 100000},
}

#: A distinctive fragment proving the rendered code is the right template.
EXPECTED_MARKERS: dict[str, str] = {
    "stm32_gpio_output": "HAL_GPIO_WritePin",
    "stm32_pwm_timer": "HAL_TIM_PWM_Start",
    "stm32_adc_poll": "HAL_ADC_GetValue",
    "stm32_uart_init": "HAL_UART_Init",
    "arduino_gpio_output": "digitalWrite",
    "arduino_pwm_analogwrite": "analogWrite",
    "arduino_adc_read": "analogRead",
    "arduino_i2c_scan": "Wire.beginTransmission",
}


class TestCatalog:
    def test_catalog_has_eight_templates(self) -> None:
        assert len(TEMPLATES) == 8
        assert {template.name for template in TEMPLATES} == set(VALID_PARAMS)

    def test_catalog_metadata_is_valid(self) -> None:
        for template in TEMPLATES:
            assert template.target in TARGETS
            assert template.category in CATEGORIES
            assert template.description
            assert template.required_params
            assert set(template.required_params).isdisjoint(template.optional_params)

    def test_catalog_covers_four_stm32_and_four_arduino(self) -> None:
        targets = [template.target for template in TEMPLATES]
        assert targets.count("stm32-hal") == 4
        assert targets.count("arduino") == 4

    def test_valid_params_fixture_covers_every_template(self) -> None:
        assert set(EXPECTED_MARKERS) == set(VALID_PARAMS)

    def test_list_templates_payload(self) -> None:
        catalog = list_templates()
        assert [entry["name"] for entry in catalog] == [t.name for t in TEMPLATES]
        for entry in catalog:
            assert set(entry) == {
                "name",
                "target",
                "category",
                "required_params",
                "optional_params",
                "description",
            }
            assert isinstance(entry["required_params"], list)
            assert entry["target"] in TARGETS
            assert entry["category"] in CATEGORIES


class TestRender:
    @pytest.mark.parametrize(("name", "params"), VALID_PARAMS.items())
    def test_each_template_renders_expected_code(
        self, name: str, params: dict[str, object]
    ) -> None:
        code = get_template(name).render(dict(params))
        assert code.startswith("/*")
        assert EXPECTED_MARKERS[name] in code
        assert code.rstrip().endswith("}")

    @pytest.mark.parametrize(("name", "params"), VALID_PARAMS.items())
    def test_each_template_output_passes_the_validator(
        self, name: str, params: dict[str, object]
    ) -> None:
        template = get_template(name)
        artifact = generate_firmware(
            FirmwareSpec(target=template.target, template=name, params=dict(params))
        )
        report = validate_firmware(artifact)
        assert report.ok, report.issues
        assert report.issues == []

    @pytest.mark.parametrize(("name", "params"), VALID_PARAMS.items())
    def test_render_is_deterministic(self, name: str, params: dict[str, object]) -> None:
        template = get_template(name)
        assert template.render(dict(params)) == template.render(dict(params))

    def test_optional_params_override_defaults(self) -> None:
        code = get_template("stm32_gpio_output").render(
            {
                "port": "GPIOB",
                "pin": "GPIO_PIN_7",
                "mode": "GPIO_MODE_OUTPUT_OD",
                "pull": "GPIO_PULLUP",
                "initial_state": "GPIO_PIN_SET",
            }
        )
        assert "GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_OD;" in code
        assert "GPIO_InitStruct.Pull = GPIO_PULLUP;" in code
        assert "HAL_GPIO_WritePin(GPIOB, GPIO_PIN_7, GPIO_PIN_SET);" in code
        assert "__HAL_RCC_GPIOB_CLK_ENABLE();" in code

    def test_grounding_hints_are_rendered_as_a_comment(self) -> None:
        code = get_template("stm32_gpio_output").render(
            {
                "port": "GPIOA",
                "pin": "GPIO_PIN_5",
                "ref": "LED1",
                "ref_pin": "1",
                "net": "LED_A",
            }
        )
        assert "* Grounding: LED1 pin 1 on net LED_A" in code

    def test_hal_header_is_configurable(self) -> None:
        code = get_template("stm32_gpio_output").render(
            {"port": "GPIOA", "pin": "GPIO_PIN_5", "hal_header": "stm32f4xx_hal.h"}
        )
        assert '#include "stm32f4xx_hal.h"' in code


class TestParamErrors:
    @pytest.mark.parametrize(
        ("name", "removed"),
        [
            ("stm32_gpio_output", "pin"),
            ("stm32_pwm_timer", "timer"),
            ("stm32_adc_poll", "channel"),
            ("stm32_uart_init", "baudrate"),
            ("arduino_pwm_analogwrite", "duty"),
            ("arduino_i2c_scan", "scl_pin"),
        ],
    )
    def test_missing_required_param_raises(self, name: str, removed: str) -> None:
        params = {key: value for key, value in VALID_PARAMS[name].items() if key != removed}
        with pytest.raises(TemplateParamError, match=removed):
            get_template(name).render(params)

    def test_unexpected_param_raises(self) -> None:
        with pytest.raises(TemplateParamError, match="bogus"):
            get_template("arduino_gpio_output").render({"pin": "13", "bogus": True})

    def test_unknown_template_raises(self) -> None:
        with pytest.raises(UnknownTemplateError, match="available"):
            get_template("does_not_exist")

    def test_invalid_choice_param_raises(self) -> None:
        with pytest.raises(TemplateParamError, match="port"):
            get_template("stm32_gpio_output").render({"port": "GPIOX", "pin": "GPIO_PIN_5"})
        with pytest.raises(TemplateParamError, match="initial"):
            get_template("arduino_gpio_output").render({"pin": "13", "initial": "MAYBE"})

    @pytest.mark.parametrize(
        ("name", "params", "key"),
        [
            ("stm32_pwm_timer", {"prescaler": -1}, "prescaler"),
            ("stm32_pwm_timer", {"prescaler": 1.5}, "prescaler"),
            ("arduino_pwm_analogwrite", {"duty": 256}, "duty"),
            ("stm32_uart_init", {"baudrate": 0}, "baudrate"),
            ("arduino_i2c_scan", {"clock_hz": 0}, "clock_hz"),
        ],
    )
    def test_out_of_range_or_wrong_type_raises(
        self, name: str, params: dict[str, object], key: str
    ) -> None:
        merged = {**VALID_PARAMS[name], **params}
        with pytest.raises(TemplateParamError, match=key):
            get_template(name).render(merged)

    @pytest.mark.parametrize(
        ("name", "key", "value"),
        [
            ("stm32_gpio_output", "pin", "PA5"),
            ("stm32_adc_poll", "pin", 5),
            ("arduino_gpio_output", "pin", "Z42"),
            ("arduino_i2c_scan", "sda_pin", "pin-4"),
        ],
    )
    def test_invalid_pin_format_raises(self, name: str, key: str, value: object) -> None:
        merged = {**VALID_PARAMS[name], key: value}
        with pytest.raises(TemplateParamError, match=key):
            get_template(name).render(merged)

    def test_invalid_hal_header_raises(self) -> None:
        with pytest.raises(TemplateParamError, match="hal_header"):
            get_template("stm32_gpio_output").render(
                {"port": "GPIOA", "pin": "GPIO_PIN_5", "hal_header": "not_a_header"}
            )
