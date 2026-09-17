"""Tests for the deterministic firmware validator.

The validator is a non-compiling static sanity layer. These tests fix the
contract: a generated artifact is clean, and each concrete mutation
(unbalanced delimiter, missing include, unknown HAL call, malformed pin,
target mismatch) is reported with the documented code and severity.
"""

from __future__ import annotations

from pcbai.firmware import (
    FirmwareArtifact,
    FirmwareSpec,
    generate_firmware,
    validate_firmware,
)
from pcbai.firmware.validator import KNOWN_HAL_FUNCTIONS, KNOWN_HAL_MACROS

STM32_CODE = generate_firmware(
    FirmwareSpec(
        target="stm32-hal",
        template="stm32_gpio_output",
        params={"port": "GPIOA", "pin": "GPIO_PIN_5"},
    )
).code
ARDUINO_CODE = generate_firmware(
    FirmwareSpec(target="arduino", template="arduino_gpio_output", params={"pin": "13"})
).code

EXPECTED_CHECKS = [
    "UNBALANCED_BRACES",
    "MISSING_INCLUDE",
    "HAL_FUNCTION_UNKNOWN",
    "PIN_SCHEMA",
    "TEMPLATE_MISMATCH",
]


def _artifact(
    code: str = STM32_CODE,
    target: str = "stm32-hal",
    template: str = "stm32_gpio_output",
    params: object = None,
    filename: str = "main.c",
) -> FirmwareArtifact:
    """Build an artifact for validator tests (defaults = a valid STM32 one)."""
    return FirmwareArtifact(
        filename=filename,
        code=code,
        target=target,
        template=template,
        params={"port": "GPIOA", "pin": "GPIO_PIN_5"} if params is None else params,  # type: ignore[arg-type]
    )


def _codes(report: object) -> list[str]:
    return [issue.code for issue in report.issues]  # type: ignore[attr-defined]


class TestValidArtifacts:
    def test_generated_stm32_artifact_is_clean(self) -> None:
        report = validate_firmware(_artifact())
        assert report.ok is True
        assert report.issues == []

    def test_generated_arduino_artifact_is_clean(self) -> None:
        report = validate_firmware(
            _artifact(
                code=ARDUINO_CODE,
                target="arduino",
                template="arduino_gpio_output",
                params={"pin": "13"},
                filename="sketch.ino",
            )
        )
        assert report.ok is True
        assert report.issues == []

    def test_checks_run_lists_the_five_checks(self) -> None:
        assert validate_firmware(_artifact()).checks_run == EXPECTED_CHECKS

    def test_comments_and_strings_do_not_break_balance(self) -> None:
        code = '/* { ( [ */\nvoid f(void)\n{\n    const char *s = "}";\n}\n'
        report = validate_firmware(_artifact(code=code))
        assert "UNBALANCED_BRACES" not in _codes(report)
        assert report.ok is True


class TestStructuralChecks:
    def test_unbalanced_braces_detected(self) -> None:
        code = "".join(STM32_CODE.rsplit("}", 1))  # drop the final closing brace
        report = validate_firmware(_artifact(code=code))
        assert report.ok is False
        assert _codes(report) == ["UNBALANCED_BRACES"]
        assert report.issues[0].severity == "error"

    def test_unexpected_closing_delimiter_detected(self) -> None:
        report = validate_firmware(_artifact(code=STM32_CODE + ")\n"))
        assert report.ok is False
        assert _codes(report) == ["UNBALANCED_BRACES"]

    def test_missing_include_is_an_error(self) -> None:
        code = "\n".join(
            line for line in STM32_CODE.splitlines() if not line.lstrip().startswith("#include")
        )
        report = validate_firmware(_artifact(code=code))
        assert report.ok is False
        assert "MISSING_INCLUDE" in _codes(report)

    def test_arduino_api_outside_ino_is_a_warning(self) -> None:
        code = "void setup()\n{\n    pinMode(13, OUTPUT);\n}\n"
        report = validate_firmware(
            _artifact(
                code=code,
                target="arduino",
                template="arduino_gpio_output",
                params={"pin": "13"},
                filename="main.c",
            )
        )
        assert report.ok is True
        assert _codes(report) == ["MISSING_INCLUDE"]
        assert report.issues[0].severity == "warning"

    def test_unknown_hal_call_is_a_warning(self) -> None:
        code = STM32_CODE + "\nvoid extra(void)\n{\n    HAL_BOGUS_Thing();\n}\n"
        report = validate_firmware(_artifact(code=code))
        assert report.ok is True
        assert _codes(report) == ["HAL_FUNCTION_UNKNOWN"]

    def test_whitelisted_hal_calls_do_not_warn(self) -> None:
        report = validate_firmware(_artifact())
        assert "HAL_FUNCTION_UNKNOWN" not in _codes(report)
        assert "HAL_GPIO_WritePin" in KNOWN_HAL_FUNCTIONS
        assert "__HAL_RCC_GPIOA_CLK_ENABLE" in KNOWN_HAL_MACROS


class TestMetadataChecks:
    def test_target_mismatch_is_an_error(self) -> None:
        report = validate_firmware(_artifact(target="arduino", params={"pin": "13"}))
        assert report.ok is False
        assert _codes(report) == ["TEMPLATE_MISMATCH"]
        assert report.issues[0].severity == "error"

    def test_unknown_template_is_a_warning(self) -> None:
        report = validate_firmware(_artifact(template="does_not_exist"))
        assert report.ok is True
        assert _codes(report) == ["TEMPLATE_MISMATCH"]
        assert report.issues[0].severity == "warning"

    def test_bad_stm32_pin_schema_warns(self) -> None:
        report = validate_firmware(_artifact(params={"port": "GPIOZ", "pin": "NOT_A_PIN"}))
        assert report.ok is True
        assert _codes(report) == ["PIN_SCHEMA", "PIN_SCHEMA"]

    def test_bad_arduino_pin_schema_warns(self) -> None:
        report = validate_firmware(
            _artifact(
                code=ARDUINO_CODE,
                target="arduino",
                template="arduino_gpio_output",
                params={"pin": "Z42"},
                filename="sketch.ino",
            )
        )
        assert _codes(report) == ["PIN_SCHEMA"]

    def test_non_dict_params_warns(self) -> None:
        report = validate_firmware(_artifact(params="not-a-dict"))
        assert _codes(report) == ["PIN_SCHEMA"]

    def test_severities_and_codes_are_known(self) -> None:
        report = validate_firmware(_artifact(target="arduino", template="does_not_exist"))
        for issue in report.issues:
            assert issue.severity in {"error", "warning"}
            assert issue.code in EXPECTED_CHECKS
            assert issue.message

    def test_validator_is_deterministic(self) -> None:
        artifact = _artifact(code=STM32_CODE + ")\n")
        assert validate_firmware(artifact) == validate_firmware(artifact)
