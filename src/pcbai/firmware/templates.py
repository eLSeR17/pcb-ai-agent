"""Deterministic firmware template catalog (STM32 HAL / Arduino).

The core invariant of pcb-ai-agent: **the LLM never writes
firmware freely** — it picks a template plus parameters, and the
generator only renders the chosen template with those parameters. This
module is that catalog: eight fixed templates (four STM32 HAL, four
Arduino), each with a documented parameter contract, strict parameter
validation *before* rendering (no partial output) and a deterministic
``render()``.

Templates shipped:

===============  ==========  ==========  ======================================
name             target      category    purpose
===============  ==========  ==========  ======================================
stm32_gpio_output  stm32-hal  gpio        GPIO output init (pin mode + level)
stm32_pwm_timer    stm32-hal  pwm         TIM PWM channel init (PSC/ARR)
stm32_adc_poll     stm32-hal  adc         ADC polled conversion init + read
stm32_uart_init    stm32-hal  uart        UART/USART init at a baudrate
arduino_gpio_output  arduino   gpio        pinMode OUTPUT + digitalWrite
arduino_pwm_analogwrite  arduino  pwm     analogWrite() PWM duty
arduino_adc_read   arduino    adc         analogRead() + millivolt scaling
arduino_i2c_scan   arduino    i2c         forward I2C bus scanner (Wire)
===============  ==========  ==========  ======================================

Every ``render()`` call is pure and deterministic: the same params
produce byte-identical code. Parameter validation is strict — a missing
required param, an unexpected param or a value outside the documented
range raises :class:`TemplateParamError` without producing any output.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

__all__ = [
    "CATEGORIES",
    "TARGETS",
    "TEMPLATES",
    "Template",
    "TemplateError",
    "TemplateParamError",
    "UnknownTemplateError",
    "get_template",
    "list_templates",
]

# --------------------------------------------------------------------------- #
# Domain vocabulary
# --------------------------------------------------------------------------- #

#: Supported firmware targets.
TARGETS: tuple[str, ...] = ("stm32-hal", "arduino")

#: Supported template categories.
CATEGORIES: tuple[str, ...] = ("gpio", "pwm", "adc", "uart", "i2c")

#: Optional params shared by every template (grounding hints + label).
#: ``ref``/``net``/``ref_pin`` name objects of the KiCad design that justify
#: the choice of pins; the generator validates them against the netlist
#: when ``design_path`` is set.
_COMMON_OPTIONAL_PARAMS: tuple[str, ...] = ("label", "ref", "ref_pin", "net")


class TemplateError(ValueError):
    """Base class for template catalog errors."""


class UnknownTemplateError(TemplateError):
    """Raised when a template name is not in the catalog."""


class TemplateParamError(TemplateError):
    """Raised when parameters violate the template contract (no render)."""


# --------------------------------------------------------------------------- #
# Parameter validation helpers (shared by all renderers)
# --------------------------------------------------------------------------- #

_GPIO_PORTS: tuple[str, ...] = (
    "GPIOA",
    "GPIOB",
    "GPIOC",
    "GPIOD",
    "GPIOE",
    "GPIOF",
    "GPIOG",
    "GPIOH",
)
_GPIO_PIN_RE = re.compile(r"GPIO_PIN_[0-9]{1,2}")
_GPIO_MODES: tuple[str, ...] = (
    "GPIO_MODE_OUTPUT_PP",
    "GPIO_MODE_OUTPUT_OD",
    "GPIO_MODE_INPUT",
    "GPIO_MODE_AF_PP",
    "GPIO_MODE_AF_OD",
    "GPIO_MODE_ANALOG",
)
_GPIO_PULLS: tuple[str, ...] = ("GPIO_NOPULL", "GPIO_PULLUP", "GPIO_PULLDOWN")
_GPIO_SPEEDS: tuple[str, ...] = (
    "GPIO_SPEED_FREQ_LOW",
    "GPIO_SPEED_FREQ_MEDIUM",
    "GPIO_SPEED_FREQ_HIGH",
    "GPIO_SPEED_FREQ_VERY_HIGH",
)
_GPIO_LEVELS: tuple[str, ...] = ("GPIO_PIN_RESET", "GPIO_PIN_SET")
_TIMERS: tuple[str, ...] = (
    "TIM1",
    "TIM2",
    "TIM3",
    "TIM4",
    "TIM5",
    "TIM6",
    "TIM7",
    "TIM8",
    "TIM12",
    "TIM13",
    "TIM14",
    "TIM15",
    "TIM16",
    "TIM17",
)
_TIM_CHANNELS: tuple[str, ...] = (
    "TIM_CHANNEL_1",
    "TIM_CHANNEL_2",
    "TIM_CHANNEL_3",
    "TIM_CHANNEL_4",
)
_ADCS: tuple[str, ...] = ("ADC1", "ADC2", "ADC3")
_ADC_CHANNEL_RE = re.compile(r"ADC_CHANNEL_[0-9]{1,2}")
_UARTS: tuple[str, ...] = (
    "USART1",
    "USART2",
    "USART3",
    "UART4",
    "UART5",
    "UART6",
    "UART7",
    "UART8",
)
_UART_WORD_LENGTHS: tuple[str, ...] = ("UART_WORDLENGTH_8B", "UART_WORDLENGTH_9B")
_UART_PARITIES: tuple[str, ...] = (
    "UART_PARITY_NONE",
    "UART_PARITY_EVEN",
    "UART_PARITY_ODD",
)
_UART_STOP_BITS: tuple[str, ...] = (
    "UART_STOPBITS_1",
    "UART_STOPBITS_1_5",
    "UART_STOPBITS_2",
)
_ARDUINO_PIN_RE = re.compile(r"(?:LED_BUILTIN|[0-9]{1,2}|A[0-9]{1,2}|D[0-9]{1,2})")
_ARDUINO_LEVELS: tuple[str, ...] = ("LOW", "HIGH")
_HAL_HEADER_RE = re.compile(r"stm32[a-z0-9_]*\.h")


def _int_param(
    template: str,
    params: dict[str, Any],
    key: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
    default: int | None = None,
) -> int:
    """Read an integer param, coercing ``"42"``/``42.0`` and range-checking."""
    if key not in params:
        if default is None:
            raise TemplateParamError(f"template {template!r}: missing parameter {key!r}")
        return default
    raw = params[key]
    if isinstance(raw, bool):
        value: int | None = None
    elif isinstance(raw, int):
        value = raw
    elif (isinstance(raw, str) and raw.lstrip("-").isdigit()) or (
        isinstance(raw, float) and raw.is_integer()
    ):
        value = int(raw)
    else:
        value = None
    if value is None:
        raise TemplateParamError(
            f"template {template!r}: parameter {key!r} must be an integer, got {raw!r}"
        )
    if minimum is not None and value < minimum:
        raise TemplateParamError(
            f"template {template!r}: parameter {key!r} must be >= {minimum}, got {value}"
        )
    if maximum is not None and value > maximum:
        raise TemplateParamError(
            f"template {template!r}: parameter {key!r} must be <= {maximum}, got {value}"
        )
    return value


def _float_param(
    template: str,
    params: dict[str, Any],
    key: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    default: float | None = None,
) -> float:
    """Read a float param, coercing ``"50"``/``50`` and range-checking."""
    if key not in params:
        if default is None:
            raise TemplateParamError(f"template {template!r}: missing parameter {key!r}")
        return default
    raw = params[key]
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise TemplateParamError(
            f"template {template!r}: parameter {key!r} must be a number, got {raw!r}"
        ) from None
    if minimum is not None and value < minimum:
        raise TemplateParamError(
            f"template {template!r}: parameter {key!r} must be >= {minimum}, got {value}"
        )
    if maximum is not None and value > maximum:
        raise TemplateParamError(
            f"template {template!r}: parameter {key!r} must be <= {maximum}, got {value}"
        )
    return value


def _choice_param(template: str, params: dict[str, Any], key: str, allowed: tuple[str, ...]) -> str:
    """Read a string param that must be one of ``allowed`` (no default)."""
    raw = params[key]
    if not isinstance(raw, str) or raw not in allowed:
        raise TemplateParamError(
            f"template {template!r}: parameter {key!r} must be one of "
            f"{', '.join(allowed)}, got {raw!r}"
        )
    return raw


def _choice_param_default(
    template: str,
    params: dict[str, Any],
    key: str,
    allowed: tuple[str, ...],
    default: str,
) -> str:
    """Read a string param from ``allowed``, falling back to ``default``."""
    if key not in params:
        return default
    return _choice_param(template, params, key, allowed)


def _pattern_param(
    template: str, params: dict[str, Any], key: str, pattern: re.Pattern[str]
) -> str:
    """Read a string param that must fully match ``pattern`` (no default)."""
    raw = params[key]
    if not isinstance(raw, str) or pattern.fullmatch(raw) is None:
        raise TemplateParamError(
            f"template {template!r}: parameter {key!r} must match {pattern.pattern!r}, got {raw!r}"
        )
    return raw


def _hal_header_param(template: str, params: dict[str, Any]) -> str:
    """Read the optional HAL include name (e.g. ``stm32f1xx_hal.h``)."""
    header = params.get("hal_header", "stm32f1xx_hal.h")
    if not isinstance(header, str) or _HAL_HEADER_RE.fullmatch(header) is None:
        raise TemplateParamError(
            f"template {template!r}: parameter 'hal_header' must match "
            f"{_HAL_HEADER_RE.pattern!r}, got {header!r}"
        )
    return header


def _label_comment(params: dict[str, Any]) -> str:
    """A trailing ``/* ... */`` comment for the optional ``label`` param."""
    label = params.get("label")
    if not isinstance(label, str) or not label:
        return ""
    return f"\n\n    /* {label} */"


def _grounding_comment(params: dict[str, Any]) -> str:
    """One `` * Grounding: ...`` comment line from the ref/net hints."""
    ref = params.get("ref")
    net = params.get("net")
    ref_text = ""
    if ref is not None:
        ref_text = str(ref)
        ref_pin = params.get("ref_pin")
        if ref_pin is not None:
            ref_text += f" pin {ref_pin}"
    net_text = f"net {net}" if net is not None else ""
    phrase = f"{ref_text} on {net_text}" if ref_text and net_text else ref_text or net_text
    return f" * Grounding: {phrase}" if phrase else ""


def _generated_header(template: str, params: dict[str, Any]) -> str:
    """The block comment that opens every generated file (didactic)."""
    lines = [
        "/*",
        f" * GENERATED by pcb-ai-agent - template {template}.",
        " * Deterministic template output - review before flashing.",
        " * Human review gate: not compiled, not tested on hardware.",
    ]
    grounding = _grounding_comment(params)
    if grounding:
        lines.append(grounding)
    lines.append(" */")
    return "\n".join(lines) + "\n\n"


# --------------------------------------------------------------------------- #
# Renderers (one per template; validation happens before any output)
# --------------------------------------------------------------------------- #


def _render_stm32_gpio_output(params: dict[str, Any]) -> str:
    """STM32 HAL GPIO output.

    Contract (``target="stm32-hal"``, ``category="gpio"``):

    - required ``port``: GPIO bank, one of ``GPIOA``..``GPIOH``.
    - required ``pin``: ``"GPIO_PIN_0"``..``"GPIO_PIN_31"``.
    - optional ``mode`` (default ``GPIO_MODE_OUTPUT_PP``), ``pull``
      (default ``GPIO_NOPULL``), ``speed`` (default
      ``GPIO_SPEED_FREQ_LOW``), ``initial_state`` (default
      ``GPIO_PIN_RESET``), ``hal_header`` (default ``stm32f1xx_hal.h``).
    - optional grounding hints: ``ref``/``ref_pin``/``net`` (a KiCad
      component/pin/net that justifies this pin choice) and ``label``.
    """
    name = "stm32_gpio_output"
    port = _choice_param(name, params, "port", _GPIO_PORTS)
    pin = _pattern_param(name, params, "pin", _GPIO_PIN_RE)
    mode = _choice_param_default(name, params, "mode", _GPIO_MODES, "GPIO_MODE_OUTPUT_PP")
    pull = _choice_param_default(name, params, "pull", _GPIO_PULLS, "GPIO_NOPULL")
    speed = _choice_param_default(name, params, "speed", _GPIO_SPEEDS, "GPIO_SPEED_FREQ_LOW")
    initial = _choice_param_default(name, params, "initial_state", _GPIO_LEVELS, "GPIO_PIN_RESET")
    header = _hal_header_param(name, params)
    return f"""\
{_generated_header(name, params)}#include "main.h"
#include "{header}"

