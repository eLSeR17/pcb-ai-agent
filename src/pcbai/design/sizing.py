"""Closed-form board sizing calculations (Ohm's law and practical design).

The numeric heart of the read layer. Every
function here is a pure, deterministic evaluation of a documented formula
with real-world reference values — the project invariant is that numbers
always come from code, never from an LLM.

Formulas locked in this module (the ones an electronics engineer writes
down by hand, with test-enforced reference examples):

- ``R = (V_supply - V_led) / I_led``, ``P = I_led**2 * R`` — LED series
  resistor (reference: 5 V, 2.0 V, 20 mA -> 150 ohm (E12), 0.06 W).
- ``R = (V_supply - V_high_min) / I_pull`` — bus pull-up, e.g. I2C.
- ``V_out = V_in * R_bottom / (R_top + R_bottom)`` — resistive divider.
- ``P_diss = V_forward * I_forward`` — diode forward dissipation.
- ``C = I_sw / (V_ripple * f_clock)`` — bulk decoupling capacitance.
- ``I = (V_supply - V_led) / R`` — current through LED + series resistor
  (inverse verification of the first formula).

All resistances/capacitances are rounded to the E12 preferred-number
series (10 % tolerance steps). Inputs are validated as physical quantities
(strictly positive); violations raise ``ValueError`` with a clear message.
"""

from __future__ import annotations

import math
import warnings

__all__ = [
    "DIODE_DISSIPATION_LIMIT_W",
    "E12_SERIES",
    "PULL_UP_BAND_OHMS",
    "decoupling_capacitor",
    "e12_round",
    "led_current_with_series_r",
    "led_series_resistor",
    "pull_up_resistor",
    "switch_diode_forward_check",
    "voltage_divider",
]

#: E12 preferred-number series (one row per decade, 10 % tolerance).
E12_SERIES: tuple[float, ...] = (
    1.0,
    1.2,
    1.5,
    1.8,
    2.2,
    2.7,
    3.3,
    3.9,
    4.7,
    5.6,
    6.8,
    8.2,
)

#: Pull-up resistors outside this band are suspicious for a bus like I2C.
PULL_UP_BAND_OHMS: tuple[float, float] = (1_000.0, 100_000.0)

#: Above this forward dissipation a diode needs thermal derating.
DIODE_DISSIPATION_LIMIT_W: float = 0.5

#: Default tolerable ripple budget for the decoupling estimate.
RIPPLE_BUDGET_V: float = 0.05


def e12_round(value: float) -> float:
    """Round a positive value to the nearest E12 preferred number.

    E12 is the 10 %-tolerance series ``{1.0, 1.2, ..., 8.2} x 10**n``
    (IEC 60063). The nearest step is chosen by relative distance, which
    keeps the error symmetric across decades. The candidate search spans
    the floor, previous and next decade so that floating-point
    ``log10`` wobble near decade boundaries cannot change the result.

    Formula:
        mantissa = value / 10**floor(log10(value))
        result   = nearest(e12_step, mantissa) * 10**floor(log10(value))

    Raises ``ValueError`` for non-positive or non-finite input.
    """
    value = _validate_positive(value, "value")
    decade = math.floor(math.log10(value))
    candidates = (
        step * 10.0**shift for shift in (decade - 1, decade, decade + 1) for step in E12_SERIES
    )
    nearest = min(candidates, key=lambda candidate: abs(candidate - value) / value)
    # 12 significant digits: kills binary floating-point noise (e.g. 4.7*0.1
    # -> 0.47000000000000003) while keeping every E12 value exact.
    return float(f"{nearest:.12g}")


def led_series_resistor(v_supply: float, v_led: float, i_led: float) -> tuple[float, float]:
    """Series resistor for an LED driven from a supply.

    Formulas:
        R = (V_supply - V_led) / I_led          (Ohm's law on the drop)
        P = I_led**2 * R                        (watts in the E12 resistor)

    ``R`` is rounded to the E12 series; ``P`` is the power the *chosen*
    resistor dissipates. ``v_supply`` must exceed ``v_led`` — otherwise a
    series resistor cannot limit the current and the call raises
    ``ValueError``.

    Reference: ``led_series_resistor(5.0, 2.0, 0.020)`` ->
    ``(150.0, 0.06)`` (150 ohm is a standard E12 value, 60 mW).
    """
    supply = _validate_positive(v_supply, "v_supply")
    led = _validate_positive(v_led, "v_led")
    current = _validate_positive(i_led, "i_led")
    if supply <= led:
        raise ValueError(
            f"v_supply ({supply} V) must exceed v_led ({led} V) for a series resistor to limit the current"
        )
    resistor = e12_round((supply - led) / current)
    # 8 decimals on watts: far beyond any design resolution and enough to
    # remove binary floating-point noise (0.02**2 * 150 -> 0.060000000000000005).
    power = round(current**2 * resistor, 8)
    return (resistor, power)


