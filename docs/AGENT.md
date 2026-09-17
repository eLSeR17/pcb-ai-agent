# Agent (ReAct over local Ollama)

> Local ReAct agent with deterministic grounding — optional LLM integration
> (see below). The agent runs fully local (Ollama), with a deterministic,
> LLM-free anti-hallucination gate on the final answer.

## What this layer adds

```
src/pcbai/agent/
├── llm_client.py   # stdlib-only Ollama client (/api/chat, native tool calling)
├── react_agent.py  # synchronous ReAct loop (PcbAgent) + the 7-tool dispatch table
└── grounding.py    # deterministic validate_answer() → GroundingReport
```

The agent sits on top of the read layer (Layer 1: parsers, audit, sizing)
and its calculations. The model never computes: it picks tools and
arguments; every fact and every number in its answer comes from a tool
result (project invariant).

## Architecture

```
 user question
      │
      ▼
┌─ PcbAgent.run() ──────────────┐   loop, bounded by max_steps (6) and
│  messages + 7 tool schemas    │   run_timeout (300 s)
│      │                        │
│      ▼                        │
│  OllamaChatClient.chat()      │   POST {OLLAMA_HOST}/api/chat
│      │                        │
│      ▼                        │
│  tool_calls? ──yes──► dispatch to the PURE functions of pcbai.mcp.server
│      │  no                  │  (load_design, list_components, list_nets,
│      ▼                      │   check_connectivity, size_resistor,
│  final answer                │   generate_bom, audit_design)
│      │                      │
│      ▼                      │
│  validate_answer() ─────────┘
│      │
│      ▼
│  AgentTrace(question, steps, final_answer, grounded, truncated)
└──────────────────────────────┘
```

- **Sync by design** — no threads, no async (the sandbox/CI rule of this
  ecosystem: plain blocking loops only).
- **Pure-function dispatch, not the MCP SDK** — the loop calls the seven
  read-layer functions directly with the JSON arguments the model produced.
  The `mcp` SDK stays an optional extra, tests run without it, and there is
  no protocol layer between the model and the tested functions.
- **`design_path` as conversation context** — tools that take a `path`
  argument can be called without it; the agent injects the path and parses
  the design once for grounding.
- **Errors never crash the loop** — an unknown tool or a failing tool returns
  a `{"error": {...}}` tool message so the model can correct itself.

## Tools exposed to the model

| tool | purpose | needs `path` |
|---|---|---|
| `load_design` | design summary (format, counts, types) | yes (injected) |
| `list_components` | `{ref, value, footprint}` per component | yes (injected) |
| `list_nets` | nets with their `{ref, pin}` connections | yes (injected) |
| `check_connectivity` | raw audit findings | yes (injected) |
| `size_resistor` | LED series resistor (`r_ohms`, `p_watts`) | no |
| `generate_bom` | deterministic BOM + ASCII table | yes (injected) |
| `audit_design` | full audit + severity summary | yes (injected) |

## Anti-hallucination grounding (deterministic, no LLM)

`validate_answer(answer, steps, design)` runs three checks and returns a
`GroundingReport(ok, unsupported, checks)`:

1. **REF_IN_ANSWER** — every ref (`R1`, `LED1`, `U2`, `J1`…) in the answer
   must exist in the loaded design or in a tool result. STM32 peripheral
   names (`UART1`, `TIM2`…) and `E12`-style jargon are denylisted so they do
   not count as refs (denylist is extensible in `grounding.py`).
2. **NUMBERS_FROM_SIZING** — when `size_resistor` was invoked, resistance/
   capacitance/power claims in the answer must match its result within 5 %
   (normalises `150`, `150 Ω`, `150.0`, `4.7 kΩ`, `150 Ohms` to base SI
   units; alphabetic unit names are case-insensitive while SI symbols keep
   their case, so `mΩ` and `MΩ` are distinct).
3. **NO_DATA_NO_CLAIM** — no tools invoked + refs/numbers asserted ⇒ fail.
   The agent cannot assert facts it never gathered.

The gate never executes or compiles anything; it only reads the answer text
and the trace. **Honest limits**: it verifies refs/numbers, not electrical
quality — a grounded answer can still be a bad design decision; that is what
the human review gate (and an engineer) is for.

## Running

Ollama must be reachable at `OLLAMA_HOST` (default `http://ollama:11434` —
the compose service name inside the Docker `docker_default` network;
`localhost`/`host.docker.internal` do not resolve from other containers).
On the host, point it at e.g. `OLLAMA_HOST=http://localhost:11434`.

```bash
# (in the python-lab container, inside the docker_default network)
python3 - <<'EOF'
from pcbai.agent.llm_client import OllamaChatClient
from pcbai.agent.react_agent import PcbAgent

agent = PcbAgent(
    client=OllamaChatClient(),
    design_path="tests/fixtures/simple-led.kicad_net",
)
trace = agent.run("What series resistor does the LED need (5 V, 2.0 V, 20 mA)?")
print(trace.final_answer)
print(trace.grounded)
for step in trace.steps:
    print(step.name, step.result)
EOF

# Offline demo (no Ollama): scripted FakeLLM, full trace + grounding
PYTHONPATH=src python3 scripts/demo_react_agent.py
```

## Testing

- `tests/test_llm_client.py` — mocked `urllib` client: payload, tool-call
  parsing, retries (fresh `Request` per attempt), timeouts, `LLMError`.
- `tests/test_react_agent.py` — scripted `FakeLLM` walking the real
  read-layer functions against the committed fixtures; `max_steps`, unknown
  tools, path injection, tool-result message flow, grounding on the trace.
- `tests/test_grounding.py` — pure checks: grounded vs invented refs,
  backed vs unbacked numbers, no-data-no-claim.
- Optional real-Ollama integration: `@pytest.mark.llm` + `@pytest.mark.slow`,
  skipped by default (set `PCB_AGENT_RUN_LLM=1` to run inside the Docker
  network). It never runs on CI.

## Limitations (honest)

- Not a substitute for an engineer: grounding catches hallucinated refs and
  unbacked numbers, not bad electronics.
- v1 size-derived checks only cover `size_resistor` (resistance/power);
  BOM counts or capacitor suggestions are not yet verified numerically.
- Ref/number extraction is heuristic (word-boundary regex + denylist);
  unusual technical prose may still over- or under-match. Extend
  `REF_DENYLIST` or the unit table as needed.
- The default model is `qwen2.5-coder:7b` (already pulled locally; it is the
  same stable, function-calling-capable model used across this portfolio).