void gpio_output_init(void)
{{
    GPIO_InitTypeDef GPIO_InitStruct = {{0}};

    __HAL_RCC_{port}_CLK_ENABLE();

    GPIO_InitStruct.Pin = {pin};
    GPIO_InitStruct.Mode = {mode};
    GPIO_InitStruct.Pull = {pull};
    GPIO_InitStruct.Speed = {speed};
    HAL_GPIO_Init({port}, &GPIO_InitStruct);

    HAL_GPIO_WritePin({port}, {pin}, {initial});{_label_comment(params)}
}}
"""


def _render_stm32_pwm_timer(params: dict[str, Any]) -> str:
    """STM32 HAL PWM output on a general-purpose timer.

    Contract (``target="stm32-hal"``, ``category="pwm"``):

    - required ``timer``: one of ``TIM1``..``TIM8``, ``TIM12``..``TIM17``.
    - required ``channel``: ``"TIM_CHANNEL_1"``..``"TIM_CHANNEL_4"``.
    - required ``port``/``pin``: the GPIO used as the channel's AF output.
    - required ``prescaler`` (0..65535) and ``period`` (1..65535); the PWM
      frequency is ``timer_clock / ((prescaler + 1) * (period + 1))``.
    - optional ``pulse_percent`` (0..100, default 50): initial duty.
    - optional ``hal_header`` and grounding hints ``ref``/``ref_pin``/``net``.
    """
    name = "stm32_pwm_timer"
    timer = _choice_param(name, params, "timer", _TIMERS)
    channel = _choice_param(name, params, "channel", _TIM_CHANNELS)
    port = _choice_param(name, params, "port", _GPIO_PORTS)
    pin = _pattern_param(name, params, "pin", _GPIO_PIN_RE)
    prescaler = _int_param(name, params, "prescaler", minimum=0, maximum=65535)
    period = _int_param(name, params, "period", minimum=1, maximum=65535)
    percent = _float_param(name, params, "pulse_percent", minimum=0.0, maximum=100.0, default=50.0)
    pulse = round(period * percent / 100.0)
    header = _hal_header_param(name, params)
    handle = f"htim{timer.removeprefix('TIM')}"
    return f"""\
{_generated_header(name, params)}#include "main.h"
#include "{header}"

