![CI](https://github.com/eLSeR17/pcb-ai-agent/actions/workflows/ci.yml/badge.svg)

# pcb-ai-agent

A fully local AI agent for KiCad PCB designs — evidence-based board audit,
deterministic sizing and firmware generation with an anti-hallucination gate.
No cloud API, no data leaves your machine.

## Problem

Electronic designers using KiCad catch signal-integrity and connectivity errors
late in the cycle.  Writing peripheral init firmware (STM32 HAL, Arduino) by
hand is repetitive and error-prone.  Off-the-shelf LLMs hallucinate resistor
values, fabricate component references and invent pin assignments — dangerous
when the output drives real hardware.

## Solution

pcb-ai-agent is a two-layer local agent:

- **Layer 1 (read)**: parses KiCad netlists and schematics, runs evidence-based
  audit rules, computes component sizing with E12 preferred values, and generates
  a bill of materials.  Every number comes from code — never from an LLM.

- **Layer 2 (write)**: a local ReAct agent (Ollama qwen2.5-coder:7b) selects
  tools and parameters; the code executes them.  A deterministic grounding gate
  validates every answer before it reaches the user.  Firmware generation uses
  8 fixed templates with a static validator and an mandatory human review gate.

## Demo

```
$ PYTHONPATH=src python3 scripts/demo_react_agent.py

==============================================================================
PCB-AI-AGENT — ReAct demo (FakeLLM, fully offline)
==============================================================================

QUESTION : What series resistor does the LED need (supply 5 V, LED 2.0 V, 20 mA)? Also list the components.
DECISION : The series resistor for LED1 should be 150 Ω (E12 step, 0.06 W). The design has 3 components and 3 nets; supply enters at J1.

REACT TRACE — tool calls the model made:
  1. list_components({"path": "tests/fixtures/simple-led.kicad_net"})
      -> {"components": [{"ref": "J1", "value": "Conn_01x02", "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"}, {"ref": "LED1", "value": "LED", "footprint": "LED_SMD:LED_0603_1608Metric"}, {"ref": "R1", "value": "330", "footprint": "Resistor_SMD:R_0603_1608Metric"}]}
  2. size_resistor({"i_led": 0.02, "v_led": 2.0, "v_supply": 5.0})
      -> {"r_ohms": 150.0, "p_watts": 0.06}

GROUNDING REPORT : ok=True
  [check] REF_IN_ANSWER: 2 ref(s) found, 0 not grounded in design/tool results
  [check] NUMBERS_FROM_SIZING: 2 numeric claim(s) checked against 2 size_resistor value(s)
  [check] NO_DATA_NO_CLAIM: skipped (at least one tool was invoked)

truncated : False
RESULT   : demo answer is fully grounded — no hallucinated facts.
```

## Architecture

Two layers, clean separation:

```
Layer 1 (read)                        Layer 2 (write)

.kicad_net / .kicad_sch               User question
       │                                     │
       ▼                                     ▼
  S-expression parser                  ReAct agent loop
       │                                     │
       ▼                                     ▼
  Netlist models                       LLM picks tool + args
       │                                     │
  ┌────┼────┬────┐                           │
  ▼    ▼    ▼    ▼                           ▼
audit sizing BOM  MCP ──tool dispatch────► pure functions
       │                                     │
       ▼                                     ▼
  Findings + values               Grounding gate (deterministic)
                                          │
                                          ▼
                                   Human review gate
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full design.

## Evals

The read layer is locked down with a golden eval harness — 21 hand-computed
cases (15 sizing, 6 audit), dual judge and regression guard.

| Metric | Value |
|---|---|
| Golden cases | 21 (15 sizing + 6 audit) |
| Score | 1.0000 |
| Judge | Heuristic (CI-safe) + LLMJudge (opt-in) |
| Regression tolerance | 0.02 |

```
$ python3 scripts/run_evals.py
  sizing: 15/15 passed | audit: 6/6 passed
  overall score: 1.0000 (21/21)
  [result] OK — 21/21 cases passed
```

See [docs/EVALS.md](docs/EVALS.md) for dataset contracts and scoring formulas.

## Stack

- **Python 3.11+** — stdlib-only runtime (zero external dependencies)
- **pytest** — test suite (493 tests, 1 opt-in LLM integration)
- **ruff** — linting and formatting
- **mcp** *(optional)* — MCP SDK for the stdio tool server
- **Ollama** *(optional)* — local LLM for the ReAct agent (`qwen2.5-coder:7b`)

## Quick start

```bash
# Clone and set up
git clone https://github.com/eLSeR17/pcb-ai-agent.git
cd pcb-ai-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # or: pip install -r requirements.txt

# Run the checks
python3 -m pytest tests/ -q -m "not llm"
python3 -m ruff check src tests scripts
python3 -m ruff format --check src tests scripts
python3 scripts/run_evals.py

# Install the pre-push hook (replicates CI checks locally)
git config core.hooksPath scripts/hooks
```

### Optional: real LLM

The agent needs a running Ollama instance.  Inside the Docker network:

```bash
# Set the endpoint (default http://ollama:11434 inside Docker)
export OLLAMA_HOST=http://localhost:11434   # on the host
export OLLAMA_MODEL=qwen2.5-coder:7b

# Run the integration test (skipped by default)
PCB_AGENT_RUN_LLM=1 python3 -m pytest tests/ -m llm -v
```

## MCP server

The project exposes 7 read-only tools via MCP over stdio — perfect for
connecting to any MCP-compatible agent client.

```bash
pip install -e ".[mcp]"
PYTHONPATH=src python3 -m pcbai.mcp.server
```

See [docs/MCP.md](docs/MCP.md) for the full tool reference.

## Limitations

- **KiCad subset v1**: the parser recognises R, C, U, J, L, D, Q components.
  Other prefixes produce partial BOMs — a documented limitation, not a bug.
- **Schematic connectivity**: the schematic parser covers a documented subset
  (symbols, wires, labels, junctions) and cannot resolve pin-level
  connectivity without the KiCad symbol library.  Use the exported netlist
  for full audit coverage.
- **Static validator**: firmware validation checks structure (braces, includes,
  pin schema) but does not compile.  Every artifact carries
  `requires_human_review=True`.
- **Compile-check is opt-in syntax checking**: `pcbai.firmware.compile_check`
  feeds artifacts to the system `gcc`/`g++` (`-fsyntax-only`, `-Wall
  -Werror`) against bundled 100 %-own HAL/Arduino stubs — a real-parser
  sanity pass, not a link and not a flash-ready vendor build.
- **Grounding is heuristic**: ref/number extraction uses regex + denylist;
  unusual technical prose may over- or under-match.

## Roadmap

- Additional firmware templates (SPI peripheral, CAN bus, DMA config)
- KiCad footprint parsing for physical dimension checks
- Cross-compilation integration (arm-none-eabi-gcc invocation)
- Expanded audit rules (power budget, thermal, decoupling coverage)

## Related portfolio projects

| Project | What it demonstrates |
|---|---|
| [alpha-agent](https://github.com/eLSeR17/alpha-agent) | Agentic function calling with guardrails and evals |
| [smart-contract-rag](https://github.com/eLSeR17/smart-contract-rag) | RAG over smart-contract audits with evidence-first retrieval |
| [evalforge](https://github.com/eLSeR17/evalforge) | Standalone eval harness: golden sets + dual judge + regression guard |
| [plc-ai-agent](https://github.com/eLSeR17/plc-ai-agent) | Local AI agent for PLC diagnostics and SCL code generation |
| [pdm-agent](https://github.com/eLSeR17/pdm-agent) | Predictive maintenance: ML (RUL + defect classification) + LLM work orders |
| [quantum-rag](https://github.com/eLSeR17/quantum-rag) | RAG over 240 arXiv papers (quantum error correction + quantum ML) |

## License

[MIT](LICENSE)