def pull_up_resistor(pull_current_ua: float, v_high_min: float, v_supply: float) -> float:
    """Maximum pull-up resistor for a bus such as I2C/SPI.

    Formula:
        R = (V_supply - V_high_min) / I_pull,   I_pull = pull_current_ua * 1e-6

    The pull current must keep the line above ``v_high_min`` (the minimum
    high-level input voltage). The resistor is rounded to E12 and validated
    against the 1 kohm..100 kohm band: out-of-band values emit a
    ``UserWarning`` (an unusually strong or weak pull suggests wrong
    assumptions, not a clean design).

    Reference: standard I2C-grade pull of 200 uA with a 0.94 V budget ->
    ``pull_up_resistor(200, 2.36, 3.3) == 4700.0`` (4.7 kohm, E12).
    """
    current = _validate_positive(pull_current_ua, "pull_current_ua") * 1e-6
    high_min = _validate_positive(v_high_min, "v_high_min")
    supply = _validate_positive(v_supply, "v_supply")
    if high_min >= supply:
        raise ValueError(f"v_high_min ({high_min} V) must be below v_supply ({supply} V)")
    resistor = e12_round((supply - high_min) / current)
    low, high = PULL_UP_BAND_OHMS
    if not low <= resistor <= high:
        warnings.warn(
            f"pull-up resistor {resistor:.3g} ohm is outside the {low:.0f}..{high:.0f} ohm band; "
            "check the bus pull-up assumptions",
            UserWarning,
            stacklevel=2,
        )
    return resistor


def voltage_divider(v_in: float, r_top: float, r_bottom: float) -> tuple[float, float]:
    """Attenuating divider between a top and a bottom resistor.

    Formulas:
        ratio = R_bottom / (R_top + R_bottom)
        v_out = V_in * ratio

    Returns ``(v_out, ratio)``. The result must be strictly inside the
    ``(0, V_in)`` range (a divider is a passive attenuator); any input
    that would not produce that raises ``ValueError``.
    """
    input_voltage = _validate_positive(v_in, "v_in")
    top = _validate_positive(r_top, "r_top")
    bottom = _validate_positive(r_bottom, "r_bottom")
    ratio = bottom / (top + bottom)
    v_out = input_voltage * ratio
    if not 0.0 < v_out < input_voltage:
        raise ValueError(
            f"divider output {v_out} V is not strictly between 0 and V_in ({input_voltage} V)"
        )
    return (v_out, ratio)


def switch_diode_forward_check(v_forward: float, i_forward: float) -> dict[str, float | bool]:
    """Forward dissipation of a switch/flyback diode.

    Formula:
        P_diss = V_forward * I_forward

    Returns a dict with the dissipation and a flag for the
    ``DIODE_DISSIPATION_LIMIT_W`` budget: above 0.5 W the diode needs a
    thermally adequate footprint/heatsink (or a lower ``V_forward``
    part). Reference: a 1N400x-class diode at 1 A drops ~0.7 V ->
    ``p_diss == 0.7``, ``exceeds is True``.
    """
    forward = _validate_positive(v_forward, "v_forward")
    current = _validate_positive(i_forward, "i_forward")
    p_diss = forward * current
    return {
        "p_diss": p_diss,
        "limit_w": DIODE_DISSIPATION_LIMIT_W,
        "exceeds": p_diss > DIODE_DISSIPATION_LIMIT_W,
    }


def decoupling_capacitor(
    f_clock_hz: float,
    i_switching_ma: float,
    v_ripple_ok: float = RIPPLE_BUDGET_V,
) -> float:
    """Bulk decoupling capacitance for a digital rail (farads, E12).

    Formula (charge balance on the bypass capacitor):
        C_total ~= I_sw / (V_ripple * f_clock),
        I_sw = i_switching_ma * 1e-3

    ``v_ripple_ok`` defaults to 50 mV, a sane ripple budget for 3.3/5 V
    logic. The result is rounded to the E12 series. Reference:
    ``decoupling_capacitor(16e6, 1)`` -> 1.25 nF -> ``1.2e-9`` F (E12).
    """
    frequency = _validate_positive(f_clock_hz, "f_clock_hz")
    switching = _validate_positive(i_switching_ma, "i_switching_ma") * 1e-3
    ripple = _validate_positive(v_ripple_ok, "v_ripple_ok")
    return e12_round(switching / (ripple * frequency))


def led_current_with_series_r(v_supply: float, v_led: float, r_ohms: float) -> float:
    """Current through an LED with a known series resistor (inverse check).

    Formula:
        I = (V_supply - V_led) / R

    The inverse of :func:`led_series_resistor`: given a chosen resistor it
    returns the resulting LED current in amperes, so a design can be
    cross-checked against its intended value. Reference:
    ``led_current_with_series_r(5.0, 2.0, 150.0) == 0.02`` (20 mA).
    """
    supply = _validate_positive(v_supply, "v_supply")
    led = _validate_positive(v_led, "v_led")
    resistor = _validate_positive(r_ohms, "r_ohms")
    if supply <= led:
        raise ValueError(
            f"v_supply ({supply} V) must exceed v_led ({led} V) for the LED to conduct"
        )
    return (supply - led) / resistor


def _validate_positive(value: float, name: str) -> float:
    """Return ``value`` as float after checking it is a finite positive number.

    Physical quantities in this module are strictly positive; ``bool`` is
    rejected on purpose (``True`` is not an electrical quantity).
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number, got {value!r}")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number, got {value!r}")
    return float(value)