TIM_HandleTypeDef {handle};

void pwm_timer_init(void)
{{
    GPIO_InitTypeDef GPIO_InitStruct = {{0}};
    TIM_OC_InitTypeDef sConfigOC = {{0}};

    __HAL_RCC_{timer}_CLK_ENABLE();
    __HAL_RCC_{port}_CLK_ENABLE();

    {handle}.Instance = {timer};
    {handle}.Init.Prescaler = {prescaler};
    {handle}.Init.Period = {period};
    {handle}.Init.CounterMode = TIM_COUNTERMODE_UP;
    {handle}.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
    HAL_TIM_PWM_Init(&{handle});

    GPIO_InitStruct.Pin = {pin};
    GPIO_InitStruct.Mode = GPIO_MODE_AF_PP;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
    HAL_GPIO_Init({port}, &GPIO_InitStruct);

    sConfigOC.OCMode = TIM_OCMODE_PWM1;
    sConfigOC.Pulse = {pulse};
    sConfigOC.OCPolarity = TIM_OCPOLARITY_HIGH;
    HAL_TIM_PWM_ConfigChannel(&{handle}, &sConfigOC, {channel});

    HAL_TIM_PWM_Start(&{handle}, {channel});{_label_comment(params)}
}}
"""


def _render_stm32_adc_poll(params: dict[str, Any]) -> str:
    """STM32 HAL ADC in polled (blocking) mode.

    Contract (``target="stm32-hal"``, ``category="adc"``):

    - required ``adc``: ``"ADC1"``/``"ADC2"``/``"ADC3"``.
    - required ``channel``: ``"ADC_CHANNEL_0"``..``"ADC_CHANNEL_18"``.
    - required ``port``/``pin``: the analog-capable GPIO of the channel.
    - optional ``samples`` (1..1024, default 1), ``vref_mv`` (1..10000,
      default 3300), ``resolution_bits`` (8..16, default 12; used in the
      scaling comment only).
    - optional ``hal_header`` and grounding hints.
    """
    name = "stm32_adc_poll"
    adc = _choice_param(name, params, "adc", _ADCS)
    channel = _pattern_param(name, params, "channel", _ADC_CHANNEL_RE)
    port = _choice_param(name, params, "port", _GPIO_PORTS)
    pin = _pattern_param(name, params, "pin", _GPIO_PIN_RE)
    samples = _int_param(name, params, "samples", minimum=1, maximum=1024, default=1)
    vref_mv = _int_param(name, params, "vref_mv", minimum=1, maximum=10000, default=3300)
    resolution = _int_param(name, params, "resolution_bits", minimum=8, maximum=16, default=12)
    header = _hal_header_param(name, params)
    handle = f"hadc{adc.removeprefix('ADC')}"
    raw_max = 2**resolution - 1
    return f"""\
{_generated_header(name, params)}#include "main.h"
#include "{header}"

