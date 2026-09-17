"""Deterministic anti-hallucination validation of the agent's final answer.

The ReAct agent is a *local LLM*: it may phrase facts loosely, round numbers
or invent references. Grounding v1 is a pure, deterministic, LLM-free
sanity layer that anchors the final answer to what the tools actually
returned (and, when a design was parsed, to the design itself):

- ``REF_IN_ANSWER``     — every reference designator (``[A-Z]+[0-9]+``, e.g.
  ``R1``, ``LED1``, ``U2``, ``J1``) mentioned in the answer must exist in the
  loaded design *or* in a trace tool result. Anything else is ``unsupported``.
- ``NUMBERS_FROM_SIZING`` — when ``size_resistor`` was invoked, any numeric
  claim in the answer about resistance/capacitance/power must match a value
  the tool actually returned (tolerant to rounding and formatting: ``150``,
  ``150 Ω``, ``150.0``, ``4.7 kΩ`` are normalised to base SI units and
  compared with a 5 % tolerance).
- ``NO_DATA_NO_CLAIM``  — if no tool was invoked at all, an answer that
  asserts references or numbers is never grounded (ok=False). This enforces
  the project invariant: *the LLM decides, the code (tools) calculates*.

Design constraints:

- Deterministic: same inputs, same report. No LLM involved.
- Never executes or compiles anything: it only reads the answer text and the
  trace steps it is given.
- Heuristics are deliberately conservative and documented in ``docs/AGENT.md``
  (e.g. a small denylist keeps STM32 peripheral names like ``UART1`` or the
  E12 series from being mistaken for component refs).

Usage::

    report = validate_answer(final_answer, trace.steps, design=design)
    report.ok            # False when any unsupported claim was found
    report.unsupported   # human-readable list of the offending claims
    report.checks        # one entry per executed check (transparency)
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterator, Sequence
from typing import TYPE_CHECKING, Any

from pcbai.kicad.netlist import Design

if TYPE_CHECKING:  # type-checking only: avoids a runtime import cycle
    from pcbai.agent.react_agent import ToolCall

__all__ = ["GroundingReport", "REF_DENYLIST", "validate_answer"]

# --------------------------------------------------------------------------- #
# Reference / number patterns
# --------------------------------------------------------------------------- #

#: Full-token reference designator: letters then digits (R1, LED1, U2, J1).
#: Word boundaries reject substrings like the ``I2`` inside ``I2C``.
_REF_RE = re.compile(r"\b[A-Z]+[0-9]+\b")

#: Any plain number token (used by NO_DATA_NO_CLAIM).
_NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?\b")

#: Resistance / capacitance / power units (longest alternative first), with
#: the SI multiplier each unit implies. Only these units are treated as
#: numeric *claims* — a "5 V" or "20 mA" mention is contextual, not a
#: resistance/capacitance/power value asserted by the model.
#:
#: Alphabetic unit *names* (``ohm(s)``, ``watt(s)``, ``farad(s)``) are matched
#: case-insensitively via a scoped ``(?i:...)`` flag ("150 Ohms"), with an
#: optional SI prefix letter ("4.7 kOhms"). SI *symbols* (``Ω``, ``µF``,
#: ``kW``…) stay case-sensitive on purpose: ``mΩ``/``MΩ`` (milli-/mega-ohm)
#: and ``mW``/``MW`` are distinct units that must never collapse. Both regexes
#: share the same alternative list so sentence detection and value capture
#: cannot drift.
_RC_P_UNIT_PATTERN = (
    r"kΩ|MΩ|mΩ|Ω|"
    r"k(?i:ohms?)|M(?i:ohms?)|m(?i:ohms?)|(?i:ohms?)|"
    r"kW|mW|MW|(?i:watts?)|W|"
    r"µF|uF|nF|pF|mF|(?i:farads?)|F"
)
_RC_P_UNIT_RE = re.compile(rf"(?:{_RC_P_UNIT_PATTERN})")
#: Number optionally followed by an r/c/p unit. A negative lookbehind rejects
#: digits inside words (the ``12`` in ``E12``, the ``1`` in ``UART1``, the
#: ``2`` in ``I2C``) so only standalone values become claims.
_RC_P_VALUE_RE = re.compile(rf"(?<![A-Za-z0-9])(\d+(?:\.\d+)?)\s*({_RC_P_UNIT_PATTERN})?")
_RC_P_UNIT_MULTIPLIER: dict[str, float] = {
    # SI symbols — case-significant, looked up verbatim (mΩ ≠ MΩ, mW ≠ MW).
    "kΩ": 1e3,
    "MΩ": 1e6,
    "mΩ": 1e-3,
    "Ω": 1.0,
    "kW": 1e3,
    "mW": 1e-3,
    "MW": 1e6,
    "W": 1.0,
    "µF": 1e-6,
    "uF": 1e-6,
    "nF": 1e-9,
    "pF": 1e-12,
    "mF": 1e-3,
    "F": 1.0,
    # Alphabetic unit names — canonical keys reached via
    # _rc_p_unit_multiplier(); the leading kilo/mega/milli prefix keeps its
    # case (mOhms = milli, MOhms = mega), the name itself is lowercased.
    "kohm": 1e3,
    "kohms": 1e3,
    "Mohm": 1e6,
    "Mohms": 1e6,
    "mohm": 1e-3,
    "mohms": 1e-3,
    "ohm": 1.0,
    "ohms": 1.0,
    "watt": 1.0,
    "watts": 1.0,
    "farad": 1.0,
    "farads": 1.0,
}

#: Case-significant SI prefix letters allowed before an alphabetic unit name
#: (``kOhms`` = kilo, ``mOhms`` = milli, ``MOhms`` = mega).
_RC_P_ALPHA_PREFIXES = "kMm"


def _rc_p_unit_multiplier(unit: str | None) -> float:
    """SI multiplier for a captured unit, tolerating case in unit names.

    Symbols (``Ω``, ``µF``, ``kW``…) are case-significant and looked up
    verbatim, so ``mΩ`` and ``MΩ`` (milli- vs mega-ohm) and ``mW``/``MW``
    never collapse. Unit *names* (``Ohm(s)``, ``Watt(s)``, ``Farad(s)``) are
    matched case-insensitively by the regexes and normalised to their
    canonical dict key here: the name is lowercased, but a leading SI prefix
    letter (``k``/``M``/``m``) keeps its case for the same milli-vs-mega
    reason.
    """
    if not unit:
        return 1.0
    if unit in _RC_P_UNIT_MULTIPLIER:  # symbols and already-canonical names
        return _RC_P_UNIT_MULTIPLIER[unit]
    if len(unit) > 1 and unit[0] in _RC_P_ALPHA_PREFIXES:
        key = unit[0] + unit[1:].lower()
    else:
        key = unit.lower()
    return _RC_P_UNIT_MULTIPLIER[key]


#: Words that mark a sentence as an r/c/p claim, so a *bare* number in it
#: (e.g. "the resistor should be 150") is treated as a value claim.
_RC_P_VOCAB_RE = re.compile(r"resist|resistor|ohm|watt|watts|power|capacit|E12", re.IGNORECASE)

_SENTENCE_RE = re.compile(r"[.;]\s+|\n+")

#: Tokens that look like refs but are standard electronics jargon. The
#: denylist is deliberately small and extensible (see docs/AGENT.md).
REF_DENYLIST: frozenset[str] = frozenset(
    {
        "E12",
        "E24",
        "E96",
        "I2C",
        *[f"TIM{n}" for n in range(1, 18)],
        *[f"UART{n}" for n in range(1, 9)],
        *[f"USART{n}" for n in range(1, 9)],
        *[f"I2C{n}" for n in range(1, 7)],
        *[f"SPI{n}" for n in range(1, 7)],
        *[f"ADC{n}" for n in range(1, 4)],
        *[f"DAC{n}" for n in range(1, 3)],
        *[f"CAN{n}" for n in range(1, 4)],
        *[f"USB{n}" for n in range(1, 4)],
    }
)

#: URL / host:port tokens stripped from the answer before analysis (a model
#: error message may legitimately mention ``http://ollama:11434``).
_URL_RE = re.compile(r"\S+://\S+")
_HOSTPORT_RE = re.compile(r"[A-Za-z0-9._-]+:\d+|\b\d+:\d+\b")

#: Relative tolerance for numeric claim matching (rounding forgiveness).
_REL_TOL = 0.05


# --------------------------------------------------------------------------- #
# Report model
# --------------------------------------------------------------------------- #


class GroundingReport:
    """Result of :func:`validate_answer` — deterministic and transparent.

    Attributes:
        ok: ``True`` when nothing unsupported was found.
        unsupported: human-readable list of the offending claims.
        checks: one entry per executed check (what was verified and the
            outcome), for transparency and tests.
    """

    def __init__(self, *, ok: bool, unsupported: list[str], checks: list[str]) -> None:
        self.ok = ok
        self.unsupported = list(unsupported)
        self.checks = list(checks)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        status = "ok" if self.ok else "UNSUPPORTED"
        return f"GroundingReport({status}, {len(self.unsupported)} unsupported, {len(self.checks)} checks)"


# --------------------------------------------------------------------------- #
# Value extraction helpers (pure, deterministic)
# --------------------------------------------------------------------------- #


def _clean_answer(answer: str) -> str:
    """Strip URL-ish and host:port tokens the model may cite verbatim."""
    text = _URL_RE.sub(" ", answer)
    text = _HOSTPORT_RE.sub(" ", text)
    return text


def _extract_refs(text: str) -> set[str]:
    """Full-token refs in ``text``, minus the denylist."""
    return {match for match in _REF_RE.findall(text) if match not in REF_DENYLIST}


def _iter_strings(value: Any) -> Iterator[str]:
    """Recursively yield every string inside a JSON-serialisable value."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_strings(item)


