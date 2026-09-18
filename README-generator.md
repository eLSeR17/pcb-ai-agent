# Synthetic industrial board generator

`src/pcbai/design/generator.py` builds **deterministic synthetic KiCad
netlists with known, seeded design faults**. It exists to validate the audit
pipeline at industrial scale (500+ components) against boards with *known*
defects — before pointing the auditor at real industrial designs — without
needing a single real schematic.

The versioned fixture `tests/fixtures/industrial-74.kicad_net` is the 75-
component demo board this generator produces with the default parameters
(seed `0`). It is a **ground truth**, not a hand-authored blob:

```bash
python3 -c "import src.pcbai.design.generator as g; print(g.generate_netlist(75) == open('tests/fixtures/industrial-74.kicad_net').read())"
# True — the fixture regenerates byte for byte
```

`tests/test_industrial.py` verifies this contract: the fixture parses, the
auditor finds **exactly** the seeded faults (rule + evidence), no healthy net
is flagged, and the same guarantees hold on the 500-component scale path.

## Why a generator (instead of more hand-made fixtures)

- **Regeneration as verification**: the fixture is the *output* of the
  generator, so the test suite proves parser, generator and auditor agree.
- **Ground-truth manifest**: every seeded fault is recorded in the returned
  `Board` (`board.seeded` and `board.expected`), independently of the
  auditor — a divergence between the two fails the test, not a demo.
- **Scale for free**: `n_components` is a parameter. 500 components exercise
  the same parser, the same rules and the same manifest contract as 75.

## Fault catalogue

By default *all* fault rules are seeded. The board stays ~81 % healthy
(61 of 75 components fault-free) so a report shows a realistic mix of
severities instead of an all-red wall.

| Rule (audit id) | Severity | Ref | Seeded condition |
|---|---|---|---|
| `LED_NO_LIMITER` | warning | `LED1`, `LED2` | LED straight to 5V/GND, no series resistor |
| `E_SERIES_COMPLIANCE` | warning | `R101` `333`, `R102` `4.83k`, `R103` `2.5k` | non-E6/E12/E24/E96 values (4700 would pass! use these) |
| `CAP_DERATING` | info | `C101` `100uF 6.3V`@5V, `C102` `220uF 10V`@12V | electrolytic rated under 1.5× the rail |
| `MISSING_FOOTPRINT` | error | `R104`, `C103` | component without a footprint line |
| `FLOATING_NET` | warning | `R105` → `NC_1` | a net with a single connection |
| `UNCONNECTED_PIN` | warning | `R106`, `R107` | resistor wired through one pin only |
| `LED_SERIES_RESISTOR` | warning | `LED3` + `R108` `10` | limiter below the 22 ohm minimum |

14 fault components, 13 seeded findings (the LED_PIN defect involves two
parts). The audit additionally fires 8 info-level `NO_DRIVER` advisories on
the resistor+LED anode nets — those are also predicted by the manifest, so
the full expected finding set is 21 (2 errors, 9 warnings, 10 infos).

## Wiring constraints (important for future edits)

The auditor's `LED_NO_LIMITER` rule treats an LED as "safe" when *any* of its
nets contains a ref starting with `R`. Because the healthy cells return
resistors to GND, **R pins must never sit on the `GND` or `5V` nets** used by
the fault LEDs, or their defect is masked. The generator respects this:
healthy bias resistors and the E_SERIES/MISSING_FOOTPRINT fault returns hang
off `3V3` instead of GND. Keep that invariant when adding new cells.

## Scale path (`n_components` ≥ 75)

Fixed board skeleton: J1–J5 connectors, U1 (STM32F030), U2 (AMS1117-3.3),
U3 (74HC595), L1 input filter, plus the fault cells; everything else is a
deterministic cycle of healthy cells (LED chains, filters, pull-ups, bias,
signal caps, diodes, transistors, inductors). Every signal net needs a real
driver pin, so when the fixed driver pool (U1/J2–J5/U3, 68 pins) is
exhausted the generator adds **expansion 74HC595 shift registers** — the same
IO-expansion pattern real boards use — keeping each net driven without
inventing fake U/J/Q refs.

- **Determinism**: same `(n_components, seed)` → same board, byte for byte.
  Different seeds vary the healthy *values* (E12/E24 pools) but never the
  topology or the faults.
- **Exact counts**: `generate_board(500, seed=0)` yields exactly 500
  components; the healthy fill loop counts expansion drivers it triggers.

```python
from pcbai.design.generator import generate_board, generate_netlist

board = generate_board(500, seed=0)          # 500 components, 379 nets
netlist_text = generate_netlist(500, seed=0)  # parser-ready
board.expected                                # ground-truth findings
```

## API

```python
generate_board(
    n_components: int = 75,
    seed: int = 0,
    faults: list[str] | None = None,   # None = ALL_FAULT_RULES
    source_path: str = "/pcb-ai-agent/tests/fixtures/industrial-74.kicad_net",
    date: str = "2026-09-19T09:00:00+02:00",
) -> Board

generate_netlist(...) -> str           # the rendered netlist text
```

- `faults=["E_SERIES_COMPLIANCE"]` seeds only that rule (selective fault
  injection for rule-level validations).
- Unknown rule ids raise `ValueError`; `n_components` below the minimum
  (23 = 9 core + 14 fault components) raises `ValueError`.

## Regenerating the fixture

```bash
python3 - <<'PY'
from pcbai.design.generator import generate_netlist
open("tests/fixtures/industrial-74.kicad_net", "w").write(generate_netlist(75, seed=0))
PY
```

The committed file must always equal `generate_netlist(75, seed=0)` — if you
change the generator (new cell, new fault rule), regenerate the fixture and
update `tests/test_industrial.py` accordingly.

## Running the validation

```bash
cd <repo-root>/staging && python -m pytest tests/test_industrial.py -q   # 18 tests
cd <repo-root>/staging && python -m pytest tests -q                       # full suite
```