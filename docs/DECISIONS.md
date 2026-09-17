# Architecture Decision Records — pcb-ai-agent

Each record follows the Status / Context / Decision / Consequences format.

---

## ADR-001: S-expression parser (sexpr.py) — own implementation, 0 dependencies

**Status:** Accepted

**Context:**
KiCad stores both netlists (`.kicad_net`) and schematics (`.kicad_sch`) as
S-expressions.  We need a parser that can read real-world files without pulling
in a KiCad library, a Lisp runtime or any heavyweight dependency.  The parser
must report precise error locations (line/column) for malformed input and handle
the full range of KiCad-specific tokens (quoted strings, nested lists, embedded
binary-like blocks).

**Decision:**
Implement a self-contained, recursive-descent S-expression parser in
`pcbai.kicad.sexpr` with zero runtime dependencies.  The parser produces a
simple nested-list representation (`list[Any]`); all semantic interpretation
(netlists, schematics) happens in higher-level modules.

**Consequences:**
- **(+)** Zero runtime dependencies for the entire parsing stack.
- **(+)** Full control over error messages and line/column tracking.
- **(+)** No version-pinning burden or upstream breakage risk.
- **(−)** We maintain the parser ourselves, but it is < 200 lines and the
  KiCad S-expression format is stable (unchanged since Eeschema 4.x).

---

## ADR-002: KiCad netlist v"E" as the source of truth for connectivity

**Status:** Accepted

**Context:**
KiCad provides two representations of a design: the exported netlist (`.kicad_net`,
format version "E", used by Eeschema 6.x–8.x) and the schematic file (`.kicad_sch`).
The netlist is a flattened, unambiguous description of every component and every
net with pin-level connections.  The schematic file encodes visual layout (wires,
labels, junctions, sheets) and pin connectivity is inferred from symbol placement
— which is only partially parseable without the full KiCad symbol library.

**Decision:**
Treat the exported netlist as the **source of truth** for pin-level connectivity.
The schematic parser covers only the documented subset (symbols, wires, labels,
junctions) and is tolerant of everything else it finds in real files (sheets,
embedded `lib_symbols`, text, buses are skipped without crashing).

**Consequences:**
- **(+)** Audit rules have unambiguous, deterministic connectivity data.
- **(+)** No dependency on KiCad symbol libraries (which are large and
  version-specific).
- **(−)** The schematic parser cannot resolve pin-level connectivity on its own;
  audit rules correctly report fewer findings when run against a schematic file.
  This is documented and intentional.

---

## ADR-003: Bounded KiCad subset v1 (R/C/U/J/L/D/Q) + ad-hoc fixtures

**Status:** Accepted

**Context:**
KiCad supports a vast component library — passives, ICs, connectors, power
symbols, mechanical parts, hierarchical sheets and more.  Trying to handle
everything in v1 would delay shipping and create a maintenance burden with
no clear payoff for a portfolio project.

**Decision:**
The v1 parser and audit rules recognise a bounded set of component reference
prefixes: `R` (resistor), `C` (capacitor), `U` (IC), `J` (connector), `L`
(inductor), `D` (diode), `Q` (transistor).  Fixtures are authored ad-hoc
(`simple-led.kicad_net`, `bad-led.kicad_net`, `minimal-mcu.kicad_net`, etc.)
and never copied from third-party sources.

**Consequences:**
- **(+)** Fast to ship, fast to test, fast to reason about.
- **(+)** Ad-hoc fixtures let us design test cases that exercise specific
  rules without the noise of a full real-world board.
- **(−)** Real boards with `F` (fuse), `Y` (crystal) or power symbols will
  produce partial BOMs; this is a documented limitation, not a bug.

---

## ADR-004: Anti-hallucination invariant — numeric values from code, never from the LLM

**Status:** Accepted

**Context:**
LLMs are confidently wrong about numbers — especially resistor values, voltage
dividers and capacitor calculations.  A firmware engineer who trusts a hallucinated
`4.7 kΩ` when the circuit needs `150 Ω` could damage hardware.

