# Firmware generation

`pcbai.firmware` turns a **template choice plus validated parameters** into
STM32 HAL / Arduino initialization code. It is the deterministic half of the
project's two-layer design: the ReAct agent (Layer 2) picks a template and
parameters, and the generator renders it deterministically — the LLM never
writes firmware code; this layer renders fixed templates and statically
validates the result.

Three modules:

| Module | Responsibility |
|---|---|
| `pcbai.firmware.templates` | The catalog of 8 templates + strict parameter contracts |
| `pcbai.firmware.generator` | `generate_firmware(spec)` → grounded `FirmwareArtifact` |
| `pcbai.firmware.validator` | Static, non-compiling checks over an artifact |
| `pcbai.firmware.compile_check` | Real-compiler syntax check (`gcc`/`g++`) over artifacts against bundled HAL/Arduino stubs |

## Template catalog

| Template | Target | Category | Required params | Optional params |
|---|---|---|---|---|
| `stm32_gpio_output` | `stm32-hal` | gpio | `port`, `pin` | `mode`, `pull`, `speed`, `initial_state`, `hal_header` |
| `stm32_pwm_timer` | `stm32-hal` | pwm | `timer`, `channel`, `port`, `pin`, `prescaler`, `period` | `pulse_percent`, `hal_header` |
| `stm32_adc_poll` | `stm32-hal` | adc | `adc`, `channel`, `port`, `pin` | `samples`, `vref_mv`, `resolution_bits`, `hal_header` |
| `stm32_uart_init` | `stm32-hal` | uart | `uart`, `baudrate` | `port`, `tx_pin`, `rx_pin`, `word_length`, `parity`, `stop_bits`, `hal_header` |
| `arduino_gpio_output` | `arduino` | gpio | `pin` | `initial` |
| `arduino_pwm_analogwrite` | `arduino` | pwm | `pin`, `duty` | — |
| `arduino_adc_read` | `arduino` | adc | `pin` | `vref_mv` |
| `arduino_i2c_scan` | `arduino` | i2c | `sda_pin`, `scl_pin` | `clock_hz` |

Every template also accepts the same four optional keys:

- `label` — a human label rendered as a trailing comment.
- `ref` / `ref_pin` / `net` — **grounding hints**: the KiCad component,
  its pin and the net that justify the chosen pin (validated when
  `design_path` is set; rendered as a `Grounding:` comment).

`list_templates()` returns this catalog as machine-readable dicts for the
agent/MCP layer; `get_template(name)` returns one `Template`. An unknown
name raises `UnknownTemplateError`.

### Parameter rules (strict, before any render)

`Template.render(params)` validates first and renders only then — a bad
call never produces partial code:

- **Required** params must be present (`TemplateParamError` naming the
  missing keys).
- **Unexpected** keys are rejected (typos fail loudly instead of being
  silently ignored).
- **Values** are type- and range-checked: GPIO banks `GPIOA`..`GPIOH`,
  pins `GPIO_PIN_0`..`GPIO_PIN_31` (or `PA5`-style in the validator),
  timers `TIM1`..`TIM8`/`TIM12`..`TIM17`, prescaler 0..65535, period
  1..65535, baudrate 1..12 000 000, duty 0..255, etc. Integers may be
  passed as `"115200"` / `115200.0`; floats where an integer is required
  are rejected.

## `FirmwareSpec` contract

```python
@dataclass(frozen=True)
class FirmwareSpec:
    target: str  # "stm32-hal" | "arduino"
    template: str  # name from the catalog
    params: dict[str, Any]
    design_path: str | None = None
```

Rules enforced by `generate_firmware(spec)`:

1. The template must exist (`UnknownTemplateError`).
2. `spec.target` must equal the template's target (`TargetMismatchError`),
   so a template can never be emitted as the wrong kind of firmware.
3. `spec.params` must satisfy the template contract (`TemplateParamError`).
4. If `design_path` is set, the KiCad netlist is parsed and every
   grounding hint must exist in it (`GroundingError`); the generator never
   invents components, nets or pins. File/parse problems also surface as
   `GroundingError` with a clear message.

## `FirmwareArtifact` contract

| Field | Meaning |
|---|---|
| `filename` | `main.c` (STM32) or `sketch.ino` (Arduino) |
| `code` | Deterministic template output (same params → byte-identical) |
| `target` / `template` | Provenance of the artifact |
| `params` | A copy of the accepted parameters |
| `grounding` | e.g. `["LED1 (ref)", "LED1 pin 1 (pin)", "LED_A (net)"]`, sorted; empty without `design_path` |
| `requires_human_review` | **Always `True` in v1** |
| `review_notes` | Concrete human checks (pin conflicts, baudrate, PWM frequency, I2C pull-ups…) |