ADC_HandleTypeDef {handle};

void adc_poll_init(void)
{{
    GPIO_InitTypeDef GPIO_InitStruct = {{0}};
    ADC_ChannelConfTypeDef sConfig = {{0}};

    __HAL_RCC_{adc}_CLK_ENABLE();
    __HAL_RCC_{port}_CLK_ENABLE();

    GPIO_InitStruct.Pin = {pin};
    GPIO_InitStruct.Mode = GPIO_MODE_ANALOG;
    HAL_GPIO_Init({port}, &GPIO_InitStruct);

    {handle}.Instance = {adc};
    {handle}.Init.ScanConvMode = ADC_SCAN_DISABLE;
    {handle}.Init.ContinuousConvMode = DISABLE;
    {handle}.Init.DataAlign = ADC_DATAALIGN_RIGHT;
    {handle}.Init.NbrOfConversion = 1;
    HAL_ADC_Init(&{handle});

    sConfig.Channel = {channel};
    sConfig.SamplingTime = ADC_SAMPLETIME_1CYCLE_5;
    HAL_ADC_ConfigChannel(&{handle}, &sConfig);
}}

uint16_t adc_poll_read(void)
{{
    HAL_ADC_Start(&{handle});
    HAL_ADC_PollForConversion(&{handle}, 100);
    uint16_t raw = HAL_ADC_GetValue(&{handle});
    HAL_ADC_Stop(&{handle});
    return raw;   /* {samples} sample(s); scale: raw * {vref_mv} / {raw_max} mV */{_label_comment(params)}
}}
"""


def _render_stm32_uart_init(params: dict[str, Any]) -> str:
    """STM32 HAL UART/USART initialization (blocking transmit ready).

    Contract (``target="stm32-hal"``, ``category="uart"``):

    - required ``uart``: ``USART1``..``USART3`` or ``UART4``..``UART8``.
    - required ``baudrate`` (1..12_000_000, e.g. ``115200``).
    - optional ``port`` (default ``GPIOA``), ``tx_pin`` (default
      ``GPIO_PIN_2``), ``rx_pin`` (default ``GPIO_PIN_3``),
      ``word_length``, ``parity``, ``stop_bits``, ``hal_header`` and
      grounding hints.
    """
    name = "stm32_uart_init"
    uart = _choice_param(name, params, "uart", _UARTS)
    baudrate = _int_param(name, params, "baudrate", minimum=1, maximum=12_000_000)
    port = _choice_param_default(name, params, "port", _GPIO_PORTS, "GPIOA")
    tx_pin = params.get("tx_pin", "GPIO_PIN_2")
    rx_pin = params.get("rx_pin", "GPIO_PIN_3")
    if not isinstance(tx_pin, str) or _GPIO_PIN_RE.fullmatch(tx_pin) is None:
        raise TemplateParamError(
            f"template {name!r}: parameter 'tx_pin' must match {_GPIO_PIN_RE.pattern!r}, "
            f"got {tx_pin!r}"
        )
    if not isinstance(rx_pin, str) or _GPIO_PIN_RE.fullmatch(rx_pin) is None:
        raise TemplateParamError(
            f"template {name!r}: parameter 'rx_pin' must match {_GPIO_PIN_RE.pattern!r}, "
            f"got {rx_pin!r}"
        )
    word_length = _choice_param_default(
        name, params, "word_length", _UART_WORD_LENGTHS, "UART_WORDLENGTH_8B"
    )
    parity = _choice_param_default(name, params, "parity", _UART_PARITIES, "UART_PARITY_NONE")
    stop_bits = _choice_param_default(name, params, "stop_bits", _UART_STOP_BITS, "UART_STOPBITS_1")
    header = _hal_header_param(name, params)
    digits = uart.removeprefix("USART") if uart.startswith("USART") else uart.removeprefix("UART")
    handle = f"huart{digits}"
    return f"""\
{_generated_header(name, params)}#include "main.h"
#include "{header}"

