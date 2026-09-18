# Architecture — pcb-ai-agent

## Overview

pcb-ai-agent is a two-layer local AI agent for KiCad PCB designs.  No data
leaves the machine; no cloud API is involved.

```
┌──────────────────────────────────────────────────────────────────────┐
│  Layer 1 — Read (evidence)                                          │
│                                                                     │
│  .kicad_net / .kicad_sch                                            │
│       │                                                             │
│       ▼                                                             │
│  S-expression parser  (sexpr.py — 0 deps)                          │
│       │                                                             │
│       ▼                                                             │
│  Netlist models  (Component / Net / Design)                         │
│       │                                                             │
│       ▼                                                             │
│  Design services: audit  sizing  BOM                                │
│       │                                                             │
│       ▼                                                             │
│  MCP read-only server  (7 tools, stdin/stdout)                      │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│  Layer 2 — Write (assisted generation)                              │
│                                                                     │
│  User question                                                      │
│       │                                                             │
│       ▼                                                             │
│  ReAct agent loop  (PcbAgent — max 6 steps)                         │
│       │                                                             │
│       ▼                                                             │
│  LLM picks tool + arguments  (Ollama qwen2.5-coder)                 │
│       │                                                             │
│       ▼                                                             │
│  Tool dispatch → Layer 1 pure functions                              │
│       │                                                             │
│       ▼                                                             │
│  Deterministic grounding  (validate_answer)                         │
│       │                                                             │
│       ▼                                                             │
│  Human review gate  (requires_human_review = True)                   │
└──────────────────────────────────────────────────────────────────────┘
```

## Layer 1 — Read (evidence)

The read layer is a pure, deterministic pipeline that converts raw KiCad files
into structured design data and then runs design-rule checks, calculations and
a bill of materials — all without an LLM.

### Data flow

```
.kicad_net / .kicad_sch
  │
  ▼
sexpr.py          S-expression parser — dependency-free, precise
  │               line/column error reporting.  KiCad stores everything
  │               as S-expressions; this is the only parser in the stack.
  │
  ▼
netlist.py        Design models (Component, Net, Design).
                  parse_netlist() handles KiCad netlist v"E" (Eeschema
                  6.x–8.x).  parse_schematic() covers the documented
                  .kicad_sch subset (symbols, wires, labels, junctions).
  │
  ├───────────────►  sizing.py       Closed-form board math (Ohm's law
  │                 ▲                + E12 preferred-value rounding).
  │                 │                LED series resistor, I2C pull-up,
  │                 │                voltage divider, decoupling cap.
  │                 │                Numeric values ALWAYS come from code.
  │
  ├───────────────►  audit.py        Evidence-based audit rules.
  │                 ▲                v1 rules: FLOATING_NET,
  │                 │                UNCONNECTED_PIN, MISSING_VALUE,
  │                 │                MISSING_FOOTPRINT, NO_DRIVER,
  │                 │                LED_NO_LIMITER.
  │                 │                v2 rules: E_SERIES_COMPLIANCE
  │                 │                (E6/E12/E24/E96 preferred
  │                 │                values), LED_SERIES_RESISTOR,
  │                 │                CAP_DERATING.
  │                 │
  ├───────────────►  bom.py          Deterministic BOM: one row per
  │                 ▲                component, sorted by ref, plus
  │                 │                an aligned ASCII table.
  │                 │
  └───────────────►  mcp/server.py   7 read-only tools exposed over
                                     MCP stdio.  Thin wrappers around
                                     the pure functions above.
```

## Layer 2 — Write (assisted generation)

The agent layer adds a local LLM (Ollama) on top of the read layer.  The LLM
**decides** which tool to call and with what arguments; the code **calculates**
every result.  A deterministic grounding gate then validates the final answer
before it reaches the user.

### Agent loop

```
User question
  │
  ▼
PcbAgent.run()           Bounded by max_steps (6) and run_timeout (300 s).
  │
  ├─► OllamaChatClient   POST to OLLAMA_HOST/api/chat (stdlib urllib,
  │   .chat()             native function calling, 2 retries on transient
  │                       errors, fresh Request per attempt).
  │
  ├─► tool_calls?  ──yes──► dispatch to pcbai.mcp.server.* pure functions
  │       │  no              (load_design, list_components, list_nets,
  │       ▼                  check_connectivity, size_resistor,
  │   final answer           generate_bom, audit_design).
  │       │
  │       ▼
  │   validate_answer()     3 checks:
  │       │                 1. REF_IN_ANSWER — refs exist in design/tools
  │       │                 2. NUMBERS_FROM_SIZING — values match tool output
  │       │                 3. NO_DATA_NO_CLAIM — no tools = no claims
  │       ▼
  └─► AgentTrace(question, steps, final_answer, grounded, truncated)
```

