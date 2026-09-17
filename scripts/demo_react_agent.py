"""Offline ReAct demo with a scripted FakeLLM — no network, no Ollama.

Shows the full agent loop: question → tool calls (dispatched to the real
read-layer pure functions) → final answer → deterministic GroundingReport.

Run from the repo root:

    PYTHONPATH=src python3 scripts/demo_react_agent.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pcbai.agent.llm_client import LLMResponse
from pcbai.agent.llm_client import ToolCall as ParsedToolCall
from pcbai.agent.react_agent import AgentTrace, PcbAgent

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "simple-led.kicad_net"


class FakeLLM:
    """Scripted stand-in: two tool calls, then a grounded final answer."""

    def __init__(self) -> None:
        self._steps: list[dict[str, Any]] = [
            {"tool_calls": [("list_components", {})]},
            {"tool_calls": [("size_resistor", {"v_supply": 5.0, "v_led": 2.0, "i_led": 0.02})]},
            {
                "final": (
                    "The series resistor for LED1 should be 150 Ω (E12 step, "
                    "0.06 W). The design has 3 components and 3 nets; supply "
                    "enters at J1."
                )
            },
        ]
        self._index = 0

    def chat(
        self,
        *,
        messages: list[dict[str, Any]] | None = None,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        timeout: float | None = None,
        **kwargs: object,
    ) -> LLMResponse:
        step = self._steps[self._index]
        self._index += 1
        if "final" in step:
            return LLMResponse(content=step["final"], tool_calls=())
        calls = tuple(
            ParsedToolCall(name=name, arguments=dict(arguments))
            for name, arguments in step["tool_calls"]
        )
        return LLMResponse(content="", tool_calls=calls)


def _summarise(value: object) -> str:
    text = json.dumps(value, indent=None, default=str)
    return text if len(text) <= 200 else text[:197] + "..."


def main() -> None:
    agent = PcbAgent(client=FakeLLM(), design_path=str(FIXTURE), run_timeout=60.0)
    question = (
        "What series resistor does the LED need (supply 5 V, LED 2.0 V, "
        "20 mA)? Also list the components."
    )
    trace = agent.run(question)

    print("=" * 78)
    print("PCB-AI-AGENT — ReAct demo (FakeLLM, fully offline)")
    print("=" * 78)
    print(f"\nQUESTION : {trace.question}")
    print(f"DECISION : {trace.final_answer}\n")

    print("REACT TRACE — tool calls the model made:")
    if not trace.steps:
        print("  (none)")
    for index, step in enumerate(trace.steps, start=1):
        print(f"  {index}. {step.name}({json.dumps(step.arguments, sort_keys=True)})")
        print(f"      -> {_summarise(step.result)}")

    report = trace.grounded
    print(f"\nGROUNDING REPORT : ok={report.ok}")
    for check in report.checks:
        print(f"  [check] {check}")
    for item in report.unsupported:
        print(f"  [UNSUPPORTED] {item}")
    print(f"\ntruncated : {trace.truncated}")

    if isinstance(trace, AgentTrace) and report.ok:
        print("RESULT   : demo answer is fully grounded — no hallucinated facts.")


if __name__ == "__main__":
    main()
