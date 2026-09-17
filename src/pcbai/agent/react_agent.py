"""ReAct agent: local-LLM reasoning loop with tool dispatch.

The agent is a synchronous ReAct loop: it sends the conversation plus the
seven read-layer tool schemas to a local Ollama model, executes whatever
``tool_calls`` the model requests against the **pure functions** of
:mod:`pcbai.mcp.server` (never the MCP protocol, no SDK needed), feeds the
results back, and stops when the model answers in plain text. Every run
finishes with a deterministic :class:`GroundingReport` over the final answer
(see :mod:`pcbai.agent.grounding`).

Project invariant enforced here and in the system prompt: *the LLM decides,
the code calculates*. The model may pick tools and arguments; every design
fact and every numeric value in its answer must come from a tool result —
never invented.

Design decisions:

- **Pure-function dispatch, not the MCP SDK**: the loop calls
  ``pcbai.mcp.server.<tool>`` directly with the JSON arguments the model
  produced. This keeps the agent dependency-free and unit-testable with a
  scripted fake client, and matches the tool contract exactly.
- **``design_path`` as conversation context**: when the agent is constructed
  with a design file, tools that require a ``path`` argument can be called
  without it (the path is injected at dispatch time) and the parsed design
  is used for grounding.
- **Unknown tool / tool error → message back to the model**, never a crash.
- **No threads, no async**: the loop is a plain bounded ``for``; the only
  blocking call is the chat request (the network is up to the client).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pcbai.agent.grounding import GroundingReport, validate_answer
from pcbai.agent.llm_client import LLMError, LLMResponse
from pcbai.kicad.netlist import Design
from pcbai.mcp import server as mcp_tools

log = logging.getLogger(__name__)

__all__ = [
    "SYSTEM_PROMPT",
    "AgentTrace",
    "ChatClient",
    "PcbAgent",
    "Tool",
    "ToolCall",
    "tool_schemas",
]

# --------------------------------------------------------------------------- #
# System prompt (fixes the invariant in the model's instructions)
# --------------------------------------------------------------------------- #

SYSTEM_PROMPT = """\
You are an experienced embedded electronics engineer assisting with KiCad PCB designs. \
You reason step by step and act only through the available tools.

## Invariant (NEVER break)

