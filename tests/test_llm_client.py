"""Tests for the local Ollama chat client (no network — mocks only).

Every test replaces ``urllib.request.urlopen`` (and counts ``Request``
objects via ``urllib.request.Request``) so the client never touches a real
socket. Covered: payload correctness, tool-call parsing (dict and JSON-string
arguments), the retry policy with a **fresh Request per attempt**, timeout
handling and the ``LLMError`` contract.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from pcbai.agent.llm_client import LLMError, OllamaChatClient, ToolCall

ENDPOINT = "http://ollama:11434/api/chat"


class FakeResponse:
    """Minimal stand-in for the ``urllib`` response object."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


def _chat_body(*, content: str = "ok", tool_calls: list[dict[str, object]] | None = None) -> bytes:
    message: dict[str, object] = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return json.dumps({"model": "qwen2.5-coder:7b", "message": message, "done": True}).encode()


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(ENDPOINT, code, f"HTTP {code}", {}, None)


# --------------------------------------------------------------------------- #
# Payload construction
# --------------------------------------------------------------------------- #


class TestPayload:
    def _capture(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
        """Install a urlopen fake and return the captured call info."""
        captured: dict[str, object] = {}

        def fake_urlopen(req: urllib.request.Request, *, timeout: float) -> FakeResponse:
            captured["url"] = req.full_url
            captured["data"] = req.data
            captured["timeout"] = timeout
            return FakeResponse(_chat_body())

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        return captured

    def test_default_model_and_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured = self._capture(monkeypatch)
        client = OllamaChatClient()
        client.chat(messages=[{"role": "user", "content": "hi"}])

        assert captured["url"] == ENDPOINT
        payload = json.loads(captured["data"])  # type: ignore[arg-type]
        assert payload["model"] == "qwen2.5-coder:7b"
        assert payload["stream"] is False
        assert payload["options"] == {"temperature": 0.2}
        assert payload["messages"] == [{"role": "user", "content": "hi"}]
        assert "tools" not in payload

    def test_messages_and_tools_in_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured = self._capture(monkeypatch)
        messages = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "x"}}]},
            {"role": "tool", "content": "{}"},
        ]
        tools = [{"type": "function", "function": {"name": "load_design"}}]
        client = OllamaChatClient()
        client.chat(messages=messages, tools=tools)

        payload = json.loads(captured["data"])  # type: ignore[arg-type]
        assert payload["messages"] == messages
        assert payload["tools"] == tools

    def test_temperature_forwarded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured = self._capture(monkeypatch)
        OllamaChatClient().chat(messages=[], temperature=0.7)
        payload = json.loads(captured["data"])  # type: ignore[arg-type]
        assert payload["options"] == {"temperature": 0.7}

    def test_env_model_and_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OLLAMA_MODEL", "qwen2.5:7b")
        monkeypatch.setenv("OLLAMA_HOST", "http://ollama-other:11434")
        captured = self._capture(monkeypatch)
        client = OllamaChatClient()
        client.chat(messages=[])
        assert client.model == "qwen2.5:7b"
        assert captured["url"] == "http://ollama-other:11434/api/chat"

    def test_constructor_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured = self._capture(monkeypatch)
        client = OllamaChatClient(model="custom:8b", host="localhost:11434")
        client.chat(messages=[])
        assert client.host == "http://localhost:11434"  # scheme added
        payload = json.loads(captured["data"])  # type: ignore[arg-type]
        assert payload["model"] == "custom:8b"
        assert captured["url"] == "http://localhost:11434/api/chat"

    def test_timeout_argument_respected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured = self._capture(monkeypatch)
        OllamaChatClient().chat(messages=[], timeout=7.0)
        assert captured["timeout"] == 7.0

    def test_default_timeout_used_when_omitted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured = self._capture(monkeypatch)
        OllamaChatClient().chat(messages=[])
        assert captured["timeout"] == 60.0

    def test_invalid_timeout_rejected(self) -> None:
        with pytest.raises(ValueError, match="timeout"):
            OllamaChatClient(timeout=0)

    def test_invalid_max_retries_rejected(self) -> None:
        with pytest.raises(ValueError, match="max_retries"):
            OllamaChatClient(max_retries=-1)


# --------------------------------------------------------------------------- #
# Response parsing
# --------------------------------------------------------------------------- #