UART_HandleTypeDef {handle};

void uart_init(void)
{{
    GPIO_InitTypeDef GPIO_InitStruct = {{0}};

    __HAL_RCC_{uart}_CLK_ENABLE();
    __HAL_RCC_{port}_CLK_ENABLE();

    GPIO_InitStruct.Pin = {tx_pin} | {rx_pin};
    GPIO_InitStruct.Mode = GPIO_MODE_AF_PP;
    GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
    HAL_GPIO_Init({port}, &GPIO_InitStruct);

    {handle}.Instance = {uart};
    {handle}.Init.BaudRate = {baudrate};
    {handle}.Init.WordLength = {word_length};
    {handle}.Init.StopBits = {stop_bits};
    {handle}.Init.Parity = {parity};
    {handle}.Init.Mode = UART_MODE_TX_RX;
    HAL_UART_Init(&{handle});{_label_comment(params)}
}}
"""


def _render_arduino_gpio_output(params: dict[str, Any]) -> str:
    """Arduino GPIO digital output (``pinMode`` + ``digitalWrite``).

    Contract (``target="arduino"``, ``category="gpio"``):

    - required ``pin``: ``"LED_BUILTIN"``, a number ``0``-``99``, or an
      ``"A0"``/``"D13"``-style pin name.
    - optional ``initial`` (``"LOW"`` default or ``"HIGH"``), ``label``
      and grounding hints ``ref``/``ref_pin``/``net``.
    """
    name = "arduino_gpio_output"
    pin = _pattern_param(name, params, "pin", _ARDUINO_PIN_RE)
    initial = _choice_param_default(name, params, "initial", _ARDUINO_LEVELS, "LOW")
    return f"""\
{_generated_header(name, params)}const int kOutputPin = {pin};