### Firmware generation (deterministic)

The firmware sub-system is the one case where the agent *generates code*, but
it does so through deterministic templates — never by asking the LLM to write
firmware directly.

```
LLM picks template + parameters
  │
  ▼
templates.py       8 fixed templates (STM32 HAL + Arduino).
  │                 Strict parameter contract validated BEFORE render.
  ▼
generator.py       Renders the template into a FirmwareArtifact.
  │                 Optional grounding: ref/ref_pin/net checked against
  │                 the parsed netlist (rejects invented components).
  ▼
validator.py       Static checks: UNBALANCED_BRACES, MISSING_INCLUDE,
  │                 HAL_FUNCTION_UNKNOWN, PIN_SCHEMA, TEMPLATE_MISMATCH.
  ▼
Human review       Every artifact: requires_human_review = True.
```

## Module map

| Module | File | Responsibility |
|---|---|---|
| `pcbai.kicad.sexpr` | `src/pcbai/kicad/sexpr.py` | Dependency-free S-expression parser with line/column error reporting |
| `pcbai.kicad.netlist` | `src/pcbai/kicad/netlist.py` | `Component`, `Net`, `Design` models; `parse_netlist()` and `parse_schematic()` |
| `pcbai.design.sizing` | `src/pcbai/design/sizing.py` | Closed-form board math with E12 preferred-value rounding |
| `pcbai.design.audit` | `src/pcbai/design/audit.py` | Evidence-based design audit rules over a `Design` |
| `pcbai.design.bom` | `src/pcbai/design/bom.py` | Deterministic bill of materials + ASCII table renderer |
| `pcbai.mcp.server` | `src/pcbai/mcp/server.py` | MCP stdio server: 7 read-only tools wrapping the pure functions |
| `pcbai.agent.llm_client` | `src/pcbai/agent/llm_client.py` | stdlib-only Ollama chat client with retries |
| `pcbai.agent.react_agent` | `src/pcbai/agent/react_agent.py` | ReAct agent loop (`PcbAgent`) + 7-tool dispatch table |
| `pcbai.agent.grounding` | `src/pcbai/agent/grounding.py` | Deterministic anti-hallucination validation of the final answer |
| `pcbai.firmware.templates` | `src/pcbai/firmware/templates.py` | Catalog of 8 fixed firmware templates + strict parameter contracts |
| `pcbai.firmware.generator` | `src/pcbai/firmware/generator.py` | Template renderer + optional netlist grounding |
| `pcbai.firmware.validator` | `src/pcbai/firmware/validator.py` | Static, non-compiling code checks |
| `pcbai.eval.golden` | `src/pcbai/eval/golden.py` | Golden dataset models + strict JSONL loaders |
| `pcbai.eval.judge` | `src/pcbai/eval/judge.py` | `HeuristicJudge` (CI) + `LLMJudge` (opt-in) |
| `pcbai.eval.runner` | `src/pcbai/eval/runner.py` | `run_evals`, `check_regression`, `write_baseline` |

## Data flow — the simple-led fixture

The committed `tests/fixtures/simple-led.kicad_net` is a minimal KiCad design
(3 components, 3 nets) that exercises the full pipeline end-to-end:

```
simple-led.kicad_net
  │
  ▼
sexpr.parse() ──► netlist.parse_netlist()
  │
  ├─► design.components = {J1, R1, LED1}
  ├─► design.nets = {5V, GND, LED_A}
  │
  ├─► audit: NO_DRIVER advisory on passive-only net LED_A
  ├─► sizing: size_resistor(5.0, 2.0, 0.02) → 150 Ω, 0.06 W
  ├─► bom: 3 rows, J1/R1/LED1
  │
  └─► agent sees: "The LED needs 150 Ω (E12)"
      grounding: REF_IN_ANSWER ✓  NUMBERS_FROM_SIZING ✓
```

## Anti-hallucination invariants

Three non-negotiable rules keep the LLM from fabricating design facts:

1. **The LLM decides, the code calculates.**  Numeric values (resistance,
   power, voltage ratios) always come from `sizing.py` — never from the model's
   training data.  The grounding gate verifies every number in the final answer
   against tool results.

2. **The LLM picks templates; the code never writes firmware.**  Firmware
   generation is a fixed set of 8 parameterised templates.  The model selects a
   template and fills in parameters; `generator.py` renders the code.  The model
   never sees a blank editor.

3. **Audit findings are grounded in evidence.**  Every audit rule carries a
   stable rule id, a severity, and the concrete refs/nets that triggered it.
   The agent reports these facts; it does not invent additional findings.
