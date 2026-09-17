"""Tests for the eval judges.

The heuristic judge is the CI gate: fully deterministic, zero network.
The LLM judge is exercised with a scriptable fake chat client — never a
real Ollama call — so the degradation contract (unreachable / unparseable
reply falls back to the heuristic verdict) and the happy pass path are
both pinned with no network dependency.
"""

from __future__ import annotations

from pcbai.agent.llm_client import LLMError
from pcbai.eval.golden import DEFAULT_SIZING_TOLERANCE, AuditCase, SizingCase
from pcbai.eval.judge import (
    EXTRA_RULE_PENALTY,
    SEVERITY_MISMATCH_PENALTY,
    HeuristicJudge,
    LLMJudge,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sizing_case(
    expected: float | list[float],
    *,
    tolerance: float = DEFAULT_SIZING_TOLERANCE,
) -> SizingCase:
    return SizingCase(
        id="sizing_test",
        description="Test sizing case",
        function="pull_up_resistor",
        inputs={"pull_current_ua": 200.0, "v_high_min": 2.36, "v_supply": 3.3},
        expected=expected,
        why="R=(3.3-2.36)/200e-6=4700 -> E12 4.7k (hand-computed)",
        tolerance=tolerance,
    )


def _audit_case(
    expected_rules: list[str],
    *,
    expected_severities: dict[str, str] | None = None,
    allow_extra_rules: bool = False,
) -> AuditCase:
    return AuditCase(
        id="audit_test",
        description="Test audit case",
        fixture="tests/fixtures/simple-led.kicad_net",
        expected_rules=expected_rules,
        expected_severities=expected_severities or {},
        allow_extra_rules=allow_extra_rules,
        why="Hand-built audit case for the judge tests",
    )


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeClient:
    """Scriptable stand-in for ``OllamaChatClient`` (records every call)."""

    def __init__(self, content: str | None = None, *, error: Exception | None = None) -> None:
        self._content = content
        self._error = error
        self.calls: list[tuple[list[dict[str, str]], float]] = []

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.0) -> _FakeResponse:
        self.calls.append((messages, temperature))
        if self._error is not None:
            raise self._error
        return _FakeResponse(self._content or "")


# ---------------------------------------------------------------------------
# Heuristic judge — sizing
# ---------------------------------------------------------------------------


def test_heuristic_sizing_exact_match_passes() -> None:
    verdict = HeuristicJudge().score_sizing(_sizing_case(4700.0), 4700.0)
    assert verdict.passed
    assert verdict.score == 1.0
    assert "match" in verdict.details


def test_heuristic_sizing_absorbs_float_noise() -> None:
    # 4700.0 + 1e-9 is within the 1e-6 relative tolerance (binary float
    # noise absorption, the whole point of the tolerance).
    verdict = HeuristicJudge().score_sizing(_sizing_case(4700.0), 4700.0 + 1e-9)
    assert verdict.passed


def test_heuristic_sizing_value_mismatch_fails() -> None:
    verdict = HeuristicJudge().score_sizing(_sizing_case(4700.0), 5600.0)
    assert not verdict.passed
    assert verdict.score == 0.0
    assert "mismatch" in verdict.details


def test_heuristic_sizing_list_expected_against_scalar_actual() -> None:
    # The dataset stores scalar-function results as a single-element list;
    # the judge must accept both shapes and match on length/content.
    verdict = HeuristicJudge().score_sizing(_sizing_case([4700.0]), 4700.0)
    assert verdict.passed


def test_heuristic_sizing_shape_mismatch_fails() -> None:
    # Pair-function actual scored against a scalar expectation: shape
    # mismatch, not a value comparison — the message must say so.
    verdict = HeuristicJudge().score_sizing(_sizing_case(4700.0), [4700.0, 0.06])
    assert not verdict.passed
    assert verdict.score == 0.0
    assert "shape mismatch" in verdict.details


# ---------------------------------------------------------------------------
# Heuristic judge — audit (recall, extras, severities)
# ---------------------------------------------------------------------------


def test_heuristic_audit_perfect_recall_passes() -> None:
    verdict = HeuristicJudge().score_audit(
        _audit_case(["LED_NO_LIMITER", "FLOATING_NET"]),
        {"LED_NO_LIMITER", "FLOATING_NET"},
    )
    assert verdict.passed
    assert verdict.score == 1.0


def test_heuristic_audit_missing_rule_misses_recall() -> None:
    verdict = HeuristicJudge().score_audit(
        _audit_case(["LED_NO_LIMITER", "FLOATING_NET"]),
        {"LED_NO_LIMITER"},
    )
    assert not verdict.passed
    assert verdict.score == 0.5  # recall 1/2, no penalties
    assert "missed rule(s)" in verdict.details


def test_heuristic_audit_extra_rule_penalised_when_not_allowed() -> None:
    verdict = HeuristicJudge().score_audit(
        _audit_case(["LED_NO_LIMITER"]),
        {"LED_NO_LIMITER", "FLOATING_NET"},
    )
    assert not verdict.passed
    assert verdict.score == 1.0 - EXTRA_RULE_PENALTY
    assert "extra rule" in verdict.details


