"""Deterministic synthetic KiCad netlist generator with seeded design faults.

This module builds *synthetic but structurally realistic* KiCad netlists at
industrial scale (default 75 components, designed to scale to 500+) so the
audit pipeline can be validated against boards with *known* defects before it
is pointed at real 500+ component industrial designs.

Why a generator is part of the project
--------------------------------------

- **Reproducibility**: the same ``(n_components, seed)`` pair always yields
  the same board, byte for byte. ``generate_netlist(75)`` regenerates the
  versioned fixture ``tests/fixtures/industrial-74.kicad_net`` exactly, so
  tests and demos never depend on an opaque binary blob.
- **Known ground truth**: every seeded fault is recorded in the returned
  :class:`Board` manifest (rule id, component, exact evidence), so the
  independent verification in ``tests/test_industrial.py`` can assert that the
  auditor finds *exactly* the seeded faults — and nothing on healthy nets
  (no false positives).
- **Scale path**: ``n_components`` is a plain parameter. The board is built
  from a fixed core plus a deterministic cycle of healthy *cells*; asking for
  500 components exercises the same parser, the same audit rules and the same
  manifest contract as the 75-component demo board.

Format contract (non-negotiable — this is why the fixture parses)
------------------------------------------------------------------

The output is a *single* top-level ``(export ...)`` S-expression with no
trailing content (``pcbai.kicad.sexpr.parse`` rejects multiple top-level
forms and anything after the closing parenthesis). The structure mirrors
``tests/fixtures/simple-led.kicad_net``: ``(version "E")``, a ``(design ...)``
block, ``(components (comp (ref ...) (value ...) (footprint ...) ...))`` and
``(nets (net (code N) (name "...") (node (ref ...) (pin ...)) ...))``. No
field the audit does not know is emitted; a component without a footprint
simply omits the ``(footprint ...)`` line, like the ``no-footprint`` fixture.

Fault catalogue (rule ids match ``pcbai.design.audit`` exactly)
---------------------------------------------------------------

======================  ====  ======  ========================================
rule id                 comps sev.    seeded condition
======================  ====  ======  ========================================
LED_NO_LIMITER            2   warning LED straight to 5V/GND, no series R
E_SERIES_COMPLIANCE       3   warning R values 333 / 4.83k / 2.5k (non-E24)
CAP_DERATING              2   info    electrolytic rated below 1.5x the rail
MISSING_FOOTPRINT         2   error   component without footprint
FLOATING_NET              1   warning a net with a single connection (NC_1)
UNCONNECTED_PIN           2   warning R wired through a single pin
LED_SERIES_RESISTOR       1   warning LED with a 10 ohm (< 22 ohm) limiter
======================  ====  ======  ========================================

14 fault components out of 75 keep the board ~81 % healthy, so a report shows
a realistic mix of errors, warnings and infos instead of an all-red wall.

Determinism note: healthy component *values* are drawn from E12/E24 pools via
a seeded ``random.Random`` — the same seed yields the same values, different
seeds yield different (but always rule-safe) boards.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from pcbai.design.audit import DRIVER_PREFIXES, PASSIVE_PREFIXES, POWER_NET_NAMES

__all__ = [
    "ALL_FAULT_RULES",
    "Board",
    "PredictedFinding",
    "SeededFault",
    "generate_board",
    "generate_netlist",
]

#: Rule ids this generator knows how to seed, in canonical application order.
ALL_FAULT_RULES: tuple[str, ...] = (
    "LED_NO_LIMITER",
    "E_SERIES_COMPLIANCE",
    "CAP_DERATING",
    "MISSING_FOOTPRINT",
    "FLOATING_NET",
    "UNCONNECTED_PIN",
    "LED_SERIES_RESISTOR",
)

#: Seed of the versioned industrial-74 fixture (``generate_netlist(75)``).
DEFAULT_SEED: int = 0

#: ``(source ...)`` string of the versioned fixture (regeneration contract).
DEFAULT_SOURCE_PATH: str = "/pcb-ai-agent/tests/fixtures/industrial-74.kicad_net"

#: Fixed export date so regeneration is byte-identical.
DEFAULT_DATE: str = "2026-09-19T09:00:00+02:00"

#: Netlist S-expression version written by Eeschema 6.x-8.x.
_NETLIST_VERSION: str = "E"

#: Schematic design file version (Eeschema 8.x), informational only.
_SCHEMATIC_VERSION: str = "20231120"

# --------------------------------------------------------------------------
# Footprint / value pools (E12/E24-safe values keep healthy parts silent)
# --------------------------------------------------------------------------

_FOOTPRINTS: dict[str, str] = {
    "R": "Resistor_SMD:R_0603_1608Metric",
    "C": "Capacitor_SMD:C_0603_1608Metric",
    "CE": "Capacitor_SMD:CP_Elec_6.3x5.4",
    "LED": "LED_SMD:LED_0603_1608Metric",
    "D": "Diode_SMD:D_SOD-123",
    "Q": "Package_TO_SOT:SOT-23-3_1x1.3mm_P0.95mm",
    "U": "Package_SO:SOIC-16_3.9x9.9mm_P1.27mm",
    "L": "Inductor_SMD:L_0603_1608Metric",
}

_FOOTPRINT_J1: str = "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"
_FOOTPRINT_JN: str = "Connector_PinHeader_2.54mm:PinHeader_1x10_P2.54mm_Vertical"
_FOOTPRINT_U1: str = "Package_SO:TSSOP-20_4.4x6.5mm_P0.65mm"
_FOOTPRINT_U2: str = "Package_TO_SOT:SOT-223-3_TabPin4"

#: E24 limiter values for healthy LED chains (all >= 22 ohm).
_LED_LIMITER_POOL: tuple[int, ...] = (330, 220, 470, 150, 680, 1_000)

#: E24 pull-up resistor pool.
_PULLUP_POOL: tuple[str, ...] = ("10k", "4.7k", "2.2k", "47k")

#: E24 bias-to-ground resistor pool.
_BIAS_POOL: tuple[str, ...] = ("10k", "4.7k", "1k", "100k")

#: E24 ceramic decoupling values.
_FILTER_POOL: tuple[str, ...] = ("100nF", "10nF", "1nF", "100pF")

#: Electrolytic pool safe on the 5 V rail (rating >= 7.5 V).
_ELECT_5V_POOL: tuple[str, ...] = ("10uF 16V", "100uF 25V", "47uF 25V", "22uF 50V")

#: Electrolytic pool safe on the 12 V rail (rating >= 18 V).
_ELECT_12V_POOL: tuple[str, ...] = ("10uF 25V", "100uF 50V", "470uF 25V", "220uF 50V")

#: Healthy fixed-value parts.
_LED_VALUE: str = "LED"
_Q_VALUE: str = "MMBT3904"
_D_VALUE: str = "1N4148"
_L_VALUE: str = "10uH"
_U1_VALUE: str = "STM32F030F4P6"
_U2_VALUE: str = "AMS1117-3.3"
_U3_VALUE: str = "74HC595"

#: Components of the fixed board skeleton (rails + drivers + input filter).
_CORE_COMPONENT_COUNT: int = 9  # J1..J5, U1..U3, L1

# --------------------------------------------------------------------------
# Public data model
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SeededFault:
    """One fault deliberately planted in the board.

    The ``evidence`` tuple mirrors, character for character, the
    ``evidence`` list the auditor is expected to emit (e.g.
    ``("R101=333",)`` for E_SERIES_COMPLIANCE or ``("LED1", "5V", "GND")``
    for LED_NO_LIMITER).
    """

    rule: str
    ref: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class PredictedFinding:
    """A finding the audit is *predicted* to emit for this board.

    ``expected`` on :class:`Board` is the union of the seeded faults and the
    NO_DRIVER advisories the topology dictates (LED anode nets made of a
    resistor plus an LED), computed from the board model — not from the
    auditor — so :mod:`tests.test_industrial` is an independent cross-check
    of both implementations.
    """

    rule: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class Board:
    """A generated board and its ground-truth manifest.

    ``render()`` returns the KiCad netlist S-expression. ``expected``,
    ``fault_refs`` and ``fault_nets`` are the oracle the tests verify the
    auditor against.
    """

    seed: int
    n_components: int
    fault_rules: tuple[str, ...]
    source_path: str
    date: str
    component_count: int
    net_count: int
    seeded: tuple[SeededFault, ...]
    expected: tuple[PredictedFinding, ...]
    fault_refs: frozenset[str]
    fault_nets: frozenset[str]
    _components: tuple[tuple[str, str | None, str | None], ...] = field(repr=False, compare=False)
    _net_names: tuple[str, ...] = field(repr=False, compare=False)
    _net_nodes: tuple[tuple[tuple[str, str], ...], ...] = field(repr=False, compare=False)

    def render(self) -> str:
        """Render the board as a KiCad netlist (one top-level ``(export)``)."""
        lines = [
            f'(export (version "{_NETLIST_VERSION}")',
            "  (design",
            f'    (source "{self.source_path}")',
            f'    (date "{self.date}")',
            '    (tool "Eeschema (8.0.1)")',
            '    (sheet (number "1") (name "/") (tstamps "/"))',
            f'    (version "{_SCHEMATIC_VERSION}")',
            "  )",
            "  (components",
        ]
        for index, (ref, value, footprint) in enumerate(self._components):
            lines.append(f"    (comp (ref {_quote(ref)})")
            if value is not None:
                lines.append(f"      (value {_quote(value)})")
            if footprint is not None:
                lines.append(f"      (footprint {_quote(footprint)})")
            lines.append(f'      (libsource (lib "Device") (part {_quote(_lib_part(ref))}))')
            lines.append('      (sheetpath (names "/") (tstamps "/"))')
            lines.append(f'      (tstamp "{_tstamp(index)}")')
            lines.append("    )")
        lines.append("  )")
        lines.append("  (nets")
        for code, (name, nodes) in enumerate(zip(self._net_names, self._net_nodes, strict=True)):
            lines.append(f'    (net (code "{code}") (name {_quote(name)})')
            for ref, pin in nodes:
                lines.append(f"      (node (ref {_quote(ref)}) (pin {_quote(pin)}))")
            lines.append("    )")
        lines.append("  )")
        lines.append(")")
        return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Board assembly
# --------------------------------------------------------------------------


@dataclass
class _Component:
    """A component being assembled (pins map pin number -> net name)."""

    ref: str
    value: str | None
    footprint: str | None
    pins: dict[str, str] = field(default_factory=dict)


class _BoardBuilder:
    """Assembles the board model: core -> seeded faults -> healthy cells."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.components: dict[str, _Component] = {}
        self.nets: dict[str, list[tuple[str, str]]] = {}
        # Driver pins (U/J/Q refs), consumed in deterministic order.
        self.driver_pool: list[tuple[str, str]] = []
        self.pool_index = 0
        self.seeded: list[SeededFault] = []
        self.fault_refs: set[str] = set()
        self.fault_nets: set[str] = set()
        self._bus_counter = 0
        self._led_counter = 0
        self._step = 0

    # -- low-level primitives ------------------------------------------------

    def _add_component(self, ref: str, value: str | None, footprint: str | None) -> None:
        if ref in self.components:
            raise ValueError(f"duplicate component reference {ref!r}")
        self.components[ref] = _Component(ref=ref, value=value, footprint=footprint)

    def _connect(self, ref: str, pin: str, net: str) -> None:
        """Attach ``ref.pin`` to ``net`` (registers the net if new)."""
        component = self.components[ref]
        previous = component.pins.get(pin)
        if previous is not None and previous != net:
            raise ValueError(f"{ref} pin {pin} already on {previous!r}, cannot join {net!r}")
        component.pins[pin] = net
        nodes = self.nets.setdefault(net, [])
        if (ref, pin) not in nodes:
            nodes.append((ref, pin))

    def _connect_bus(self, ref: str, bus: str, driver: tuple[str, str]) -> None:
        """Wire ``driver.pin`` and ``ref.pin 1`` to a fresh signal net."""
        self._connect(driver[0], driver[1], bus)
        self._connect(ref, "1", bus)

    def _next_ref(self, prefix: str) -> str:
        """Next free reference for ``prefix`` (skips refs already in use)."""
        counter = 1
        while f"{prefix}{counter}" in self.components:
            counter += 1
        return f"{prefix}{counter}"

    def _new_bus(self) -> str:
        """Fresh signal-net name (deterministic counter)."""
        self._bus_counter += 1
        return f"IO{self._bus_counter}"

    def _pop_driver(self) -> tuple[str, str]:
        """Next driver pin ``(ref, pin)``.

        When the fixed pool is exhausted the board gains an expansion
        driver (another 74HC595-style shift register, wired like the core
        U3) — industrial boards grow their IO through exactly this kind of
        serial expander, so the 500+ scale path stays honest.
        """
        if self.pool_index >= len(self.driver_pool):
            self._add_expansion_driver()
        item = self.driver_pool[self.pool_index]
        self.pool_index += 1
        return item

    def _add_expansion_driver(self) -> None:
        """Add one 74HC595-style expansion IC and push its 12 output pins."""
        ref = self._next_ref("U")
        self._add_component(ref, _U3_VALUE, _FOOTPRINTS["U"])
        self._connect(ref, "16", "3V3")  # VCC
        self._connect(ref, "8", "GND")
        self._connect(ref, "13", "GND")  # !OE tied low = outputs enabled
        for pin in (1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 14):
            self.driver_pool.append((ref, str(pin)))

    def _mark_fault(
        self, ref: str, rule: str, evidence: tuple[str, ...], extra_refs: tuple[str, ...] = ()
    ) -> None:
        """Record a seeded fault and the refs/nets it touches.

        ``extra_refs`` marks companions of the fault (e.g. the undersized
        series resistor of an LED_SERIES_RESISTOR cell) as fault parts too,
        without creating a second finding.
        """
        self.seeded.append(SeededFault(rule=rule, ref=ref, evidence=evidence))
        self.fault_refs.add(ref)
        self.fault_refs.update(extra_refs)
        self.fault_nets.update(net for net in self.components[ref].pins.values())

    # -- topology ------------------------------------------------------------

    def _add_core(self) -> None:
        """Board skeleton: rails, connectors, MCU, regulator, shift register."""
        self._add_component("J1", "Conn_01x02", _FOOTPRINT_J1)
        self._add_component("J2", "Conn_01x10", _FOOTPRINT_JN)
        self._add_component("J3", "Conn_01x10", _FOOTPRINT_JN)
        self._add_component("J4", "Conn_01x10", _FOOTPRINT_JN)
        self._add_component("J5", "Conn_01x10", _FOOTPRINT_JN)
        self._add_component("U1", _U1_VALUE, _FOOTPRINT_U1)
        self._add_component("U2", _U2_VALUE, _FOOTPRINT_U2)
        self._add_component("U3", _U3_VALUE, _FOOTPRINTS["U"])
        self._add_component("L1", _L_VALUE, _FOOTPRINTS["L"])

        # Rails (every rail carries at least one U/J/Q pin: no NO_DRIVER).
        self._connect("J1", "1", "5V")
        self._connect("J1", "2", "GND")
        self._connect("U1", "2", "GND")
        self._connect("U2", "1", "GND")
        self._connect("U3", "8", "GND")
        self._connect("U3", "13", "GND")  # !OE tied low = outputs enabled
        self._connect("U1", "1", "3V3")
        self._connect("U2", "2", "3V3")
        self._connect("J2", "10", "3V3")
        self._connect("U3", "16", "3V3")  # VCC
        self._connect("J4", "10", "12V")
        self._connect("L1", "1", "12V")
        self._connect("L1", "2", "12V_L")
        self._connect("U2", "3", "12V_L")  # regulator input, post-inductor

        # Driver pool (deterministic order).
        for pin in range(3, 21):  # U1 GPIO 3..20
            self.driver_pool.append(("U1", str(pin)))
        for pin in range(1, 10):  # J2 pins 1..9
            self.driver_pool.append(("J2", str(pin)))
        for pin in range(1, 11):  # J3 pins 1..10
            self.driver_pool.append(("J3", str(pin)))
        for pin in range(1, 10):  # J4 pins 1..9
            self.driver_pool.append(("J4", str(pin)))
        for pin in range(1, 11):  # J5 pins 1..10
            self.driver_pool.append(("J5", str(pin)))
        for pin in (1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 12, 14):  # 74HC595 outputs
            self.driver_pool.append(("U3", str(pin)))

    def _add_fault_cells(self, rules: tuple[str, ...]) -> None:
        """Plant the requested fault cells (deterministic refs in the 100 block)."""
        if "LED_NO_LIMITER" in rules:
            for ref in ("LED1", "LED2"):
                self._add_component(ref, _LED_VALUE, _FOOTPRINTS["LED"])
                self._connect(ref, "1", "5V")
                self._connect(ref, "2", "GND")
                self._mark_fault(ref, "LED_NO_LIMITER", (ref, "5V", "GND"))
        if "E_SERIES_COMPLIANCE" in rules:
            for ref, value in (("R101", "333"), ("R102", "4.83k"), ("R103", "2.5k")):
                self._add_component(ref, value, _FOOTPRINTS["R"])
                self._connect_bus(ref, self._new_bus(), self._pop_driver())
                # Return to 3V3 (NOT GND): R pins must stay off GND/5V, the
                # nets of the LED_NO_LIMITER fault cells.
                self._connect(ref, "2", "3V3")
                self._mark_fault(ref, "E_SERIES_COMPLIANCE", (f"{ref}={value}",))
        if "CAP_DERATING" in rules:
            for ref, value, net in (("C101", "100uF 6.3V", "5V"), ("C102", "220uF 10V", "12V")):
                self._add_component(ref, value, _FOOTPRINTS["CE"])
                self._connect(ref, "1", net)
                self._connect(ref, "2", "GND")
                self._mark_fault(ref, "CAP_DERATING", (ref, value, net))
        if "MISSING_FOOTPRINT" in rules:
            self._add_component("R104", "10k", None)  # no footprint: the fault
            self._connect_bus("R104", self._new_bus(), self._pop_driver())
            self._connect("R104", "2", "3V3")  # keep R pins off GND (see E_SERIES)
            self._mark_fault("R104", "MISSING_FOOTPRINT", ("R104",))
            self._add_component("C103", "100nF", None)  # no footprint: the fault
            self._connect_bus("C103", self._new_bus(), self._pop_driver())
            self._connect("C103", "2", "GND")
            self._mark_fault("C103", "MISSING_FOOTPRINT", ("C103",))
        if "FLOATING_NET" in rules:
            self._add_component("R105", "10k", _FOOTPRINTS["R"])
            self._connect_bus("R105", self._new_bus(), self._pop_driver())
            self._connect("R105", "2", "NC_1")  # single connection: floating
            self._mark_fault("R105", "FLOATING_NET", ("NC_1", "R105"))
        if "UNCONNECTED_PIN" in rules:
            for ref in ("R106", "R107"):
                self._add_component(ref, "10k", _FOOTPRINTS["R"])
                self._connect_bus(ref, self._new_bus(), self._pop_driver())
                self._mark_fault(ref, "UNCONNECTED_PIN", (ref, "1"))
        if "LED_SERIES_RESISTOR" in rules:
            self._add_component("LED3", _LED_VALUE, _FOOTPRINTS["LED"])
            self._add_component("R108", "10", _FOOTPRINTS["R"])  # 10 ohm < 22 ohm
            self._connect_bus("R108", "LED3_DRV", self._pop_driver())
            self._connect("R108", "2", "LED3_A")
            self._connect("LED3", "1", "LED3_A")
            self._connect("LED3", "2", "GND")
            self._mark_fault(
                "LED3", "LED_SERIES_RESISTOR", ("LED3", "R108=10"), extra_refs=("R108",)
            )

    # -- healthy cells --------------------------------------------------------

    def _add_led_chain(self) -> int:
        """Healthy LED + series limiter cell (2 comps, consumes 1 driver pin)."""
        r = self._next_ref("R")
        led = self._next_ref("LED")
        self._add_component(r, str(self.rng.choice(_LED_LIMITER_POOL)), _FOOTPRINTS["R"])
        self._add_component(led, _LED_VALUE, _FOOTPRINTS["LED"])
        self._led_counter += 1
        anode = f"LED_A{self._led_counter}"
        bus = self._new_bus()
        self._connect_bus(r, bus, self._pop_driver())
        self._connect(r, "2", anode)
        self._connect(led, "1", anode)
        self._connect(led, "2", "GND")
        return 2

    def _add_filter_cap(self) -> int:
        """Healthy ceramic decoupling cap on 3V3 (1 comp, no driver pin)."""
        c = self._next_ref("C")
        self._add_component(c, self.rng.choice(_FILTER_POOL), _FOOTPRINTS["C"])
        self._connect(c, "1", "3V3")
        self._connect(c, "2", "GND")
        return 1

    def _add_electrolytic(self) -> int:
        """Healthy electrolytic filter on 5V or 12V (1 comp, no driver pin)."""
        c = self._next_ref("C")
        rail = "5V" if self.rng.random() < 0.5 else "12V"
        pool = _ELECT_5V_POOL if rail == "5V" else _ELECT_12V_POOL
        self._add_component(c, self.rng.choice(pool), _FOOTPRINTS["CE"])
        self._connect(c, "1", rail)
        self._connect(c, "2", "GND")
        return 1

    def _add_pullup(self) -> int:
        """Healthy pull-up resistor to 3V3 (1 comp, consumes 1 driver pin)."""
        r = self._next_ref("R")
        self._add_component(r, self.rng.choice(_PULLUP_POOL), _FOOTPRINTS["R"])
        self._connect_bus(r, self._new_bus(), self._pop_driver())
        self._connect(r, "2", "3V3")
        return 1

    def _add_bias(self) -> int:
        """Healthy bias resistor, pull-up style, to 3V3 (1 comp, 1 driver pin).

        Note: returns to 3V3, not GND — R pins must stay off the GND/5V nets
        of the LED_NO_LIMITER fault cells (see ``_add_fault_cells``).
        """
        r = self._next_ref("R")
        self._add_component(r, self.rng.choice(_BIAS_POOL), _FOOTPRINTS["R"])
        self._connect_bus(r, self._new_bus(), self._pop_driver())
        self._connect(r, "2", "3V3")
        return 1

    def _add_signal_cap(self) -> int:
        """Healthy signal filter cap to GND (1 comp, consumes 1 driver pin)."""
        c = self._next_ref("C")
        self._add_component(c, self.rng.choice(_FILTER_POOL), _FOOTPRINTS["C"])
        self._connect_bus(c, self._new_bus(), self._pop_driver())
        self._connect(c, "2", "GND")
        return 1

    def _add_diode(self) -> int:
        """Healthy protection diode (1 comp, consumes 1 driver pin)."""
        d = self._next_ref("D")
        self._add_component(d, _D_VALUE, _FOOTPRINTS["D"])
        self._connect_bus(d, self._new_bus(), self._pop_driver())
        self._connect(d, "2", "GND")
        return 1

    def _add_transistor(self) -> int:
        """Healthy NPN transistor (1 comp, consumes 1 driver pin)."""
        q = self._next_ref("Q")
        self._add_component(q, _Q_VALUE, _FOOTPRINTS["Q"])
        self._connect_bus(q, self._new_bus(), self._pop_driver())
        self._connect(q, "2", "5V")
        self._connect(q, "3", "GND")
        return 1

    def _add_inductor(self) -> int:
        """Healthy inductor (1 comp, consumes 1 driver pin)."""
        lref = self._next_ref("L")
        self._add_component(lref, _L_VALUE, _FOOTPRINTS["L"])
        self._connect_bus(lref, self._new_bus(), self._pop_driver())
        self._connect(lref, "2", "5V")
        return 1

    _CELL_CYCLE: tuple[str, ...] = (
        "led",
        "filter",
        "electrolytic",
        "pullup",
        "bias",
        "signal_cap",
        "diode",
        "transistor",
        "inductor",
    )

    def _add_healthy_cells(self, budget: int) -> None:
        """Fill the remaining component budget with a deterministic cycle.

        The loop counts *actual* components (healthy cells plus any
        expansion drivers they triggered), so the final component count is
        exactly ``budget``. Two guards keep the fill exact:

        - a 2-component LED chain never takes the last single slot
          (``remaining == 1``) — swapped for a 1-component filter cap;
        - an LED chain is also swapped when only 2 slots remain and the
          driver pool is empty (the chain would add its own components
          *plus* an expansion driver = 3 components).
        """
        cycle = {
            "led": self._add_led_chain,
            "filter": self._add_filter_cap,
            "electrolytic": self._add_electrolytic,
            "pullup": self._add_pullup,
            "bias": self._add_bias,
            "signal_cap": self._add_signal_cap,
            "diode": self._add_diode,
            "transistor": self._add_transistor,
            "inductor": self._add_inductor,
        }
        while len(self.components) < budget:
            remaining = budget - len(self.components)
            kind = self._CELL_CYCLE[self._step % len(self._CELL_CYCLE)]
            if kind == "led" and (
                remaining == 1 or (remaining == 2 and self.pool_index >= len(self.driver_pool))
            ):
                kind = "filter"  # cheap 1-component cell: keeps the count exact
            cycle[kind]()
            self._step += 1

    # -- manifest ------------------------------------------------------------

    def _predict_no_driver(self) -> list[PredictedFinding]:
        """NO_DRIVER mirror over the board model (audit semantics, from the
        model — kept honest by ``test_industrial.py``, which fails loudly if
        the two implementations diverge)."""
        findings: list[PredictedFinding] = []
        for name in sorted(self.nets):
            nodes = self.nets[name]
            if not nodes:
                continue
            if name.upper() in POWER_NET_NAMES:
                continue
            refs = sorted({ref for ref, _ in nodes})
            if all(ref[0] in PASSIVE_PREFIXES for ref in refs) and not any(
                ref[0] in DRIVER_PREFIXES for ref in refs
            ):
                findings.append(PredictedFinding("NO_DRIVER", (name, *refs)))
        return findings


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def generate_board(
    n_components: int = 75,
    seed: int = DEFAULT_SEED,
    faults: list[str] | tuple[str, ...] | None = None,
    source_path: str = DEFAULT_SOURCE_PATH,
    date: str = DEFAULT_DATE,
) -> Board:
    """Generate a deterministic synthetic netlist with seeded faults.

    Args:
        n_components: Total number of components on the board. Must leave
            room for the fixed core plus every requested fault cell (a
            ``ValueError`` is raised otherwise). Use e.g. 500 for the
            industrial scale path.
        seed: Determinism key — the same ``(n_components, seed)`` pair
            always renders the same board; different seeds vary the healthy
            values. ``0`` regenerates the versioned ``industrial-74`` fixture.
        faults: Rule ids to seed (see :data:`ALL_FAULT_RULES`); ``None``
            seeds every rule. Unknown ids raise ``ValueError``.
        source_path: ``(source ...)`` string in the design block
            (informational; the default matches the versioned fixture).
        date: ``(date ...)`` string in the design block.
    """
    unknown = [rule for rule in (faults or ALL_FAULT_RULES) if rule not in ALL_FAULT_RULES]
    if unknown:
        raise ValueError(f"unknown fault rule(s): {', '.join(sorted(unknown))}")
    rules = tuple(ALL_FAULT_RULES if faults is None else faults)

    builder = _BoardBuilder(random.Random(seed))
    builder._add_core()  # noqa: SLF001 -- internal composition step
    if rules:
        builder._add_fault_cells(rules)
    if n_components < len(builder.components):
        raise ValueError(
            f"n_components={n_components} leaves no room for the {_CORE_COMPONENT_COUNT}"
            f"-component core plus {len(builder.components) - _CORE_COMPONENT_COUNT} fault"
            f" component(s); minimum is {len(builder.components)}"
        )
    builder._add_healthy_cells(n_components)

    seeded = tuple(builder.seeded)
    predicted = tuple(
        sorted(seeded + tuple(builder._predict_no_driver()), key=lambda f: (f.rule, f.evidence))
    )
    return Board(
        seed=seed,
        n_components=n_components,
        fault_rules=rules,
        source_path=source_path,
        date=date,
        component_count=len(builder.components),
        net_count=len(builder.nets),
        seeded=seeded,
        expected=predicted,
        fault_refs=frozenset(builder.fault_refs),
        fault_nets=frozenset(builder.fault_nets),
        _components=tuple(
            (comp.ref, comp.value, comp.footprint) for comp in builder.components.values()
        ),
        _net_names=tuple(builder.nets),
        _net_nodes=tuple(tuple(nodes) for nodes in builder.nets.values()),
    )


def generate_netlist(
    n_components: int = 75,
    seed: int = DEFAULT_SEED,
    faults: list[str] | tuple[str, ...] | None = None,
    source_path: str = DEFAULT_SOURCE_PATH,
    date: str = DEFAULT_DATE,
) -> str:
    """Return the netlist text for :func:`generate_board`.

    ``generate_netlist(75)`` reproduces the versioned
    ``tests/fixtures/industrial-74.kicad_net`` byte for byte.
    """
    return generate_board(
        n_components=n_components,
        seed=seed,
        faults=faults,
        source_path=source_path,
        date=date,
    ).render()


def _quote(text: str) -> str:
    """Render a string as a double-quoted S-expression atom."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _tstamp(index: int) -> str:
    """Deterministic UUID-style timestamp for a component."""
    return f"66e60000-0000-0000-0000-00000000{index:04x}"


def _lib_part(ref: str) -> str:
    """Symbol part name for the (informational) libsource block."""
    if ref.startswith("LED"):
        return "LED"
    if ref.startswith(("R", "C", "D", "Q", "L")):
        return ref[0]
    return ref
