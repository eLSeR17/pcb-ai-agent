"""Agent layer: local-LLM ReAct loop with anti-hallucination grounding.

Public API:

- :class:`pcbai.agent.llm_client.OllamaChatClient` — stdlib-only Ollama
  client (``/api/chat``) with native function calling and retries on
  transient failures.
- :class:`pcbai.agent.react_agent.PcbAgent` — the synchronous ReAct loop:
  the model picks the seven read-layer tools and their arguments, the code
  dispatches them to the pure functions of :mod:`pcbai.mcp.server`.
- :func:`pcbai.agent.grounding.validate_answer` — deterministic, LLM-free
  anti-hallucination report over the final answer (refs, sizing numbers,
  no-data-no-claim rules).
"""

from pcbai.agent.grounding import GroundingReport, validate_answer
from pcbai.agent.llm_client import LLMError, LLMResponse, OllamaChatClient
from pcbai.agent.react_agent import (
    SYSTEM_PROMPT,
    AgentTrace,
    PcbAgent,
    Tool,
)
from pcbai.agent.react_agent import (
    ToolCall as AgentToolCall,
)

__all__ = [
    "AgentToolCall",
    "AgentTrace",
    "GroundingReport",
    "LLMError",
    "LLMResponse",
    "OllamaChatClient",
    "PcbAgent",
    "SYSTEM_PROMPT",
    "Tool",
    "validate_answer",
]