## Human review gate (v1)

Nothing produced by this layer is considered flash-ready:

- `requires_human_review` is hard-coded `True` for every artifact.
- `review_notes` lists target- and category-specific checks to perform —
  for example *“verify GPIOA/GPIO_PIN_5 matches the schematic net and is
  not used by another peripheral”*.
- The validator is **static**: it never compiles or executes the code, so
  a passing report is a sanity check, not a guarantee. Compiling with the
  real toolchain and reviewing the pin map remain the human's job.

This mirrors the pattern proven in `plc-ai-agent`: the LLM decides, the
code generates, the validator checks, the human approves.

## Validator checks

`validate_firmware(artifact)` never compiles anything; it returns:

```python
@dataclass(frozen=True)
class ValidationReport:
    ok: bool  # True when there are no "error" issues
    issues: list[ValidationIssue]  # severity, code, message
    checks_run: list[str]  # the five check ids below
```

| Code | Severity | What it catches |
|---|---|---|
| `UNBALANCED_BRACES` | error | Unbalanced `{}`, `()`, `[]` (tokenizer skips comments/strings) |
| `MISSING_INCLUDE` | error / warning | `HAL_` code without `"main.h"`/`"stm32*.h"`; `pinMode`/`analogWrite` outside a `.ino` file |
| `HAL_FUNCTION_UNKNOWN` | warning | `HAL_*`/`__HAL_*` calls outside the documented whitelist |
| `PIN_SCHEMA` | warning | Pin/port params not matching the target's pin grammar |
| `TEMPLATE_MISMATCH` | error / warning | Artifact target ≠ template target; unknown template |

**Known limitations**: the balancing tokenizer ignores comments and
strings but does not expand preprocessor macros or understand C++
templates, and any `HAL_*` mention inside a comment is treated as a call
reference. The validator is a best-effort static layer, not a C parser.

## Compile-check (real compiler)

`compile_check(artifacts)` is the opt-in counterpart to the static
validator: it feeds artifacts to the **system compilers** and reports
whether they parse. It is deliberately separate — the validator is a
dependency-free deterministic gate that runs anywhere, while the
compile-check needs a toolchain:

- STM32 HAL artifacts compile as **C11** with `gcc -std=c11 -fsyntax-only
  -Wall -Werror`; Arduino sketches as **C++11** with `g++ -std=c++11
  -fsyntax-only -Wall -Werror -include Arduino.h` (the `-include` mirrors
  how the Arduino toolchain injects its core).
- No vendor SDK is needed: the check compiles against the bundled,
  100 % own stubs in `src/pcbai/firmware/stubs/` (`stm32f4xx_hal.h`,
  `stm32f1xx_hal.h` bridge, `main.h`, `Arduino.h`, `Wire.h`). They cover
  exactly the HAL/Arduino surface the 8 templates use — types, instance
  macros, init structs and functions — so artifacts get a genuine parser
  pass without vendoring the real STM32 HAL or Arduino core.
- Artifacts are written to a throwaway temporary directory (one per
  artifact), compiled, and the directory is removed before returning. The
  check is deterministic and never modifies the artifact.
- A missing compiler short-circuits into a `CompileReport.error` message
  instead of a partial result set; every artifact is compiled even when an
  earlier one failed, so one run lists all breakages.

```python
from pcbai.firmware import FirmwareSpec, generate_firmware, compile_check

artifact = generate_firmware(
    FirmwareSpec(
        target="stm32-hal",
        template="stm32_gpio_output",
        params={"port": "GPIOA", "pin": "GPIO_PIN_5", "label": "LED"},
    )
)
report = compile_check([artifact])
assert report.ok, report.results[0].stderr
```

What this is **not**: a syntax check with minimal stubs is not a link, not
a flash-ready build, and does not validate against the vendor toolchain's
headers or libraries. Human review (`requires_human_review=True`) and a
real build remain mandatory before flashing.

## Usage

```python
from pcbai.firmware import FirmwareSpec, generate_firmware, validate_firmware

artifact = generate_firmware(
    FirmwareSpec(
        target="stm32-hal",
        template="stm32_gpio_output",
        params={"port": "GPIOA", "pin": "GPIO_PIN_5", "ref": "LED1", "net": "LED_A"},
        design_path="tests/fixtures/simple-led.kicad_net",
    )
)
report = validate_firmware(artifact)
assert report.ok and artifact.requires_human_review
print(artifact.code)
```