def _refs_in_value(value: Any) -> set[str]:
    refs: set[str] = set()
    for text in _iter_strings(value):
        refs |= _extract_refs(text)
    return refs


def _numbers_in_value(value: Any) -> list[float]:
    """Every float/int inside a JSON-serialisable value (for sizing support)."""
    numbers: list[float] = []
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numbers.append(float(value))
    elif isinstance(value, dict):
        for item in value.values():
            numbers.extend(_numbers_in_value(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            numbers.extend(_numbers_in_value(item))
    return numbers


def _value_claims(answer: str) -> list[tuple[str, float]]:
    """Resistance/capacitance/power claims in the answer.

    Returns ``(raw, base_value)`` pairs. A number is a claim when its sentence
    carries an r/c/p unit (Ω, W, F…) *or* r/c/p vocabulary (resistor, power,
    capacitor…), so bare counts ("3 components") are not flagged. A bare
    number directly followed by a letter is left alone too: "5 V" and "20 mA"
    are contextual figures, not r/c/p values (the r/c/p units are captured by
    the value regex and bypass this rule).
    """
    claims: list[tuple[str, float]] = []
    for sentence in _SENTENCE_RE.split(answer):
        sentence = sentence.strip()
        if not sentence:
            continue
        has_unit = _RC_P_UNIT_RE.search(sentence)
        is_claim_sentence = _RC_P_VOCAB_RE.search(sentence)
        if not (has_unit or is_claim_sentence):
            continue
        for match in _RC_P_VALUE_RE.finditer(sentence):
            raw_number = match.group(1)
            unit = match.group(2)
            if unit is None:
                following = sentence[match.end() :].lstrip()
                if following and following[0].isalpha():
                    # "5 V", "20 mA", "3 Vrms" — contextual, not an r/c/p claim.
                    continue
            value = float(raw_number) * _rc_p_unit_multiplier(unit)
            raw = f"{raw_number} {unit}".strip() if unit else raw_number
            claims.append((raw, value))
    return claims


# --------------------------------------------------------------------------- #
# Public validation
# --------------------------------------------------------------------------- #


def validate_answer(
    answer: str,
    steps: Sequence[ToolCall],
    design: Design | None = None,
) -> GroundingReport:
    """Check the agent's final answer against the evidence it gathered.

    Args:
        answer: the final answer text produced by the ReAct agent.
        steps: the trace tool calls (each with ``name``, ``arguments`` and
            ``result``) recorded during the run. Accepts any sequence of
            objects exposing those three attributes (duck typing keeps this
            module independent of the agent implementation).
        design: the parsed :class:`pcbai.kicad.netlist.Design`, when the
            agent had a ``design_path``; ``None`` otherwise.

    Returns:
        A :class:`GroundingReport`; ``ok`` is ``False`` as soon as any of the
        three checks found an unsupported claim.

    Note:
        ``steps`` entries must expose ``name``, ``arguments`` and ``result``
        attributes (as the trace :class:`~pcbai.agent.react_agent.ToolCall`
        does); a malformed entry surfaces an ``AttributeError``.
    """
    text = _clean_answer(answer)
    unsupported: list[str] = []
    checks: list[str] = []

    # 1) REF_IN_ANSWER ----------------------------------------------------- #
    answer_refs = _extract_refs(text)
    known_refs: set[str] = set()
    if design is not None:
        known_refs |= set(design.components)
        for net_name in design.nets:
            known_refs |= _extract_refs(net_name)
    for step in steps:
        known_refs |= _refs_in_value(step.result)

    if answer_refs:
        unknown_refs = sorted(answer_refs - known_refs)
        checks.append(
            f"REF_IN_ANSWER: {len(answer_refs)} ref(s) found, "
            f"{len(unknown_refs)} not grounded in design/tool results"
        )
        for ref in unknown_refs:
            unsupported.append(
                f"REF_IN_ANSWER: reference {ref!r} does not exist in the loaded "
                "design or in any tool result"
            )
    else:
        checks.append("REF_IN_ANSWER: no reference designators found in the answer")

    # 2) NUMBERS_FROM_SIZING ------------------------------------------------ #
    sizing_results: list[float] = []
    for step in steps:
        if step.name == "size_resistor":
            sizing_results.extend(_numbers_in_value(step.result))

    claims = _value_claims(text)
    if sizing_results:
        unbacked = [
            (raw, value)
            for raw, value in claims
            if not any(
                math.isclose(value, expected, rel_tol=_REL_TOL) for expected in sizing_results
            )
        ]
        checks.append(
            f"NUMBERS_FROM_SIZING: {len(claims)} numeric claim(s) checked "
            f"against {len(sizing_results)} size_resistor value(s)"
        )
        for raw, value in unbacked:
            unsupported.append(
                f"NUMBERS_FROM_SIZING: value {raw!r} ({value:g} in base units) "
                "in the answer is not backed by any size_resistor result"
            )
    elif claims:
        # Such claims with no sizing evidence deserve a note, not a failure:
        # they may come from the BOM or the user's own question (v1 scope).
        checks.append(
            f"NUMBERS_FROM_SIZING: skipped (size_resistor not called, {len(claims)} claim(s) ignored)"
        )
    else:
        checks.append("NUMBERS_FROM_SIZING: skipped (size_resistor not called, no numeric claims)")

    # 3) NO_DATA_NO_CLAIM --------------------------------------------------- #
    if not steps:
        claims_factual = bool(answer_refs or _NUMBER_RE.findall(text))
        if claims_factual:
            unsupported.append(
                "NO_DATA_NO_CLAIM: the answer asserts references or numbers but "
                "no tool was invoked — nothing can ground it"
            )
            checks.append("NO_DATA_NO_CLAIM: FAILED (refs/numbers in an answer with no tool calls)")
        else:
            checks.append(
                "NO_DATA_NO_CLAIM: no tools called and the answer makes no "
                "factual ref/number assertion"
            )
    else:
        checks.append("NO_DATA_NO_CLAIM: skipped (at least one tool was invoked)")

    return GroundingReport(ok=not unsupported, unsupported=unsupported, checks=checks)