def test_heuristic_audit_extra_rule_tolerated_when_allowed() -> None:
    verdict = HeuristicJudge().score_audit(
        _audit_case(["LED_NO_LIMITER"], allow_extra_rules=True),
        {"LED_NO_LIMITER", "FLOATING_NET"},
    )
    assert verdict.passed
    assert verdict.score == 1.0


def test_heuristic_audit_severity_mismatch_fails() -> None:
    verdict = HeuristicJudge().score_audit(
        _audit_case(["LED_NO_LIMITER"], expected_severities={"LED_NO_LIMITER": "warning"}),
        {"LED_NO_LIMITER"},
        {"LED_NO_LIMITER": "info"},
    )
    assert not verdict.passed
    assert verdict.score == 1.0 - SEVERITY_MISMATCH_PENALTY
    assert "severity mismatch" in verdict.details


def test_heuristic_audit_clean_design_passes_with_no_findings() -> None:
    verdict = HeuristicJudge().score_audit(_audit_case([]), set())
    assert verdict.passed
    assert verdict.score == 1.0
    assert "clean design" in verdict.details


def test_heuristic_audit_clean_design_fails_on_any_finding() -> None:
    # Empty expected rule set with a detected rule: recall is 1.0 by
    # convention but the unallowed extra rule fails the case.
    verdict = HeuristicJudge().score_audit(_audit_case([]), {"NO_DRIVER"})
    assert not verdict.passed
    assert verdict.score == 1.0 - EXTRA_RULE_PENALTY


# ---------------------------------------------------------------------------
# LLM judge — fake client, no network (degradation contract)
# ---------------------------------------------------------------------------


def test_llm_judge_passes_when_heuristic_and_llm_agree() -> None:
    client = _FakeClient('{"score": 5, "reasoning": "correct and clear"}')
    verdict = LLMJudge(client=client).score_sizing(_sizing_case(4700.0), 4700.0)
    assert verdict.passed
    assert verdict.score == 1.0
    assert client.calls, "the LLM judge must ask the model"


def test_llm_judge_asks_with_temperature_zero() -> None:
    client = _FakeClient('{"score": 4, "reasoning": "ok"}')
    LLMJudge(client=client).score_sizing(_sizing_case(4700.0), 4700.0)
    messages, temperature = client.calls[0]
    assert temperature == 0.0
    prompt_text = "\n".join(message["content"] for message in messages)
    assert messages[0]["role"] == "system"
    # The prompt carries the case description, the function context and
    # both expected and candidate values.
    assert "Test sizing case" in prompt_text
    assert "pull_up_resistor" in prompt_text
    assert "4700" in prompt_text


def test_llm_judge_cannot_rescue_a_failing_heuristic() -> None:
    client = _FakeClient('{"score": 5, "reasoning": "perfect"}')
    verdict = LLMJudge(client=client).score_sizing(_sizing_case(4700.0), 5600.0)
    assert not verdict.passed  # the heuristic gate stays authoritative


def test_llm_judge_low_llm_score_fails_but_keeps_heuristic_score() -> None:
    client = _FakeClient('{"score": 1, "reasoning": "wrong"}')
    verdict = LLMJudge(client=client).score_sizing(_sizing_case(4700.0), 4700.0)
    assert not verdict.passed
    assert verdict.score == 0.2  # 1/5 normalised
    assert "llm judge" in verdict.details


def test_llm_judge_degrades_when_ollama_unreachable() -> None:
    client = _FakeClient(error=LLMError("connection refused"))
    verdict = LLMJudge(client=client).score_sizing(_sizing_case(4700.0), 4700.0)
    assert verdict.passed  # honest fallback to the heuristic verdict
    assert "degraded to heuristic" in verdict.details


def test_llm_judge_degrades_on_unparseable_reply() -> None:
    client = _FakeClient("I cannot rate this design without more context.")
    verdict = LLMJudge(client=client).score_sizing(_sizing_case(4700.0), 4700.0)
    assert verdict.passed
    assert "degraded to heuristic" in verdict.details


def test_llm_judge_degrades_on_empty_reply() -> None:
    client = _FakeClient("")
    verdict = LLMJudge(client=client).score_audit(_audit_case(["NO_DRIVER"]), {"NO_DRIVER"})
    assert verdict.passed
    assert "degraded to heuristic" in verdict.details


def test_llm_judge_audit_prompt_uses_description_or_fixture() -> None:
    # Regression guard: AuditCase.description defaults to empty, so the
    # LLM prompt must fall back to the fixture and never raise.
    client = _FakeClient('{"score": 5, "reasoning": "rules match"}')
    case = _audit_case(["NO_DRIVER"])
    verdict = LLMJudge(client=client).score_audit(case, {"NO_DRIVER"})
    assert verdict.passed
    messages, _temperature = client.calls[0]
    prompt_text = "\n".join(message["content"] for message in messages)
    assert "tests/fixtures/simple-led.kicad_net" in prompt_text
    assert "NO_DRIVER" in prompt_text