void setup()
{{
    pinMode(kOutputPin, OUTPUT);
    digitalWrite(kOutputPin, {initial});{_label_comment(params)}
}}

void loop()
{{
    /* user logic */
}}
"""


def _render_arduino_pwm_analogwrite(params: dict[str, Any]) -> str:
    """Arduino PWM output with ``analogWrite`` (8-bit duty).

    Contract (``target="arduino"``, ``category="pwm"``):

    - required ``pin``: any Arduino PWM-capable pin (see ``pin`` rule of
      :func:`_render_arduino_gpio_output`).
    - required ``duty``: integer 0..255 (0 = off, 255 = full).
    - optional ``label`` and grounding hints.
    """
    name = "arduino_pwm_analogwrite"
    pin = _pattern_param(name, params, "pin", _ARDUINO_PIN_RE)
    duty = _int_param(name, params, "duty", minimum=0, maximum=255)
    percent = duty * 100.0 / 255.0
    return f"""\
{_generated_header(name, params)}const int kPwmPin = {pin};

void setup()
{{
    pinMode(kPwmPin, OUTPUT);
    analogWrite(kPwmPin, {duty});   /* {percent:.1f}% duty ({duty}/255) */{_label_comment(params)}
}}

void loop()
{{
}}
"""


def _render_arduino_adc_read(params: dict[str, Any]) -> str:
    """Arduino analog input with millivolt scaling (10-bit ADC).

    Contract (``target="arduino"``, ``category="adc"``):

    - required ``pin``: an ``"A0"``-style analog pin.
    - optional ``vref_mv`` (1..10000, default 5000) used in the scaling
      expression, ``label`` and grounding hints.
    """
    name = "arduino_adc_read"
    pin = _pattern_param(name, params, "pin", _ARDUINO_PIN_RE)
    vref_mv = _int_param(name, params, "vref_mv", minimum=1, maximum=10000, default=5000)
    return f"""\
{_generated_header(name, params)}const int kAdcPin = {pin};

