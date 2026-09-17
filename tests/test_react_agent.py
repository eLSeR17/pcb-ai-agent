"""Tests for the local ReAct agent (no LLM, no network — scripted client).

A ``FakeLLM`` replays predetermined responses (tool calls, then a text
answer) through the same :meth:`ChatClient.chat` interface as the real
Ollama client, so the ReAct loop is exercised end to end without touching
the network. Covered: single and multi-tool loops against the real
read-layer pure functions (real fixture data), ``path`` injection from
``design_path``,
unknown-tool handling, tool error isolation, ``max_steps`` truncation, total
timeout, the tool-result message flow and the grounding report attached to
the trace.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest

from pcbai.agent.grounding import GroundingReport
from pcbai.agent.llm_client import LLMError, LLMResponse
from pcbai.agent.llm_client import ToolCall as ParsedToolCall
from pcbai.agent.react_agent import SYSTEM_PROMPT, AgentTrace, PcbAgent, tool_schemas

FIXTURES = Path(__file__).parent / "fixtures"
SIMPLE_LED = FIXTURES / "simple-led.kicad_net"

# --------------------------------------------------------------------------- #
# Scripted fake client
# --------------------------------------------------------------------------- #


class FakeLLM:
    """Replays a script of tool calls and a final answer, records calls.

    Script entries: ``{"tool_calls": [(name, arguments), ...]}`` or
    ``{"final": "text"}``.
    """

    def __init__(self, script: list[dict[str, Any]]) -> None:
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []

    def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        timeout: float | None = None,
    ) -> LLMResponse:
        self.calls.append(
            {"messages": messages, "tools": tools, "temperature": temperature, "timeout": timeout}
        )
        if not self._script:
            return LLMResponse(content="(script exhausted)", tool_calls=())
        step = self._script.pop(0)
        if "final" in step:
            return LLMResponse(content=step["final"], tool_calls=())
        calls = tuple(
            ParsedToolCall(name=name, arguments=dict(arguments))
            for name, arguments in step["tool_calls"]
        )
        return LLMResponse(content="", tool_calls=calls)


def _tools(*pairs: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    return {"tool_calls": list(pairs)}


def _final(text: str) -> dict[str, Any]:
    return {"final": text}


# --------------------------------------------------------------------------- #
# ReAct loop behaviour
# --------------------------------------------------------------------------- #


class TestReActLoop:
    def test_single_tool_then_final_answer(self) -> None:
        fake = FakeLLM(
            [
                _tools(("load_design", {"path": str(SIMPLE_LED)})),
                _final("The design has been loaded."),
            ]
        )
        trace = PcbAgent(client=fake).run("What is in the board?")

        assert isinstance(trace, AgentTrace)
        assert len(trace.steps) == 1
        step = trace.steps[0]
        assert step.name == "load_design"
        assert step.arguments == {"path": str(SIMPLE_LED)}
        assert isinstance(step.result, dict)
        assert step.result["components"] == 3
        assert step.result["nets"] == 3
        assert trace.final_answer == "The design has been loaded."
        assert trace.truncated is False
        assert trace.grounded.ok is True

    def test_tool_result_is_real_fixture_data(self) -> None:
        fake = FakeLLM(
            [
                _tools(("list_components", {"path": str(SIMPLE_LED)})),
                _final("The components are J1, LED1 and R1."),
            ]
        )
        trace = PcbAgent(client=fake).run("List the components.")

        refs = [row["ref"] for row in trace.steps[0].result["components"]]
        assert refs == ["J1", "LED1", "R1"]
        assert trace.grounded.ok is True  # refs came from the tool result

    def test_multiple_tools_in_sequence(self) -> None:
        fake = FakeLLM(
            [
                _tools(("list_nets", {"path": str(SIMPLE_LED)})),
                _tools(("size_resistor", {"v_supply": 5.0, "v_led": 2.0, "i_led": 0.02})),
                _final("Use a 150 Ω series resistor (0.06 W)."),
            ]
        )
        trace = PcbAgent(client=fake).run("What resistor should I use?")

        assert [step.name for step in trace.steps] == ["list_nets", "size_resistor"]
        sizing = trace.steps[1].result
        assert sizing == {"r_ohms": 150, "p_watts": 0.06}
        assert trace.final_answer == "Use a 150 Ω series resistor (0.06 W)."
        assert trace.grounded.ok is True  # 150 Ω and 0.06 W are backed by sizing

    def test_path_injected_from_design_path(self) -> None:
        fake = FakeLLM(
            [
                _tools(("list_components", {})),  # model omits the path
                _final("The components are J1, LED1 and R1."),
            ]
        )
        agent = PcbAgent(client=fake, design_path=str(SIMPLE_LED))
        trace = agent.run("List the components.")

        assert trace.steps[0].arguments == {"path": str(SIMPLE_LED)}
        assert trace.steps[0].result["components"]  # the fixture was parsed
        assert agent.design_path == str(SIMPLE_LED)

    def test_unknown_tool_returns_error_and_loop_continues(self) -> None:
        fake = FakeLLM(
            [
                _tools(("fly_to_the_moon", {})),
                _final("I cannot do that with the available tools."),
            ]
        )
        trace = PcbAgent(client=fake).run("Do something impossible.")

        assert len(trace.steps) == 1
        assert trace.steps[0].name == "fly_to_the_moon"
        assert trace.steps[0].result["error"]["type"] == "unknown_tool"
        assert trace.final_answer == "I cannot do that with the available tools."
        assert trace.grounded.ok is True

    def test_missing_path_without_design_path_is_an_error(self) -> None:
        fake = FakeLLM([_tools(("load_design", {})), _final("I need a design file path.")])
        trace = PcbAgent(client=fake).run("Load the board.")

        assert trace.steps[0].result["error"]["type"] == "missing_argument"
        assert trace.final_answer == "I need a design file path."

    def test_tool_value_error_is_isolated_and_reported(self) -> None:
        fake = FakeLLM(
            [
                _tools(("size_resistor", {"v_supply": -5.0, "v_led": 2.0, "i_led": 0.02})),
                _final("Those parameters are not physical."),
            ]
        )
        trace = PcbAgent(client=fake).run("Size a resistor.")

        assert trace.steps[0].result["error"]["type"] == "tool_error"
        assert "finite positive" in trace.steps[0].result["error"]["message"]
        assert trace.final_answer == "Those parameters are not physical."

    def test_invalid_arguments_type_error_reported(self) -> None:
        fake = FakeLLM(
            [
                _tools(("size_resistor", {"v_supply": "5 volts"})),  # missing v_led/i_led
                _final("I will retry with the full parameters."),
            ]
        )
        trace = PcbAgent(client=fake).run("Size a resistor.")

        assert trace.steps[0].result["error"]["type"] == "invalid_arguments"
        assert trace.final_answer == "I will retry with the full parameters."

    def test_max_steps_truncates_with_honest_message(self) -> None:
        script = [_tools(("size_resistor", {"v_supply": 5.0, "v_led": 2.0, "i_led": 0.02}))] * 8
        trace = PcbAgent(client=FakeLLM(script), max_steps=3).run("Keep sizing.")

        assert trace.truncated is True
        assert "maximum reasoning steps" in trace.final_answer
        assert len(trace.steps) == 3
        assert trace.grounded.ok is True  # the truncation note asserts no facts

    def test_llm_error_returns_honest_unavailable_answer(self) -> None:
        class FailingLLM:
            def chat(self, **kwargs: object) -> LLMResponse:
                raise LLMError("connection refused")

        trace = PcbAgent(client=FailingLLM()).run("What is the design?")

        assert trace.final_answer.startswith("I could not complete the request")
        assert trace.steps == ()
        assert trace.truncated is False
        assert trace.grounded.ok is True  # an availability note, not a fact claim

    def test_empty_final_answer_triggers_one_retry(self) -> None:
        fake = FakeLLM([_final(""), _final("Done.")])
        trace = PcbAgent(client=fake).run("Hello?")

        assert len(fake.calls) == 2
        assert trace.final_answer == "Done."

    def test_messages_flow_assistant_tool_calls_then_tool_result(self) -> None:
        fake = FakeLLM(
            [
                _tools(("size_resistor", {"v_supply": 5.0, "v_led": 2.0, "i_led": 0.02})),
                _final("Use 150 Ω."),
            ]
        )
        PcbAgent(client=fake).run("Size it.")

        history = fake.calls[1]["messages"]
        roles = [message["role"] for message in history]
        assert roles == ["user", "assistant", "tool"]
        assistant = history[1]
        assert assistant["tool_calls"][0]["function"]["name"] == "size_resistor"
        assert '"r_ohms"' in history[2]["content"]

    def test_trace_records_arguments_and_result(self) -> None:
        fake = FakeLLM(
            [
                _tools(("size_resistor", {"v_supply": 5.0, "v_led": 2.0, "i_led": 0.02})),
                _final("Use 150 Ω."),
            ]
        )
        trace = PcbAgent(client=fake).run("Size it.")

        step = trace.steps[0]
        assert step.name == "size_resistor"
        assert step.arguments == {"v_supply": 5.0, "v_led": 2.0, "i_led": 0.02}
        assert step.result == {"r_ohms": 150, "p_watts": 0.06}
        assert isinstance(trace.grounded, GroundingReport)

    def test_tool_schemas_are_sent_every_turn(self) -> None:
        fake = FakeLLM([_final("No tools needed.")])
        PcbAgent(client=fake).run("Say hi.")

        schemas = fake.calls[0]["tools"]
        assert schemas is not None
        assert len(schemas) == 7
        names = {schema["function"]["name"] for schema in schemas}
        assert names == {
            "load_design",
            "list_components",
            "list_nets",
            "check_connectivity",
            "size_resistor",
            "generate_bom",
            "audit_design",
        }

    def test_temperature_and_timeout_forwarded(self) -> None:
        fake = FakeLLM([_final("ok")])
        PcbAgent(client=fake, run_timeout=60.0).run("Hi.")

        assert fake.calls[0]["temperature"] == 0.2
        # Rounding between the two monotonic() reads is sub-microsecond, but
        # the budget boundary must never be exceeded: min(client 60s, remaining).
        assert fake.calls[0]["timeout"] == pytest.approx(60.0, abs=0.5)

    def test_run_timeout_truncates(self) -> None:
        class SlowLLM:
            def chat(self, **kwargs: object) -> LLMResponse:
                time.sleep(0.05)
                return LLMResponse(
                    content="final",
                    tool_calls=(ParsedToolCall(name="size_resistor", arguments={}),),
                )

        trace = PcbAgent(client=SlowLLM(), run_timeout=0.01, max_steps=10).run("Hi.")
        assert trace.truncated is True
        assert "timeout exceeded" in trace.final_answer


# --------------------------------------------------------------------------- #
# System prompt & schema contract
# --------------------------------------------------------------------------- #


class TestContract:
    def test_system_prompt_enforces_the_invariant(self) -> None:
        assert "Never invent" in SYSTEM_PROMPT
        assert "call the tools" in SYSTEM_PROMPT
        assert "report" in SYSTEM_PROMPT.lower()

    def test_schemas_expose_only_known_tools(self) -> None:
        assert set(tool_schemas()[0]["function"].keys()) == {"name", "description", "parameters"}

    def test_size_resistor_schema_requires_three_parameters(self) -> None:
        schema = next(
            s["function"]["parameters"]
            for s in tool_schemas()
            if s["function"]["name"] == "size_resistor"
        )
        assert schema["required"] == ["v_supply", "v_led", "i_led"]


# --------------------------------------------------------------------------- #
# Real-Ollama integration (opt-in, skipped by default — never runs on CI)
# --------------------------------------------------------------------------- #


@pytest.mark.llm
@pytest.mark.slow
@pytest.mark.skipif(
    os.getenv("PCB_AGENT_RUN_LLM") != "1",
    reason="requires a live local Ollama; set PCB_AGENT_RUN_LLM=1 to run",
)
def test_real_ollama_react_loop_end_to_end() -> None:
    """Full loop against a real Ollama inside the docker network.

    Run it from the python-lab container (or anywhere that reaches
    ``OLLAMA_HOST``, default ``http://ollama:11434``):

        PCB_AGENT_RUN_LLM=1 python3 -m pytest tests/test_react_agent.py -m llm -v

    Soft assertions on purpose: we verify the loop made at least one tool
    round trip and produced a non-empty final answer — not the exact model
    wording, which model versions may phrase differently.
    """
    from pcbai.agent.llm_client import OllamaChatClient

    client = OllamaChatClient()
    agent = PcbAgent(client=client, design_path=str(SIMPLE_LED), run_timeout=240.0)
    trace = agent.run(
        "The LED is driven from the 5V net. What series resistor should I use "
        "(LED forward voltage 2.0 V, target current 20 mA)?"
    )

    assert trace.steps, "expected at least one tool round trip"
    assert {step.name for step in trace.steps} <= {
        "load_design",
        "list_components",
        "list_nets",
        "check_connectivity",
        "size_resistor",
        "generate_bom",
        "audit_design",
    }
    assert trace.final_answer.strip(), "expected a non-empty final answer"
    assert "no final answer" not in trace.final_answer
