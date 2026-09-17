"""pcb-ai-agent — a fully local AI agent for KiCad PCB designs.

The package ships the complete two-layer design:

- **Read layer (Layer 1)** — KiCad S-expression parsers (``pcbai.kicad``),
  evidence-based audit rules, E12-preferred-value sizing math and a
  deterministic bill of materials (``pcbai.design``).
- **Write layer (Layer 2)** — a read-only MCP server (``pcbai.mcp``), a
  local-LLM ReAct agent with deterministic grounding (``pcbai.agent``) and
  template-driven firmware generation with a static validator and a human
  review gate (``pcbai.firmware``).
- **Quality** — a golden eval harness with a dual judge and a regression
  guard (``pcbai.eval``).

Same two-layer pattern as the companion project plc-ai-agent: the LLM
decides, the code calculates.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
