"""Judges for the pcb-ai-agent eval harness.

A *judge* scores one golden case against the actual output of the read
layer, producing a :class:`JudgeVerdict` with a boolean pass decision, a
``[0, 1]`` score and a human-readable explanation.

Two implementations mirror the dual-judge design of the sibling projects
(``evalforge``, ``smart-contract-rag``):

- :class:`HeuristicJudge` — fully deterministic, zero-network scoring.
  **This is the CI judge.** Sizing values are compared within a relative
  tolerance (the golden dataset stores E12-rounded results, so 1e-6 only
  absorbs binary float noise); audit findings are scored by recall of the
  expected rules plus a penalty for unallowed extras.
- :class:`LLMJudge` — opt-in qualitative judge on top of the
  :class:`~pcbai.agent.llm_client.OllamaChatClient` (local Ollama inside
  the Docker network). It scores the quality *of an agent-style output
  text* for a case on a 0-5 scale. If Ollama is unreachable it **degrades
  honestly to the heuristic verdict** (never raises, never breaks a run) —
  and it is documented as NOT for CI use.

Both judges expose the same two methods (:meth:`score_sizing`,
:meth:`score_audit`); the runner dispatches by case type.

Score contracts
---------------
Sizing (heuristic):
    PASS when every expected value matches its actual counterpart within
    ``case.tolerance`` (relative). Score 1.0 on pass, 0.0 otherwise.

Audit (heuristic):
    - recall = |expected_rules ∩ detected| / |expected_rules| (1.0 when the
      expected set is empty);
    - extras = detected - expected_rules; when ``allow_extra_rules`` is
      False each extra rule subtracts 0.25 from the score;
    - a declared ``expected_severities`` entry that does not match the
      detected severity subtracts 0.25 per mismatch;
    - score = clamp(recall - penalties, 0, 1);
    - PASS requires recall == 1.0, no unallowed extras and no severity
      mismatch.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from pcbai.agent.llm_client import LLMError, OllamaChatClient
from pcbai.eval.golden import AuditCase, SizingCase

__all__ = [
    "EXTRA_RULE_PENALTY",
    "HeuristicJudge",
    "JUDGE_SCALE_MAX",
    "JudgeVerdict",
    "LLMJudge",
    "LLM_PASS_MIN_SCORE",
    "SEVERITY_MISMATCH_PENALTY",
]

#: Upper bound of the LLM judge scale. LLM scores are normalised ``score / 5``.
JUDGE_SCALE_MAX: float = 5.0

#: Minimum normalised LLM score for a case to pass under ``--llm-judge``
#: (3 on the 0-5 scale: "at least acceptable"). The heuristic verdict alone
#: never fails a case when the LLM is unavailable.
LLM_PASS_MIN_SCORE: float = 3.0

#: Penalty applied per unallowed extra rule in the audit score.
EXTRA_RULE_PENALTY: float = 0.25

#: Penalty applied per severity mismatch in the audit score.
SEVERITY_MISMATCH_PENALTY: float = 0.25


@dataclass(frozen=True)
class JudgeVerdict:
    """The outcome of scoring one case against its actual output.

    Attributes:
        passed: Overall pass decision (the gate for the eval run).
        score: Graded signal in ``[0, 1]`` (1.0 = perfect).
        details: Human-readable explanation, always non-empty.
    """

    passed: bool
    score: float
    details: str


class HeuristicJudge:
    """Deterministic, CI-safe judge (no network, fully reproducible)."""

    name = "heuristic"

    def score_sizing(self, case: SizingCase, actual: float | list[float]) -> JudgeVerdict:
        """Sizing: every actual value must match the expected within tolerance."""
        expected_values = (
            [case.expected] if isinstance(case.expected, (int, float)) else list(case.expected)
        )
        actual_values = [actual] if isinstance(actual, (int, float)) else list(actual)
        if len(expected_values) != len(actual_values):
            return JudgeVerdict(
                passed=False,
                score=0.0,
                details=(
                    f"shape mismatch: expected {len(expected_values)} value(s) "
                    f"({case.expected!r}), got {len(actual_values)} ({actual!r})"
                ),
            )
        # Lengths are equal (checked above): strict=True pins that invariant
        # so a future refactor cannot silently truncate the comparison.
        for index, (wanted, got) in enumerate(zip(expected_values, actual_values, strict=True)):
            if not _values_close(got, wanted, case.tolerance):
                return JudgeVerdict(
                    passed=False,
                    score=0.0,
                    details=(
                        f"value {index} mismatch: expected {wanted!r}, got {got!r} "
                        f"(relative tolerance {case.tolerance:g})"
                    ),
                )
        return JudgeVerdict(
            passed=True,
            score=1.0,
            details=(
                f"values {expected_values!r} match the hand-computed E12-expected "
                f"within tolerance {case.tolerance:g}"
            ),
        )

    def score_audit(
        self,
        case: AuditCase,
        detected_rules: set[str],
        detected_severities: dict[str, str] | None = None,
    ) -> JudgeVerdict:
        """Audit: recall of the expected rules plus extra/severity penalties.

        Args:
            case: The golden audit case (expected rules + severities).
            detected_rules: Rule ids the audit actually fired.
            detected_severities: Optional ``{rule: severity}`` map for the
                severity contract check.
        """
        detected = set(detected_rules)
        severities = dict(detected_severities or {})
        expected = set(case.expected_rules)
        matched = detected & expected
        extras = detected - expected
        missing = sorted(expected - detected)

        recall = len(matched) / len(expected) if expected else 1.0
        penalties = 0.0
        problems: list[str] = []

        if missing:
            problems.append(f"missed rule(s) {missing} (recall {len(matched)}/{len(expected)})")
        if extras and not case.allow_extra_rules:
            penalties += EXTRA_RULE_PENALTY * len(extras)
            problems.append(f"{len(extras)} unexpected extra rule(s) {sorted(extras)}")

        severity_mismatches: list[str] = []
        for rule in sorted(matched):
            wanted = case.expected_severities.get(rule)
            if wanted is not None and severities.get(rule) != wanted:
                severity_mismatches.append(
                    f"{rule} ({severities.get(rule)!r} != expected {wanted!r})"
                )
        if severity_mismatches:
            penalties += SEVERITY_MISMATCH_PENALTY * len(severity_mismatches)
            problems.append("severity mismatch(es): " + "; ".join(severity_mismatches))

        score = max(0.0, min(1.0, recall - penalties))
        if not expected and not detected:
            details = "clean design: no findings, matching the empty expected rule set"
        elif problems:
            details = "; ".join(problems)
        else:
            details = f"all {len(expected)} expected rule(s) fired with correct severities"

        passed = (
            recall == 1.0 and not severity_mismatches and (not extras or case.allow_extra_rules)
        )
        return JudgeVerdict(passed=passed, score=score, details=details)


class LLMJudge:
    """Opt-in qualitative judge backed by a local Ollama chat model.

    Scores the *quality of an agent-style answer text* for a case on the
    ``JUDGE_SCALE_MAX`` (0-5) scale via ``OllamaChatClient``.
    The prompt carries the case description, the hand-computed expected
    result and the actual output; the model must reply with a single JSON
    object ``{"score": <0-5>, "reasoning": "<short>"}`` parsed with a
    defensive cascade (fenced block -> brace matching -> trailing-comma
    repair -> raw text).

    Degradation contract (honest, never CI-breaking): if Ollama is
    unreachable, times out, returns an error, or the response does not
    parse, the verdict falls back to the *heuristic* verdict with the
    failure recorded in ``details``. ``passed`` additionally requires the
    normalised LLM score to reach ``LLM_PASS_MIN_SCORE``.
    """

    name = "llm"

    def __init__(
        self,
        *,
        client: OllamaChatClient | None = None,
        model: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._client = client or OllamaChatClient(model=model, timeout=timeout)
        self._heuristic = HeuristicJudge()

    def score_sizing(self, case: SizingCase, actual: float | list[float]) -> JudgeVerdict:
        heuristic = self._heuristic.score_sizing(case, actual)
        content = self._ask_model(case, actual)
        return self._combine(heuristic, content)

    def score_audit(
        self,
        case: AuditCase,
        detected_rules: set[str],
        detected_severities: dict[str, str] | None = None,
    ) -> JudgeVerdict:
        heuristic = self._heuristic.score_audit(case, detected_rules, detected_severities)
        content = self._ask_model(case, detected_rules)
        return self._combine(heuristic, content)

    # ------------------------------------------------------------------
    def _combine(self, heuristic: JudgeVerdict, content: str | tuple[str, str]) -> JudgeVerdict:
        if isinstance(content, tuple):  # (reason, error) — degraded
            reason, error = content
            return JudgeVerdict(
                passed=heuristic.passed,
                score=heuristic.score,
                details=f"[llm judge degraded to heuristic: {reason} {error}] {heuristic.details}",
            )
        score_raw = _extract_score(content)
        if score_raw is None:
            return JudgeVerdict(
                passed=heuristic.passed,
                score=heuristic.score,
                details=(
                    "[llm judge degraded to heuristic: response did not parse as "
                    f"a {{score, reasoning}} JSON object] {heuristic.details}"
                ),
            )
        normalised = max(0.0, min(1.0, score_raw / JUDGE_SCALE_MAX))
        passed = heuristic.passed and normalised >= LLM_PASS_MIN_SCORE / JUDGE_SCALE_MAX
        return JudgeVerdict(
            passed=passed,
            score=normalised,
            details=(
                f"llm judge {score_raw:g}/{JUDGE_SCALE_MAX:g} "
                f"(pass threshold {LLM_PASS_MIN_SCORE:g}) — {self._reasoning(content)}"
            ),
        )

    # ------------------------------------------------------------------
    def _ask_model(self, case: SizingCase | AuditCase, actual: Any) -> str | tuple[str, str]:
        """Call the judge model; returns the answer text or ``(reason, error)``."""
        try:
            response = self._client.chat(
                messages=[
                    {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
                    {"role": "user", "content": self._user_prompt(case, actual)},
                ],
                temperature=0.0,
            )
        except (LLMError, OSError, ValueError) as exc:
            return "unreachable", f"({type(exc).__name__}: {exc})"
        content = (response.content or "").strip()
        if not content:
            return "empty", "(ollama returned an empty answer)"
        return content

    # ------------------------------------------------------------------
    def _user_prompt(self, case: SizingCase | AuditCase, actual: Any) -> str:
        if isinstance(case, SizingCase):
            context = f"FUNCTION: {case.function}({_format_inputs(case.inputs)})"
            expected = _format_values(case.expected)
            got = _format_values(actual)
            description = case.description
        else:
            context = f"FIXTURE: {case.fixture}"
            expected = ", ".join(case.expected_rules) or "(none — the design must be clean)"
            got = ", ".join(sorted(set(actual)))
            description = case.description or f"audit of {case.fixture}"
        return (
            f"CASE: {description}\n"
            f"CONTEXT: {context}\n"
            f"EXPECTED: {expected}\n"
            f"CANDIDATE OUTPUT: {got}\n"
        )

    @staticmethod
    def _reasoning(content: str) -> str:
        parsed = _extract_json_object(content)
        if parsed and isinstance(parsed.get("reasoning"), str):
            return parsed["reasoning"].strip()
        return "no reasoning given"


def _format_values(value: Any) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(f"{number:g}" for number in value) + "]"
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return str(value)


def _format_inputs(inputs: dict[str, float]) -> str:
    return ", ".join(f"{name}={value:g}" for name, value in sorted(inputs.items()))


def _values_close(actual: float, expected: float, tolerance: float) -> bool:
    """Relative closeness with a floor for near-zero expected values."""
    scale = max(abs(expected), 1e-12)
    return abs(actual - expected) <= tolerance * scale


def _extract_score(text: str) -> float | None:
    """Pull a numeric ``score`` out of the model's JSON reply, if present."""
    parsed = _extract_json_object(text)
    if parsed is None:
        return None
    raw = parsed.get("score", parsed.get("quality"))
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Defensive JSON object extraction (lessons from the sibling evals).

    Cascade: fenced ```json``` block, outermost brace substring, raw text;
    every candidate is also tried with trailing commas repaired.
    """
    if not text or not text.strip():
        return None
    candidates: list[str] = []
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence:
        candidates.append(fence.group(1))
    if "{" in text and "}" in text:
        candidates.append(text[text.index("{") : text.rindex("}") + 1])
    candidates.append(text.strip())
    for candidate in candidates:
        for attempt in (candidate, _fix_trailing_commas(candidate)):
            try:
                data = json.loads(attempt)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(data, dict):
                return data
    return None


def _fix_trailing_commas(text: str) -> str:
    """Remove trailing commas before closing braces/brackets (LLM slip)."""
    return re.sub(r",\s*([}\]])", r"\1", text)


_JUDGE_SYSTEM_PROMPT = """\
You are a strict, impartial evaluation judge for a PCB design assistant. You
are given a CASE (the design question), the EXPECTED result (computed by hand
with the documented formulas) and the CANDIDATE OUTPUT of the system.

Rate the candidate output on a single axis 0 to 5:
- correctness: does it match the expected result (values, rules)?
- clarity: is it a concise, useful engineering answer?

5 = fully correct and clear, 0 = wrong or empty.

Respond with a single JSON object and nothing else:
{"score": <int or float 0-5>, "reasoning": "<one short line>"}
"""
