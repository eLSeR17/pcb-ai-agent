"""Deterministic static validation of a generated firmware artifact.

This is a *sandbox-safe*, non-compiling sanity layer: it never executes or
compiles the code, it only inspects the text and the artifact metadata.
It exists to catch the failure modes that matter for template output
(unbalanced delimiters, missing includes, unknown HAL calls, malformed pin
names, template/target mismatches) — it is **not** a C parser and does not
replace compiling the code.

Checks performed (stable ``codes``):

- ``UNBALANCED_BRACES`` — ``{}``/``()``/``[]`` balancing with a small
  tokenizer that skips ``//`` and ``/* */`` comments and quoted strings.
- ``MISSING_INCLUDE`` — ``HAL_`` code without a ``"main.h"``/``"stm32*.h"``
  include is an error; ``pinMode``/``analogWrite`` outside a ``.ino`` file
  is a warning.
- ``HAL_FUNCTION_UNKNOWN`` — ``HAL_*``/``__HAL_*`` calls outside the
  documented whitelist are warnings.
- ``PIN_SCHEMA`` — pin/port params must be strings matching the target's
  documented pin grammar (warnings; custom boards may use other names).
- ``TEMPLATE_MISMATCH`` — the artifact target must equal the template's
  target; an unknown template is a warning.

Tokenizer limitations (documented, deliberate): it does not expand
preprocessor macros, does not understand C++ templates and treats any
``HAL_*``/``__HAL_*`` mention inside comments as a call reference.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pcbai.firmware.templates import UnknownTemplateError, get_template

__all__ = [
    "KNOWN_HAL_FUNCTIONS",
    "KNOWN_HAL_MACROS",
    "ValidationIssue",
    "ValidationReport",
    "validate_firmware",
]

#: Whitelist of HAL functions the validator recognises (short, documented).
#: Anything else that looks like a ``HAL_xxx(`` call raises a warning, not
#: an error: custom HAL extensions are legitimate.
KNOWN_HAL_FUNCTIONS: frozenset[str] = frozenset(
    {
        "HAL_Init",
        "HAL_Delay",
        "HAL_GPIO_Init",
        "HAL_GPIO_WritePin",
        "HAL_GPIO_ReadPin",
        "HAL_GPIO_TogglePin",
        "HAL_TIM_PWM_Init",
        "HAL_TIM_PWM_ConfigChannel",
        "HAL_TIM_PWM_Start",
        "HAL_TIM_PWM_Stop",
        "HAL_TIM_Base_Start",
        "HAL_ADC_Init",
        "HAL_ADC_ConfigChannel",
        "HAL_ADC_Start",
        "HAL_ADC_PollForConversion",
        "HAL_ADC_GetValue",
        "HAL_ADC_Stop",
        "HAL_UART_Init",
        "HAL_UART_DeInit",
        "HAL_UART_Transmit",
        "HAL_UART_Receive",
        "HAL_I2C_Init",
        "HAL_I2C_Master_Transmit",
        "HAL_I2C_Master_Receive",
    }
)

#: Whitelist of CMSIS ``__HAL_*`` macros used by the shipped templates
#: (clock enables + TIM helper macros).
KNOWN_HAL_MACROS: frozenset[str] = frozenset(
    [f"__HAL_RCC_GPIO{port}_CLK_ENABLE" for port in "ABCDEFGH"]
    + [f"__HAL_RCC_TIM{timer}_CLK_ENABLE" for timer in (*range(1, 9), *range(12, 18))]
    + [f"__HAL_RCC_ADC{adc}_CLK_ENABLE" for adc in (1, 2, 3)]
    + [f"__HAL_RCC_USART{u}_CLK_ENABLE" for u in (1, 2, 3)]
    + [f"__HAL_RCC_UART{u}_CLK_ENABLE" for u in (4, 5, 6, 7, 8)]
    + [
        "__HAL_TIM_SET_COMPARE",
        "__HAL_TIM_SET_AUTORELOAD",
        "__HAL_TIM_SET_PRESCALER",
        "__HAL_UART_ENABLE_IT",
    ]
)

#: Stable check ids (also the issue codes) in execution order.
_CHECKS: tuple[str, ...] = (
    "UNBALANCED_BRACES",
    "MISSING_INCLUDE",
    "HAL_FUNCTION_UNKNOWN",
    "PIN_SCHEMA",
    "TEMPLATE_MISMATCH",
)

_PAIRS: dict[str, str] = {"{": "}", "(": ")", "[": "]"}
_OPENERS: dict[str, str] = {close: open_ for open_, close in _PAIRS.items()}
_HAL_CALL_RE = re.compile(r"\b(?:__)?HAL_[A-Za-z0-9_]+(?=\s*\()")
_INCLUDE_RE = re.compile(r"#\s*include\s*[<\"]([^>\"]+)[>\"]")
_HAL_USE_RE = re.compile(r"\bHAL_")
_ARDUINO_API_RE = re.compile(r"\b(?:pinMode|analogWrite)\s*\(")
_STM32_PORT_RE = re.compile(r"GPIO[A-H]")
_STM32_PIN_RE = re.compile(r"(?:GPIO_PIN_[0-9]{1,2}|P[A-H][0-9]{1,2})")
_ARDUINO_PIN_RE = re.compile(r"(?:LED_BUILTIN|[0-9]{1,2}|A[0-9]{1,2}|D[0-9]{1,2})")

#: Pin-like param keys inspected by the PIN_SCHEMA check, per target.
_STM32_PORT_KEYS: tuple[str, ...] = ("port",)
_STM32_PIN_KEYS: tuple[str, ...] = ("pin", "rx_pin", "tx_pin")
_ARDUINO_PIN_KEYS: tuple[str, ...] = ("pin", "sda_pin", "scl_pin")


@dataclass(frozen=True)
class ValidationIssue:
    """A single validation finding.

    Attributes:
        severity: ``"error"`` (blocks ``ok``) or ``"warning"``.
        code: Stable check id (one of ``_CHECKS``).
        message: Human-readable explanation.
    """

    severity: str
    code: str
    message: str


@dataclass(frozen=True)
class ValidationReport:
    """The result of validating a firmware artifact.

    Attributes:
        ok: ``True`` when there are no ``error`` issues.
        issues: Findings in check execution order.
        checks_run: The stable list of checks that were run.
    """

    ok: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    checks_run: list[str] = field(default_factory=list)


def validate_firmware(artifact: object) -> ValidationReport:
    """Run the deterministic checks over an artifact and report findings.

    ``artifact`` is a :class:`pcbai.firmware.generator.FirmwareArtifact`
    (typed as ``object`` to keep the check functions independently
    testable with lightweight stand-ins). The code is never executed or
    compiled.
    """
    code = str(getattr(artifact, "code", ""))
    filename = str(getattr(artifact, "filename", ""))
    target = str(getattr(artifact, "target", ""))
    template_name = str(getattr(artifact, "template", ""))
    params = getattr(artifact, "params", {})

    issues: list[ValidationIssue] = []
    issues.extend(_check_balanced_braces(code))
    issues.extend(_check_includes(code, filename))
    issues.extend(_check_hal_calls(code))
    issues.extend(_check_pin_schema(params, target))
    issues.extend(_check_template_match(target, template_name))

    return ValidationReport(
        ok=not any(issue.severity == "error" for issue in issues),
        issues=issues,
        checks_run=list(_CHECKS),
    )


def _check_balanced_braces(code: str) -> list[ValidationIssue]:
    """Tokenize ``code`` and verify ``{}``/``()``/``[]`` balance.

    Skips ``//`` line comments, ``/* */`` block comments and single/double
    quoted strings (with backslash escapes). Preprocessor macros are not
    expanded — documented limitation.
    """
    stack: list[str] = []
    index = 0
    length = len(code)
    while index < length:
        char = code[index]
        nxt = code[index + 1] if index + 1 < length else ""
        if char == "/" and nxt == "/":
            while index < length and code[index] != "\n":
                index += 1
            continue
        if char == "/" and nxt == "*":
            index += 2
            while index + 1 < length and not (code[index] == "*" and code[index + 1] == "/"):
                index += 1
            index += 2
            continue
        if char in ('"', "'"):
            quote = char
            index += 1
            while index < length:
                if code[index] == "\\":
                    index += 2
                    continue
                if code[index] == quote:
                    break
                index += 1
            index += 1
            continue
        if char in _PAIRS:
            stack.append(char)
        elif char in _OPENERS:
            if not stack or stack[-1] != _OPENERS[char]:
                return [
                    ValidationIssue(
                        "error",
                        "UNBALANCED_BRACES",
                        f"unexpected {char!r} at offset {index} (no matching {_OPENERS[char]!r})",
                    )
                ]
            stack.pop()
        index += 1
    if stack:
        return [
            ValidationIssue(
                "error",
                "UNBALANCED_BRACES",
                f"{len(stack)} unclosed delimiter(s) at end of code: {''.join(stack)!r}",
            )
        ]
    return []


def _check_includes(code: str, filename: str) -> list[ValidationIssue]:
    """Verify HAL/Arduino includes are consistent with the code and filename."""
    issues: list[ValidationIssue] = []
    includes = _INCLUDE_RE.findall(code)
    if _HAL_USE_RE.search(code) and not any(
        name == "main.h" or (name.startswith("stm32") and name.endswith(".h")) for name in includes
    ):
        issues.append(
            ValidationIssue(
                "error",
                "MISSING_INCLUDE",
                'code uses HAL_* but includes neither "main.h" nor a "stm32*.h" '
                f"header (found: {includes!r})",
            )
        )
    if _ARDUINO_API_RE.search(code) and not filename.endswith(".ino"):
        issues.append(
            ValidationIssue(
                "warning",
                "MISSING_INCLUDE",
                f"Arduino API (pinMode/analogWrite) used in non-.ino file {filename!r}",
            )
        )
    return issues


def _check_hal_calls(code: str) -> list[ValidationIssue]:
    """Warn about HAL calls outside the documented function/macro whitelist."""
    issues: list[ValidationIssue] = []
    for name in sorted(set(_HAL_CALL_RE.findall(code))):
        if name in KNOWN_HAL_FUNCTIONS or name in KNOWN_HAL_MACROS:
            continue
        issues.append(
            ValidationIssue(
                "warning",
                "HAL_FUNCTION_UNKNOWN",
                f"{name}() is not in the documented HAL whitelist",
            )
        )
    return issues


def _check_pin_schema(params: Any, target: str) -> list[ValidationIssue]:
    """Check pin/port params against the target's documented pin grammar."""
    if not isinstance(params, dict):
        return [
            ValidationIssue("warning", "PIN_SCHEMA", "artifact params must be a dict to check pins")
        ]
    if target == "stm32-hal":
        specs = [(key, _STM32_PORT_RE) for key in _STM32_PORT_KEYS]
        specs += [(key, _STM32_PIN_RE) for key in _STM32_PIN_KEYS]
    elif target == "arduino":
        specs = [(key, _ARDUINO_PIN_RE) for key in _ARDUINO_PIN_KEYS]
    else:
        return [
            ValidationIssue(
                "warning",
                "PIN_SCHEMA",
                f"unknown target {target!r}: cannot check pin formats",
            )
        ]

    issues: list[ValidationIssue] = []
    for key, pattern in specs:
        if key not in params:
            continue
        value = params[key]
        if not isinstance(value, str):
            issues.append(
                ValidationIssue(
                    "warning",
                    "PIN_SCHEMA",
                    f"param {key!r} should be a string pin name, got {value!r}",
                )
            )
            continue
        if pattern.fullmatch(value) is None:
            issues.append(
                ValidationIssue(
                    "warning",
                    "PIN_SCHEMA",
                    f"param {key!r}={value!r} does not match {pattern.pattern!r} "
                    f"for target {target!r}",
                )
            )
    return issues


def _check_template_match(target: str, template_name: str) -> list[ValidationIssue]:
    """The artifact target must equal the template's declared target."""
    try:
        template = get_template(template_name)
    except UnknownTemplateError:
        return [
            ValidationIssue(
                "warning",
                "TEMPLATE_MISMATCH",
                f"unknown template {template_name!r}: cannot verify the artifact target",
            )
        ]
    if template.target != target:
        return [
            ValidationIssue(
                "error",
                "TEMPLATE_MISMATCH",
                f"artifact target {target!r} does not match template "
                f"{template_name!r}'s target {template.target!r}",
            )
        ]
    return []