**Decision:**
The project enforces an absolute invariant: *the LLM decides, the code calculates*.
The model may pick tools and arguments; every numeric value in its final answer must
come from a tool result.  The deterministic `validate_answer()` function verifies
this with three checks (`REF_IN_ANSWER`, `NUMBERS_FROM_SIZING`,
`NO_DATA_NO_CLAIM`).  A failed grounding report means the answer is not trusted.

**Consequences:**
- **(+)** Hardware-facing answers are reproducible and verifiable.
- **(+)** The grounding gate is deterministic — same input, same report, no LLM.
- **(−)** The model cannot answer questions that require data not available through
  the seven tools; it honestly says so rather than guessing.

---

## ADR-005: Firmware by deterministic templates + static validator + human review gate

**Status:** Accepted

**Context:**
Generating firmware init code is one of the few cases where the agent produces
code, not just analysis.  Letting the LLM write HAL/Arduino code directly is
unacceptable: the code must compile, follow the vendor API, and use the correct
pin assignments — none of which an LLM can guarantee.

**Decision:**
Firmware generation uses 8 fixed templates (4 STM32 HAL + 4 Arduino) with strict
parameter contracts.  `templates.py` validates every parameter before rendering;
`generator.py` renders the template into a `FirmwareArtifact`; `validator.py`
runs static structural checks (braces, includes, pin schema).  Every artifact is
returned with `requires_human_review=True` — the validator is a sanity layer,
never a substitute for compiling and reviewing.

**Consequences:**
- **(+)** Generated code is guaranteed to follow the vendor API pattern.
- **(+)** Templates are auditable, diffable and versionable.
- **(+)** The human review gate prevents any generated code from reaching
  production without engineering sign-off.
- **(−)** The template set is finite (8 templates in v1); the model cannot
  generate novel peripheral configurations.  This is intentional.

---

## ADR-006: MCP server read-only by design (7 tools, stdin, lazy SDK import)

**Status:** Accepted

**Context:**
The Model Context Protocol (MCP) is the standard interface between LLM agents
and external tools.  For a portfolio project, the MCP server must be safe to
run, easy to test, and demonstrate the pattern without creating attack surface.

**Decision:**
The MCP server exposes exactly 7 read-only tools over stdio — no HTTP endpoint,
no listening socket, no disk writes.  Each tool is a thin wrapper around a pure
function.  The `mcp` SDK (`>= 2.0`) is imported lazily (`server.py`), so the
pure tool functions can be imported and tested without the optional dependency
installed.

