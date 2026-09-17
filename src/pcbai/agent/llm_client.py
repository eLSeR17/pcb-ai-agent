"""Minimal Ollama chat client over the stdlib (``urllib``) — no external deps.

Talks to the local Ollama ``POST /api/chat`` endpoint with native
function-calling support (``tools`` + ``message.tool_calls``). Blocking and
synchronous by design — the pcb-ai-agent ReAct loop is a plain ``for`` loop
with no threads and no async machinery.

Configuration (constructor arguments override env vars):

- ``OLLAMA_HOST`` — endpoint; default ``http://ollama:11434``. Inside the
  Docker network the compose service name ``ollama`` resolves; ``localhost``
  and ``host.docker.internal`` do *not* resolve from other containers.
- ``OLLAMA_MODEL`` — model name; default ``qwen2.5-coder:7b``.

Retry policy: up to ``max_retries`` retries (default 2) for transient
failures — HTTP 502/503 (model still loading) and network-level errors
(connection refused, DNS, read timeout, reset). A **fresh** ``Request``
object is built for every attempt: reusing one Request across retries masks
the real API error behind proxy CONNECT failures (recurring lesson in this
ecosystem). Non-transient HTTP codes (400/404/500...) and malformed JSON
raise immediately with context — never an opaque traceback.

Nothing in this module performs I/O at import time — no network, no sockets.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

__all__ = ["LLMError", "LLMResponse", "OllamaChatClient", "ToolCall"]

log = logging.getLogger(__name__)

#: Default Ollama endpoint (compose service name inside the Docker network).
_DEFAULT_HOST = "http://ollama:11434"
_DEFAULT_MODEL = "qwen2.5-coder:7b"
_DEFAULT_TIMEOUT = 60.0
_DEFAULT_MAX_RETRIES = 2  # total attempts = max_retries + 1

#: HTTP statuses considered transient (model loading / server busy).
_RETRYABLE_HTTP = frozenset({502, 503})


class LLMError(RuntimeError):
    """Raised when the Ollama request cannot be completed.

    The message carries the endpoint, the HTTP status or network failure and
    the attempt count, so callers never have to unwrap raw socket exceptions.
    """


@dataclass(frozen=True)
class ToolCall:
    """One tool invocation requested by the model."""

    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class LLMResponse:
    """Parsed ``/api/chat`` response: optional text plus native tool calls.

    ``raw`` keeps the complete JSON payload for transparency and debugging.
    It defaults to an empty dict so callers that build responses directly
    (mocks, tests) do not need to fabricate one.
    """

    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)


def _normalize_host(host: str) -> str:
    """Add an explicit ``http://`` scheme when the caller omitted it."""
    host = host.strip().rstrip("/")
    if "://" not in host:
        host = f"http://{host}"
    return host


class OllamaChatClient:
    """Blocking chat client for the local Ollama ``/api/chat`` endpoint."""

    def __init__(
        self,
        *,
        host: str | None = None,
        model: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ) -> None:
        self.host = _normalize_host(host or os.getenv("OLLAMA_HOST", _DEFAULT_HOST))
        self.model = model or os.getenv("OLLAMA_MODEL", _DEFAULT_MODEL)
        if timeout <= 0:
            raise ValueError(f"timeout must be positive, got {timeout!r}")
        self.timeout = float(timeout)
        if max_retries < 0:
            raise ValueError(f"max_retries must be >= 0, got {max_retries!r}")
        self.max_retries = int(max_retries)

    # -- public API --------------------------------------------------------- #

    def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        timeout: float | None = None,
    ) -> LLMResponse:
        """Send a non-streaming chat completion and parse the response.

        Args:
            messages: conversation history (``role`` + ``content`` dicts;
                assistant ``tool_calls`` and ``tool``-role result messages
                are passed through unchanged).
            tools: tool schemas in OpenAI/Ollama function-calling format.
            temperature: sampling temperature (low = deterministic output).
            timeout: per-request timeout in seconds; falls back to the value
                given in the constructor when ``None``.

        Returns:
            An :class:`LLMResponse` with the assistant text (possibly empty)
            and the native ``tool_calls`` requested by the model.

        Raises:
            LLMError: network failure, non-retryable HTTP error, or a
                malformed / error-carrying response.
        """
        url = f"{self.host}/api/chat"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": list(messages),
            "stream": False,
            "options": {"temperature": temperature},
        }
        if tools:
            payload["tools"] = list(tools)
        data = json.dumps(payload).encode("utf-8")
        effective_timeout = self.timeout if timeout is None else float(timeout)

        for attempt in range(self.max_retries + 1):
            # A NEW Request object per attempt (see module docstring).
            req = self._make_request(url, data)
            try:
                with urllib.request.urlopen(req, timeout=effective_timeout) as resp:
                    raw = resp.read().decode("utf-8")
            except urllib.error.HTTPError as exc:
                if exc.code in _RETRYABLE_HTTP and attempt < self.max_retries:
                    log.warning("Ollama HTTP %s (attempt %d) — retrying", exc.code, attempt + 1)
                    continue
                raise LLMError(
                    f"Ollama HTTP {exc.code} {exc.reason} at {url} "
                    f"(attempt {attempt + 1}/{self.max_retries + 1})"
                ) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt < self.max_retries:
                    log.warning("Ollama unreachable (attempt %d): %s — retrying", attempt + 1, exc)
                    continue
                raise LLMError(
                    f"cannot reach Ollama at {self.host} after "
                    f"{self.max_retries + 1} attempts: {exc}"
                ) from exc

            try:
                result = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise LLMError(f"invalid JSON response from Ollama at {url}: {exc}") from exc
            return self._parse_response(result)

        raise LLMError(f"Ollama request failed after {self.max_retries + 1} attempts")

    # -- internals ---------------------------------------------------------- #

    def _make_request(self, url: str, data: bytes) -> urllib.request.Request:
        """Build a fresh POST Request (separate method so tests can count them)."""
        return urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

    def _parse_response(self, result: dict[str, Any]) -> LLMResponse:
        """Validate the Ollama JSON and build an :class:`LLMResponse`."""
        error = result.get("error")
        if error:
            raise LLMError(f"Ollama returned an error: {error}")
        message = result.get("message")
        if not isinstance(message, dict):
            raise LLMError(f"Ollama response missing a 'message' object (keys: {sorted(result)})")

        content = message.get("content")
        content = "" if content is None else str(content)

        tool_calls: list[ToolCall] = []
        for raw_call in message.get("tool_calls") or []:
            function = raw_call.get("function", {}) if isinstance(raw_call, dict) else {}
            name = function.get("name", "") if isinstance(function, dict) else ""
            arguments = function.get("arguments", {}) if isinstance(function, dict) else {}
            # Ollama may serialise arguments as a JSON string or as a dict.
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            tool_calls.append(ToolCall(name=str(name), arguments=arguments))

        return LLMResponse(content=content, tool_calls=tuple(tool_calls), raw=result)