- You must call the tools to obtain any design fact or numeric value. Never \
invent component reference designators, net names, values or numbers.
- If a tool returns an error, report it honestly and do not guess a fallback \
value.
- Your final answer must only assert facts that came from the tools (or from \
the user's own question). Do not answer from prior knowledge.

## Available tools

{tools}

## How to answer

- Call the tools that give you the evidence for the question (usually one or \
two calls), then answer the user in plain text, citing the actual refs, nets \
and values returned by the tools.
- When a design file was provided as conversation context, the tools that \
take a `path` argument can be called without it — the path is filled in for \
you.
- If you need a numeric value, call the tool that computes it; never \
calculate from memory.
"""


# --------------------------------------------------------------------------- #
# Trace data types
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ToolCall:
    """One executed tool call, recorded for the trace and grounding."""

    name: str
    arguments: dict[str, Any]
    result: Any = field(default=None, compare=False)


@dataclass(frozen=True)
class AgentTrace:
    """The full record of one :meth:`PcbAgent.run` invocation."""

    question: str
    steps: tuple[ToolCall, ...]
    final_answer: str
    grounded: GroundingReport
    truncated: bool = False


class ChatClient(Protocol):
    """Minimal chat interface the ReAct loop depends on.

    Implemented by :class:`pcbai.agent.llm_client.OllamaChatClient`; tests
    substitute a scripted fake, so the loop runs with zero network.
    """

    def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        timeout: float | None = None,
    ) -> LLMResponse: ...


# --------------------------------------------------------------------------- #
# Tool catalogue (the seven read-only tool functions, dispatch table)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Tool:
    """One tool exposed to the model, bound to a pure function handler."""

    name: str
    description: str
    handler: Callable[..., Any]
    needs_path: bool


def _list_components(path: str) -> dict[str, Any]:
    return {"components": mcp_tools.list_components(path)}


def _list_nets(path: str) -> dict[str, Any]:
    return {"nets": mcp_tools.list_nets(path)}


_TOOLS: tuple[Tool, ...] = (
    Tool(
        name="load_design",
        description="Summarise a KiCad design file (netlist or schematic): format, "
        "component and net counts and component types by reference prefix.",
        handler=mcp_tools.load_design,
        needs_path=True,
    ),
    Tool(
        name="list_components",
        description="List every component as {ref, value, footprint}, ordered by "
        "reference designator.",
        handler=_list_components,
        needs_path=True,
    ),
    Tool(
        name="list_nets",
        description="List every net as {name, connections} where connections are "
        "{ref, pin} pairs, ordered by net name.",
        handler=_list_nets,
        needs_path=True,
    ),
    Tool(
        name="check_connectivity",
        description="Run the evidence-based audit rules and return the raw findings "
        "({rule, severity, message, evidence, position}).",
        handler=mcp_tools.check_connectivity,
        needs_path=True,
    ),
    Tool(
        name="size_resistor",
        description="Size the series resistor for an LED (Ohm's law, E12 rounded): "
        "returns r_ohms and the resistor's dissipation p_watts. Arguments: "
        "v_supply (V), v_led (V), i_led (A).",
        handler=mcp_tools.size_resistor,
        needs_path=False,
    ),
    Tool(
        name="generate_bom",
        description="Generate a deterministic bill of materials from a design: one "
        "row per component plus an aligned ASCII table.",
        handler=mcp_tools.generate_bom,
        needs_path=True,
    ),
    Tool(
        name="audit_design",
        description="Full design audit: all findings plus a severity summary "
        "(errors/warnings/infos and the rules that fired).",
        handler=mcp_tools.audit_design,
        needs_path=True,
    ),
)

_TOOL_BY_NAME: dict[str, Tool] = {tool.name: tool for tool in _TOOLS}

_PATH_PARAM: dict[str, Any] = {
    "type": "string",
    "description": "Path to a KiCad design file (.kicad_net/.net or .kicad_sch).",
}

_PARAMETER_SCHEMAS: dict[str, dict[str, Any]] = {
    "load_design": {"type": "object", "properties": {"path": _PATH_PARAM}, "required": ["path"]},
    "list_components": {
        "type": "object",
        "properties": {"path": _PATH_PARAM},
        "required": ["path"],
    },
    "list_nets": {"type": "object", "properties": {"path": _PATH_PARAM}, "required": ["path"]},
    "check_connectivity": {
        "type": "object",
        "properties": {"path": _PATH_PARAM},
        "required": ["path"],
    },
    "generate_bom": {"type": "object", "properties": {"path": _PATH_PARAM}, "required": ["path"]},
    "audit_design": {"type": "object", "properties": {"path": _PATH_PARAM}, "required": ["path"]},
    "size_resistor": {
        "type": "object",
        "properties": {
            "v_supply": {"type": "number", "description": "Supply voltage in volts (e.g. 5.0)."},
            "v_led": {"type": "number", "description": "LED forward voltage in volts (e.g. 2.0)."},
            "i_led": {
                "type": "number",
                "description": "Desired LED current in amperes (e.g. 0.02).",
            },
        },
        "required": ["v_supply", "v_led", "i_led"],
    },
}


def tool_schemas() -> list[dict[str, Any]]:
    """The seven tool schemas in OpenAI/Ollama function-calling format."""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": _PARAMETER_SCHEMAS[tool.name],
            },
        }
        for tool in _TOOLS
    ]


def _grounding_design(path: str | None) -> Design | None:
    """Parse the design once for grounding; degrade gracefully on errors."""
    if not path:
        return None
    try:
        design, _format = mcp_tools._load_design(path)  # noqa: SLF001 - single source of truth
        return design
    except ValueError:
        log.warning("could not load design %r for grounding (tools will report it)", path)
        return None


# --------------------------------------------------------------------------- #
# The ReAct loop
# --------------------------------------------------------------------------- #


class PcbAgent:
    """Synchronous ReAct agent over the seven read-only design tools.

    Args:
        client: an object implementing :class:`ChatClient` (real Ollama
            client, or a scripted fake in tests).
        design_path: optional KiCad design file used as conversation
            context — injected into path-taking tools when the model omits
            the argument, and parsed for answer grounding.
        max_steps: maximum ReAct iterations before truncating.
        run_timeout: total wall-clock budget in seconds for one run
            (``None`` disables the deadline). Each LLM call is bounded by
            ``min(client.timeout, remaining_budget)``.
        system_prompt: model instructions (default enforces the invariant).
    """

    def __init__(
        self,
        *,
        client: ChatClient,
        design_path: str | Path | None = None,
        max_steps: int = 6,
        run_timeout: float | None = 300.0,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        if max_steps < 1:
            raise ValueError(f"max_steps must be >= 1, got {max_steps!r}")
        if run_timeout is not None and run_timeout <= 0:
            raise ValueError(f"run_timeout must be positive or None, got {run_timeout!r}")
        self._client = client
        self.design_path = str(design_path) if design_path else None
        self._design = _grounding_design(self.design_path)
        self._max_steps = int(max_steps)
        self._run_timeout = run_timeout
        self._system_prompt = system_prompt

    # -- public API --------------------------------------------------------- #

    def run(self, question: str) -> AgentTrace:
        """Answer ``question`` through the ReAct loop and ground the result.

        Returns an :class:`AgentTrace` with every executed tool call, the
        final answer and the deterministic :class:`GroundingReport` of that
        answer. Never raises for model/tool failures: they are either
        reported to the model (tool errors) or returned as an honest final
        answer (LLM unavailable, timeout).
        """
        question = (question or "").strip() or "(no question provided)"
        messages: list[dict[str, Any]] = [{"role": "user", "content": self._user_message(question)}]
        steps: list[ToolCall] = []
        deadline = time.monotonic() + self._run_timeout if self._run_timeout else None

        for _ in range(self._max_steps):
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                return self._finish(
                    question,
                    steps,
                    "(no final answer: total run timeout exceeded)",
                    truncated=True,
                )

            try:
                response = self._client.chat(
                    messages=messages,
                    tools=tool_schemas(),
                    temperature=0.2,
                    timeout=self._per_call_timeout(remaining),
                )
            except LLMError as exc:
                message = f"I could not complete the request because the local model is unavailable: {exc}"
                return self._finish(question, steps, message, truncated=False)

            if response.tool_calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": response.content or "",
                        "tool_calls": [
                            {"function": {"name": call.name, "arguments": call.arguments}}
                            for call in response.tool_calls
                        ],
                    }
                )
                for call in response.tool_calls:
                    used_args, result = self._execute(call.name, dict(call.arguments))
                    steps.append(ToolCall(name=call.name, arguments=used_args, result=result))
                    messages.append(
                        {
                            "role": "tool",
                            "content": json.dumps(result, ensure_ascii=False, default=str),
                        }
                    )
                continue

            answer = (response.content or "").strip()
            if not answer:
                messages.append(
                    {
                        "role": "user",
                        "content": "Your previous message was empty. Call a tool or answer the question.",
                    }
                )
                continue
            return self._finish(question, steps, answer, truncated=False)

        return self._finish(
            question,
            steps,
            "(no final answer: maximum reasoning steps reached)",
            truncated=True,
        )

    # -- internals ---------------------------------------------------------- #

    def _user_message(self, question: str) -> str:
        if self.design_path:
            return f"Design file: {self.design_path}\n\nQuestion: {question}"
        return f"Question: {question}"

    def _per_call_timeout(self, remaining: float | None) -> float:
        client_timeout = float(getattr(self._client, "timeout", 60.0) or 60.0)
        if remaining is None:
            return client_timeout
        return max(0.1, min(client_timeout, remaining))

    def _execute(self, name: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], Any]:
        """Dispatch one tool call; return ``(used_arguments, result)``.

        ``used_arguments`` is the argument dict actually handed to the tool
        (the ``design_path`` is injected when the model omitted ``path``), so
        the trace always records what really ran. Unknown tools and tool
        failures become ``{"error": {...}}`` payloads that are fed back to
        the model as tool messages — the loop never crashes and the model can
        correct itself.
        """
        tool = _TOOL_BY_NAME.get(name)
        args = dict(arguments)
        if tool is None:
            return (
                args,
                {
                    "error": {
                        "type": "unknown_tool",
                        "message": f"Unknown tool {name!r}; available tools: {', '.join(_TOOL_BY_NAME)}",
                    }
                },
            )
        if tool.needs_path and "path" not in args:
            if self.design_path is None:
                return (
                    args,
                    {
                        "error": {
                            "type": "missing_argument",
                            "message": (
                                f"Tool {name!r} requires a 'path' argument and the agent "
                                "was not given a design_path."
                            ),
                        }
                    },
                )
            args["path"] = self.design_path
        try:
            return (args, tool.handler(**args))
        except TypeError as exc:
            return (args, {"error": {"type": "invalid_arguments", "message": str(exc)}})
        except ValueError as exc:
            return (args, {"error": {"type": "tool_error", "message": str(exc)}})

    def _finish(
        self,
        question: str,
        steps: list[ToolCall],
        answer: str,
        *,
        truncated: bool,
    ) -> AgentTrace:
        report = validate_answer(answer, steps, design=self._design)
        return AgentTrace(
            question=question,
            steps=tuple(steps),
            final_answer=answer,
            grounded=report,
            truncated=truncated,
        )