class TestParsing:
    def _install(self, monkeypatch: pytest.MonkeyPatch, body: bytes) -> None:
        monkeypatch.setattr(urllib.request, "urlopen", lambda req, *, timeout: FakeResponse(body))

    def test_parses_content_and_tool_calls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        body = _chat_body(
            content="",
            tool_calls=[
                {
                    "function": {
                        "name": "size_resistor",
                        "arguments": {"v_supply": 5.0, "v_led": 2.0, "i_led": 0.02},
                    }
                }
            ],
        )
        self._install(monkeypatch, body)
        response = OllamaChatClient().chat(messages=[])

        assert response.content == ""
        assert len(response.tool_calls) == 1
        call = response.tool_calls[0]
        assert isinstance(call, ToolCall)
        assert call.name == "size_resistor"
        assert call.arguments == {"v_supply": 5.0, "v_led": 2.0, "i_led": 0.02}

    def test_arguments_as_json_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        body = _chat_body(
            tool_calls=[{"function": {"name": "load_design", "arguments": '{"path": "x.net"}'}}]
        )
        self._install(monkeypatch, body)
        response = OllamaChatClient().chat(messages=[])
        assert response.tool_calls[0].arguments == {"path": "x.net"}

    def test_malformed_arguments_string_becomes_empty_dict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body = _chat_body(
            tool_calls=[{"function": {"name": "load_design", "arguments": "{not json"}}]
        )
        self._install(monkeypatch, body)
        response = OllamaChatClient().chat(messages=[])
        assert response.tool_calls[0].arguments == {}

    def test_no_tool_calls_returns_empty_tuple(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._install(monkeypatch, _chat_body(content="plain answer"))
        response = OllamaChatClient().chat(messages=[])
        assert response.content == "plain answer"
        assert response.tool_calls == ()

    def test_none_content_becomes_empty_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        body = json.dumps({"message": {"role": "assistant", "content": None}}).encode()
        self._install(monkeypatch, body)
        response = OllamaChatClient().chat(messages=[])
        assert response.content == ""

    def test_multiple_tool_calls_keep_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        body = _chat_body(
            tool_calls=[
                {"function": {"name": "a", "arguments": {}}},
                {"function": {"name": "b", "arguments": {}}},
            ]
        )
        self._install(monkeypatch, body)
        response = OllamaChatClient().chat(messages=[])
        assert [call.name for call in response.tool_calls] == ["a", "b"]

    def test_missing_message_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._install(monkeypatch, b'{"done": true}')
        with pytest.raises(LLMError, match="message"):
            OllamaChatClient().chat(messages=[])

    def test_error_field_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._install(monkeypatch, b'{"error": "model not found"}')
        with pytest.raises(LLMError, match="model not found"):
            OllamaChatClient().chat(messages=[])

    def test_invalid_json_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._install(monkeypatch, b"<html>not json</html>")
        with pytest.raises(LLMError, match="invalid JSON"):
            OllamaChatClient().chat(messages=[])


# --------------------------------------------------------------------------- #
# Retries (fresh Request per attempt)
# --------------------------------------------------------------------------- #


class TestRetries:
    def _install(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *responses: object,
    ) -> list[urllib.request.Request]:
        """Install urlopen raising/calling each ``responses`` in order and
        return every Request object created by the client."""
        requests: list[urllib.request.Request] = []
        real_request = urllib.request.Request

        def spy_request(*args: object, **kwargs: object) -> urllib.request.Request:
            request = real_request(*args, **kwargs)  # type: ignore[arg-type]
            requests.append(request)
            return request

        monkeypatch.setattr(urllib.request, "Request", spy_request)

        calls: list[object] = list(responses)

        def fake_urlopen(req: urllib.request.Request, *, timeout: float) -> FakeResponse:
            outcome = calls.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return FakeResponse(outcome)  # type: ignore[arg-type]

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        return requests

    def test_retries_on_503_then_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        requests = self._install(monkeypatch, _http_error(503), _chat_body(content="recovered"))
        response = OllamaChatClient().chat(messages=[])
        assert len(requests) == 2  # 1 attempt + 1 retry
        assert len({id(req) for req in requests}) == 2  # fresh Request per attempt
        assert response.content == "recovered"

    def test_retries_on_502_then_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        requests = self._install(monkeypatch, _http_error(502), _chat_body(content="recovered"))
        response = OllamaChatClient().chat(messages=[])
        assert len(requests) == 2
        assert response.content == "recovered"

    def test_retries_exhausted_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        requests = self._install(monkeypatch, _http_error(503), _http_error(503), _http_error(503))
        with pytest.raises(LLMError, match="503"):
            OllamaChatClient().chat(messages=[])
        assert len(requests) == 3  # max_retries + 1

    def test_non_retryable_http_raises_immediately(self, monkeypatch: pytest.MonkeyPatch) -> None:
        requests = self._install(monkeypatch, _http_error(400), _chat_body())
        with pytest.raises(LLMError, match="400"):
            OllamaChatClient().chat(messages=[])
        assert len(requests) == 1

    def test_500_not_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        requests = self._install(monkeypatch, _http_error(500), _chat_body())
        with pytest.raises(LLMError, match="500"):
            OllamaChatClient().chat(messages=[])
        assert len(requests) == 1

    def test_urlerror_retried_then_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        refused = urllib.error.URLError(ConnectionRefusedError("connection refused"))
        requests = self._install(monkeypatch, refused, _chat_body(content="back"))
        response = OllamaChatClient().chat(messages=[])
        assert len(requests) == 2
        assert response.content == "back"

    def test_urlerror_exhausted_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        refused = urllib.error.URLError(ConnectionRefusedError("connection refused"))
        requests = self._install(monkeypatch, refused, refused, refused)
        with pytest.raises(LLMError, match="cannot reach Ollama"):
            OllamaChatClient().chat(messages=[])
        assert len(requests) == 3

    def test_read_timeout_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        requests = self._install(monkeypatch, TimeoutError("timed out"), _chat_body())
        response = OllamaChatClient().chat(messages=[])
        assert len(requests) == 2
        assert response.content == "ok"

    def test_timeout_exhausted_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        requests = self._install(monkeypatch, TimeoutError(), TimeoutError(), TimeoutError())
        with pytest.raises(LLMError, match="cannot reach Ollama"):
            OllamaChatClient().chat(messages=[])
        assert len(requests) == 3

    def test_zero_retries_means_single_attempt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        requests = self._install(monkeypatch, _http_error(503), _chat_body())
        with pytest.raises(LLMError, match="503"):
            OllamaChatClient(max_retries=0).chat(messages=[])
        assert len(requests) == 1