void setup()
{{
    pinMode(kAdcPin, INPUT);
    Serial.begin(9600);
}}

void loop()
{{
    int raw = analogRead(kAdcPin);        /* 0..1023 (10-bit) */
    long mv = (long)raw * {vref_mv} / 1023;   /* approximate millivolts */
    Serial.println(mv);
    delay(100);{_label_comment(params)}
}}
"""


def _render_arduino_i2c_scan(params: dict[str, Any]) -> str:
    """Arduino I2C device scan over the Wire library.

    Contract (``target="arduino"``, ``category="i2c"``):

    - required ``sda_pin``/``scl_pin``: Arduino pin names for the I2C bus
      (classic Uno: ``"A4"``/``"A5"``).
    - optional ``clock_hz`` (1..1_000_000, default 100000; rendered as a
      comment — ``Wire.setClock`` is called at runtime), ``label`` and
      grounding hints.
    """
    name = "arduino_i2c_scan"
    sda = _pattern_param(name, params, "sda_pin", _ARDUINO_PIN_RE)
    scl = _pattern_param(name, params, "scl_pin", _ARDUINO_PIN_RE)
    clock_hz = _int_param(name, params, "clock_hz", minimum=1, maximum=1_000_000, default=100000)
    return f"""\
{_generated_header(name, params)}#include <Wire.h>

void setup()
{{
    Wire.begin({sda}, {scl});            /* SDA, SCL - clock {clock_hz} Hz */
    Serial.begin(115200);
}}