**Consequences:**
- **(+)** The server is safe to run: no tool mutates the design or the file system.
- **(+)** Tests run without the `mcp` extra — the CI dependency tree stays minimal.
- **(−)** Adding a new tool requires touching the dispatch table in two places
  (the `MCP` registration and the agent's `_TOOLS` tuple), but the set is small
  enough that this is manageable.

---

## ADR-007: ReAct agent with stdlib-only urllib client (no requests/openai SDK)

**Status:** Accepted

**Context:**
The `requests` library and the `openai` SDK are the most common Python HTTP
clients for LLM APIs.  However, they add heavyweight transitive dependencies
(`urllib3`, `certifi`, `httpx`, `pydantic`), increase the attack surface, and
are unnecessary for a single-endpoint, non-streaming, synchronous chat client.

**Decision:**
`OllamaChatClient` uses only the Python stdlib (`urllib.request`, `urllib.error`,
`json`).  Retries create a **fresh `Request` object per attempt** — reusing the
same `Request` across retries masks the real API error behind proxy CONNECT
failures (a recurring lesson in this ecosystem).  The client supports native
Ollama function calling (`tools` + `message.tool_calls`) and surfaces errors
as `LLMError` with full context.

**Consequences:**
- **(+)** Zero runtime dependencies for the entire agent stack.
- **(+)** The client is ~100 lines and fully testable with mocked `urllib`.
- **(−)** We lose automatic retries with backoff libraries (but `max_retries=2`
  with a fresh `Request` is sufficient for Ollama's transient 502/503).

---

## ADR-008: Deterministic grounding (validate_answer) — REF / NUMBERS / NO_DATA

**Status:** Accepted

**Context:**
A local LLM (qwen2.5-coder:7b) may phrase facts loosely, round numbers
incorrectly, or invent component reference designators.  There is no cloud
API with built-in safety filters; the grounding must happen locally and
deterministically.

**Decision:**
`validate_answer(answer, steps, design)` implements three checks:

1. **REF_IN_ANSWER** — every `[A-Z]+[0-9]+` token (R1, LED1, U2, J1) in the
   answer must exist in the loaded design or in a tool result.  A small denylist
   keeps STM32 peripheral names (UART1, TIM2) and E-series jargon (E12) from
   being mistaken for component refs.

2. **NUMBERS_FROM_SIZING** — when `size_resistor` was invoked, resistance /
   capacitance / power claims must match tool output within 5 % relative tolerance,
   normalising units (`150`, `150 Ω`, `4.7 kΩ`, `150 Ohms` → base SI).

3. **NO_DATA_NO_CLAIM** — no tools invoked + refs/numbers asserted = fail.

Unit normalisation is case-insensitive for alphabetic names (`Ohms`/`ohms`) and
case-significant for SI symbols (`mΩ` ≠ `MΩ`, `mW` ≠ `MW`).

**Consequences:**
- **(+)** Grounding is pure, deterministic and fast (< 1 ms).
- **(+)** Same input, same report — fully reproducible in CI.
- **(−)** Heuristic ref extraction (regex + denylist) may over- or under-match
  in unusual technical prose; the denylist is extensible.

---

## ADR-009: Eval harness — golden set + dual judge + regression guard

**Status:** Accepted

**Context:**
The read layer (sizing math + audit rules) is the project's core value.  A
regression that silently changes a sizing formula or drops an audit rule would
undermine trust in every agent answer.

**Decision:**
The eval harness adds a golden dataset with three layers:

1. **Golden dataset** — 21 hand-computed cases (15 sizing, 6 audit) committed
   as JSONL with stable `case_id`s.

2. **Dual judge** — `HeuristicJudge` (deterministic, zero-network, CI-safe)
   scores sizing within relative tolerance and audit by rule recall with
   extra-rule / severity penalties.  `LLMJudge` (opt-in local Ollama) adds a
   0–5 quality score and degrades honestly to the heuristic verdict when
   unreachable.

3. **Regression guard** — `check_regression(report, baseline)` fails CI when
   the score drops more than 0.02 below the stored baseline.  A missing baseline
   is informational (first run), not a failure.

**Consequences:**
- **(+)** Every code change that touches sizing or audit is automatically checked
  against the golden set.
- **(+)** The regression guard catches silent quality drops even when tests pass.
- **(−)** The golden set is hand-maintained; adding a new sizing formula requires
  authoring new cases.  This is deliberate — automated case generation would
  defeat the purpose.

---

## ADR-010: Default model qwen2.5-coder:7b (qwen3 leaves content empty by reasoning)

**Status:** Accepted

**Context:**
The agent needs a local model with native function-calling support that
reliably returns both text content and tool calls.  qwen3:8b is newer but
has a known behaviour where `content` is left empty when the model performs
internal reasoning (`reasoning_content` is populated instead), breaking the
ReAct loop's assumption that a response always carries either tool calls or
text content.

**Decision:**
The default model is `qwen2.5-coder:7b`, which is stable, function-calling
capable, and returns `content` reliably.  This model is already pulled locally
and is the same default used across this portfolio (plc-ai-agent, evalforge).

**Consequences:**
- **(+)** Consistent behaviour across the portfolio.
- **(+)** Well-tested with the ReAct loop and grounding gate.
- **(−)** Not the newest model available; Ollama supports model switching via
  `OLLAMA_MODEL` env var if the user wants to experiment.
