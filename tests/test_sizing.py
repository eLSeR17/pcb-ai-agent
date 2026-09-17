"""Sizing library tests — closed-form, deterministic math with real-world
reference values. Every formula is locked to a documented example."""

from __future__ import annotations

import math
import warnings

import pytest

from pcbai.design.sizing import (
    DIODE_DISSIPATION_LIMIT_W,
    E12_SERIES,
    decoupling_capacitor,
    e12_round,
    led_current_with_series_r,
    led_series_resistor,
    pull_up_resistor,
    switch_diode_forward_check,
    voltage_divider,
)


def _is_e12(value: float) -> bool:
    """True when ``value`` is an E12 preferred number (any decade)."""
    return any(
        math.isclose(value, step * 10**decade, rel_tol=1e-9)
        for step in E12_SERIES
        for decade in range(-9, 10)
    )


class TestE12Round:
    @pytest.mark.parametrize(
        ("ohms", "expected"),
        [
            (150.0, 150.0),  # the LED reference value, already E12
            (1000.0, 1000.0),
            (4700.0, 4700.0),
            (0.47, 0.47),
            (145.0, 150.0),  # nearest E12 step
            (960.0, 1000.0),  # decade-boundary rounding
        ],
    )
    def test_e12_round_known_values(self, ohms: float, expected: float) -> None:
        assert e12_round(ohms) == expected

    @pytest.mark.parametrize(
        "ohms",
        [10.0, 47.0, 120.0, 330.0, 2200.0, 82000.0, 0.1, 1_000_000.0],
    )
    def test_e12_round_stays_within_series_tolerance(self, ohms: float) -> None:
        # E12 (10 % tolerance) guarantees the nearest step is within ~12 %.
        assert abs(e12_round(ohms) - ohms) / ohms <= 0.12

    def test_e12_round_rejects_non_positive(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            e12_round(-1.0)
        with pytest.raises(ValueError, match="positive"):
            e12_round(0.0)


class TestLedSeriesResistor:
    def test_reference_5v_2v_20ma(self) -> None:
        # R = (5 - 2.0) / 0.020 = 150 ohm (E12); P = 0.02^2 * 150 = 0.06 W.
        assert led_series_resistor(5.0, 2.0, 0.020) == (150.0, 0.06)

    def test_result_is_e12(self) -> None:
        # 12 V, 3.3 V, 20 mA -> R ~= 435 ohm -> E12 (470 ohm).
        resistor, _power = led_series_resistor(12.0, 3.3, 20e-3)
        assert _is_e12(resistor)

    def test_supply_must_exceed_led_voltage(self) -> None:
        with pytest.raises(ValueError, match="must exceed"):
            led_series_resistor(3.0, 5.0, 0.020)

    def test_current_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            led_series_resistor(5.0, 2.0, 0.0)


class TestPullUpResistor:
    def test_reference_i2c_grade_4700_ohm(self) -> None:
        # R = (3.3 - 2.36) / 200e-6 = 4700 ohm exactly (E12).
        assert pull_up_resistor(200, 2.36, 3.3) == 4700.0

    def test_out_of_band_warns(self) -> None:
        # R = 150 ohm is outside 1k..100k -> UserWarning, value still returned.
        with pytest.warns(UserWarning):
            assert pull_up_resistor(1000, 3.15, 3.3) == 150.0

    def test_in_band_is_silent(self) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("error", UserWarning)
            assert pull_up_resistor(200, 2.36, 3.3) == 4700.0

    def test_high_min_must_be_below_supply(self) -> None:
        with pytest.raises(ValueError, match="below v_supply"):
            pull_up_resistor(200, 3.3, 3.3)


class TestVoltageDivider:
    def test_reference_equal_resistors(self) -> None:
        # 5 V through 10k/10k -> 2.5 V, ratio 0.5.
        assert voltage_divider(5.0, 10_000.0, 10_000.0) == (2.5, 0.5)

    def test_attenuation_ratio(self) -> None:
        # 5 V through 1k top / 9k bottom -> 4.5 V, ratio 0.9.
        v_out, ratio = voltage_divider(5.0, 1_000.0, 9_000.0)
        assert v_out == pytest.approx(4.5)
        assert ratio == pytest.approx(0.9)

    def test_resistors_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            voltage_divider(5.0, 0.0, 10_000.0)
        with pytest.raises(ValueError, match="positive"):
            voltage_divider(0.0, 1_000.0, 9_000.0)


class TestSwitchDiodeForwardCheck:
    def test_small_signal_diode_within_budget(self) -> None:
        result = switch_diode_forward_check(0.3, 0.05)
        assert result["p_diss"] == pytest.approx(0.015)
        assert result["exceeds"] is False
        assert result["limit_w"] == DIODE_DISSIPATION_LIMIT_W

    def test_power_diode_exceeds_dissipation_budget(self) -> None:
        # 1N400x-class diode at 1 A: ~0.7 V * 1 A = 0.7 W > 0.5 W budget.
        result = switch_diode_forward_check(0.7, 1.0)
        assert result["p_diss"] == pytest.approx(0.7)
        assert result["exceeds"] is True


class TestDecouplingCapacitor:
    def test_reference_16mhz_1ma(self) -> None:
        # C = 0.001 / (0.05 * 16e6) = 1.25 nF -> 1.2 nF (E12).
        assert decoupling_capacitor(16e6, 1.0) == pytest.approx(1.2e-9)

    def test_custom_ripple_budget(self) -> None:
        # Doubling the ripple budget halves the required capacitance:
        # C = 0.001 / (0.1 * 16e6) = 6.25e-10 F -> 6.8e-10 F (E12).
        assert decoupling_capacitor(16e6, 1.0, v_ripple_ok=0.1) == pytest.approx(6.8e-10)

    def test_inputs_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            decoupling_capacitor(0.0, 1.0)


class TestLedCurrentWithSeriesR:
    def test_reference_inverse_check(self) -> None:
        # I = (5 - 2.0) / 150 = 0.02 A — the inverse of the 150 ohm reference.
        assert led_current_with_series_r(5.0, 2.0, 150.0) == pytest.approx(0.02)

    def test_validation_errors(self) -> None:
        with pytest.raises(ValueError, match="must exceed"):
            led_current_with_series_r(2.0, 5.0, 150.0)
        with pytest.raises(ValueError, match="positive"):
            led_current_with_series_r(5.0, 2.0, 0.0)