void loop()
{{
    byte error, address;
    int nDevices = 0;

    Serial.println("Scanning I2C bus...");
    for (address = 1; address < 127; address++)
    {{
        Wire.beginTransmission(address);
        error = Wire.endTransmission();
        if (error == 0)
        {{
            Serial.print("I2C device found at 0x");
            if (address < 16)
            {{
                Serial.print("0");
            }}
            Serial.println(address, HEX);
            nDevices++;
        }}
        else if (error == 4)
        {{
            Serial.print("Unknown error at 0x");
            Serial.println(address, HEX);
        }}
    }}
    Serial.print("done. devices found: ");
    Serial.println(nDevices);
    delay(1000);{_label_comment(params)}
}}
"""


# --------------------------------------------------------------------------- #
# Template model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Template:
    """A deterministic firmware template.

    Attributes:
        name: Unique template id (also its ``_RENDERERS`` key).
        target: Firmware target — one of :data:`TARGETS`.
        category: Peripheral category — one of :data:`CATEGORIES`.
        required_params: Params that ``render`` requires (missing -> error).
        optional_params: Params accepted with documented defaults.
        description: One-line human summary for catalogs/tools.

    ``render`` is pure and deterministic; it validates parameters first
    (required presence, no unexpected keys, per-template format and range
    rules) and only then renders the code, so a bad call never produces
    partial output.
    """

    name: str
    target: str
    category: str
    required_params: tuple[str, ...]
    optional_params: tuple[str, ...]
    description: str

    def render(self, params: dict[str, Any]) -> str:
        """Validate ``params`` against the contract and render the code.

        Raises:
            TemplateParamError: on a missing required, unexpected, or
                out-of-contract parameter (nothing is rendered).
        """
        expected = set(self.required_params) | set(self.optional_params)
        unexpected = sorted(set(params) - expected)
        if unexpected:
            raise TemplateParamError(
                f"template {self.name!r}: unexpected parameter(s) "
                f"{', '.join(repr(key) for key in unexpected)}; "
                f"expected one of: {', '.join(sorted(expected))}"
            )
        missing = [key for key in self.required_params if key not in params]
        if missing:
            raise TemplateParamError(
                f"template {self.name!r}: missing required parameter(s): {', '.join(missing)}"
            )
        return _RENDERERS[self.name](params)


# --------------------------------------------------------------------------- #
# Catalog
# --------------------------------------------------------------------------- #

#: Ordered template catalog (index order is part of the public contract).
TEMPLATES: tuple[Template, ...] = (
    Template(
        name="stm32_gpio_output",
        target="stm32-hal",
        category="gpio",
        required_params=("port", "pin"),
        optional_params=(
            "mode",
            "pull",
            "speed",
            "initial_state",
            "hal_header",
            *_COMMON_OPTIONAL_PARAMS,
        ),
        description="STM32 HAL GPIO output: clock enable, pin init, initial level.",
    ),
    Template(
        name="stm32_pwm_timer",
        target="stm32-hal",
        category="pwm",
        required_params=("timer", "channel", "port", "pin", "prescaler", "period"),
        optional_params=("pulse_percent", "hal_header", *_COMMON_OPTIONAL_PARAMS),
        description="STM32 HAL PWM on a general-purpose timer (PSC/ARR + channel).",
    ),
    Template(
        name="stm32_adc_poll",
        target="stm32-hal",
        category="adc",
        required_params=("adc", "channel", "port", "pin"),
        optional_params=(
            "samples",
            "vref_mv",
            "resolution_bits",
            "hal_header",
            *_COMMON_OPTIONAL_PARAMS,
        ),
        description="STM32 HAL ADC in polled mode: init, blocking conversion, raw read.",
    ),
    Template(
        name="stm32_uart_init",
        target="stm32-hal",
        category="uart",
        required_params=("uart", "baudrate"),
        optional_params=(
            "port",
            "tx_pin",
            "rx_pin",
            "word_length",
            "parity",
            "stop_bits",
            "hal_header",
            *_COMMON_OPTIONAL_PARAMS,
        ),
        description="STM32 HAL UART/USART init at a given baudrate (blocking transmit).",
    ),
    Template(
        name="arduino_gpio_output",
        target="arduino",
        category="gpio",
        required_params=("pin",),
        optional_params=("initial", *_COMMON_OPTIONAL_PARAMS),
        description="Arduino digital output: pinMode(OUTPUT) + digitalWrite.",
    ),
    Template(
        name="arduino_pwm_analogwrite",
        target="arduino",
        category="pwm",
        required_params=("pin", "duty"),
        optional_params=(*_COMMON_OPTIONAL_PARAMS,),
        description="Arduino PWM with analogWrite: 8-bit duty on a PWM-capable pin.",
    ),
    Template(
        name="arduino_adc_read",
        target="arduino",
        category="adc",
        required_params=("pin",),
        optional_params=("vref_mv", *_COMMON_OPTIONAL_PARAMS),
        description="Arduino analog input: analogRead() with millivolt scaling.",
    ),
    Template(
        name="arduino_i2c_scan",
        target="arduino",
        category="i2c",
        required_params=("sda_pin", "scl_pin"),
        optional_params=("clock_hz", *_COMMON_OPTIONAL_PARAMS),
        description="Arduino I2C device scan over the Wire library.",
    ),
)

#: Name -> template lookup (immutable after module import).
TEMPLATE_INDEX: dict[str, Template] = {template.name: template for template in TEMPLATES}

#: Renderer lookup: template name -> private render function.
_RENDERERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "stm32_gpio_output": _render_stm32_gpio_output,
    "stm32_pwm_timer": _render_stm32_pwm_timer,
    "stm32_adc_poll": _render_stm32_adc_poll,
    "stm32_uart_init": _render_stm32_uart_init,
    "arduino_gpio_output": _render_arduino_gpio_output,
    "arduino_pwm_analogwrite": _render_arduino_pwm_analogwrite,
    "arduino_adc_read": _render_arduino_adc_read,
    "arduino_i2c_scan": _render_arduino_i2c_scan,
}


def get_template(name: str) -> Template:
    """Look up a template by name.

    Raises:
        UnknownTemplateError: when ``name`` is not in the catalog (with the
            available names in the message).
    """
    try:
        return TEMPLATE_INDEX[name]
    except KeyError:
        available = ", ".join(sorted(TEMPLATE_INDEX))
        raise UnknownTemplateError(f"unknown template {name!r}; available: {available}") from None


def list_templates() -> list[dict[str, object]]:
    """Machine-readable catalog for the agent / MCP layer.

    Returns one dict per template in catalog order with ``name``,
    ``target``, ``category``, ``required_params``, ``optional_params``
    and ``description`` — enough for a caller (LLM or CLI) to pick a
    template without importing internals.
    """
    return [
        {
            "name": template.name,
            "target": template.target,
            "category": template.category,
            "required_params": list(template.required_params),
            "optional_params": list(template.optional_params),
            "description": template.description,
        }
        for template in TEMPLATES
    ]
