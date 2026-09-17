# Evals — golden dataset, dual judge, regression guard

> The eval harness follows the same pattern as the companion portfolio
> projects. Deterministic CI runs over a hand-built golden dataset; the LLM
> judge is an **opt-in** review aid.

## 1. Why

The project invariant is *"the code calculates, the LLM decides"* — the
LLM never computes numbers or invents findings. That makes the read layer
(sizing math + audit rules) the **single source of truth** every
agent answer must match. The harness locks it down: when the sizing formulas
or the audit rules change, `run_evals.py` must still score 1.0 against
the golden cases — or a regression is reported.

## 2. Components

```
data/golden/
├── sizing_cases.jsonl   # 15 hand-computed sizing cases (4 functions, 15 examples)
├── audit_cases.jsonl    # 6 audit cases over the committed KiCad fixtures
└── baseline.json        # written with --write-baseline (regression guard)
src/pcbai/eval/
├── __init__.py          # public exports
├── golden.py            # SizingCase / AuditCase / GoldenSet + JSONL loaders
├── judge.py             # HeuristicJudge (CI) + LLMJudge (opt-in)
└── runner.py            # run_evals / check_regression / write_baseline
scripts/run_evals.py     # CLI (exit 0/1 for CI)
```

## 3. Golden dataset

### Sizing cases (`sizing_cases.jsonl`)

One line per case: `{"case_id", "description", "function", "inputs",
"expected", "why"}`. Covered ground:

| Function | Cases | Example |
|---|---|---|
| `led_series_resistor` | 5 | 5 V, 2.0 V LED, 20 mA → `150 Ω`, `0.06 W` |
| `pull_up_resistor` | 3 | 200 µA, min logic-high 2.36 V, 3.3 V → `4.7 kΩ` |
| `voltage_divider` | 3 | 3.3 V over 10k/10k → `1.65 V` out, 50 % ratio |
| `decoupling_capacitor` | 4 | 16 MHz/1 mA → `1.2 nF` |

- Every expected value is **hand-computed** (formula + E12 preferred-value
  rounding) and stored already-rounded; the judge compares within a
  relative tolerance of `1e-6` (default per case) that only absorbs binary
  float noise.
- `expected` may be a single number or a list of numbers (pair-returning
  functions store both values, e.g. `[150, 0.06]` for R and P). The judge
  validates the shape against the actual output at scoring time — a shape
  mismatch is a loud failure, not a silent truncation.
- The `why` field documents the hand calculation: auditors can re-derive
  any expectation without running the code.

### Audit cases (`audit_cases.jsonl`)

```json
{"case_id", "description", "fixture", "expected_rules": [],
 "expected_severities": {}, "allow_extra_rules": false, "why"}
```

| Case | Fixture | Expected |
|---|---|---|
| `audit_simple_led_schematic_clean` | `simple-led.kicad_sch` | `[]` (clean, strict) |
| `audit_simple_led_netlist_advisory` | `simple-led.kicad_net` | `[NO_DRIVER]` (info) |
| `audit_minimal_mcu_clean` | `minimal-mcu.kicad_net` | `[]` (clean, strict) |
| `audit_no_footprint_not_manufacturable` | `no-footprint.kicad_net` | `[MISSING_FOOTPRINT]` (error), extras allowed |
| `audit_bad_led_missing_limiter` | `bad-led.kicad_net` | all 5 fired rules **with severities** (strict) |
| `audit_bad_led_focus_star_rule` | `bad-led.kicad_net` | `[LED_NO_LIMITER]` (warning), extras allowed |

Design decision: the same mini-design in both formats is intentional —
`schema → []` plain clean, while the netlist version carries the
`NO_DRIVER` advisory, so the harness covers both "clean" and "advisory"
interpretations instead of papering one over.

## 4. Heuristic judge (CI-safe)

Fully deterministic, zero network — **this is what CI runs**.

### Sizing scoring

PASS iff every expected value matches its actual within the case's
relative tolerance (scale `max(|expected|, 1e-12)` so near-zero values
never false-fail). Score: `1.0` pass, `0.0` fail.

### Audit scoring

```
recall    = |expected_rules ∩ detected| / |expected_rules|   (1.0 if expected empty)
extras    = detected - expected_rules
penalty   = 0.25 × len(extras not allowed) + 0.25 × severity mismatches
score     = clamp(recall - penalty, 0, 1)
PASS      = recall == 1.0 AND no severity mismatch AND (no extras OR allow_extra_rules)
```

- **Extras policy (strict, spec-literal)**: by default an extra rule is a
  regression signal (-0.25 each, no pass). `allow_extra_rules: true`
  marks cases whose intent is "focus on the star rule" (e.g. future v2
  audit rules firing on an old fixture) — extras then never fail the case,
  matching the companion-projects contract.
- **Severity contract**: when a case declares `expected_severities`, the
  detected severity must match exactly (warning ≠ error).

## 5. LLM judge (opt-in, NOT CI)

`python3 scripts/run_evals.py --llm-judge` wraps the same run with
`LLMJudge` (`OllamaChatClient`, local Ollama in the Docker
network). The model rates the quality of the *candidate output text* for
each case on a 0–5 scale; the verdict passes only when **the heuristic
passes AND** the normalised LLM score ≥ 3/5.

Honest degradation (never breaks a run): an unreachable Ollama, an error,
or an unparseable reply falls back to the **heuristic verdict** with the
failure recorded in `details`. Rationale: the LLM
judge is a review aid for human engineers, never a CI dependency.

## 6. Regression guard

`baseline.json` stores the score of an approved run. Every later run
fails CI when its score drops more than `REGRESSION_TOLERANCE` (0.02)
below the baseline — even when every case still passes individually.

- Missing baseline → **not a failure** (first-run mode; the message tells
  you to run `--write-baseline`).
- Unreadable/corrupt baseline → **failure** (a broken guard must fail
  loudly so it gets re-written).
- `write_baseline` refuses to persist a failing run — a baseline is only
  ever written from a green state.

## 7. CLI usage

```bash
python3 scripts/run_evals.py                 # heuristic, CI-safe; exit 0 = green
python3 scripts/run_evals.py --write-baseline   # persist current score as baseline
python3 scripts/run_evals.py --llm-judge        # opt-in qualitative judge
python3 scripts/run_evals.py --golden-dir data/golden --baseline data/golden/baseline.json
```

Exit codes: `0` all cases passed **and** no regression (or no baseline);
`1` any failure, regression, or corrupt baseline. Intended for a CI step
(`pytest` for unit tests + `run_evals.py` for the behavioral guard).

## 8. Limitations (honest)

- Golden coverage is a curated snapshot, not a property test: it proves
  the read layer matches **documented** expectations on representative
  designs, not mathematical exhaustiveness (sizing is trivial closed-form
  math; audit rules are intentionally v1).
- `expected` values are stored E12-rounded — the harness validates the
  *user-facing* engineering value, by design.
- Audit findings depend on exact rule ids and severities; a deliberate
  rework of the rules (e.g. `NO_DRIVER` promoting severity) is an expected
  class of change: update the affected entries in `audit_cases.jsonl`,
  re-run, re-baseline.