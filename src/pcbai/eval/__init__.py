"""Eval harness for pcb-ai-agent: golden dataset, judges, runner.

Public API:

- :mod:`pcbai.eval.golden` — the golden dataset models (:class:`SizingCase`,
  :class:`AuditCase`, :class:`GoldenSet`) and the JSONL loaders.
- :mod:`pcbai.eval.judge` — the deterministic :class:`HeuristicJudge`
  (CI-safe) and the opt-in :class:`LLMJudge` (local Ollama, not CI).
- :mod:`pcbai.eval.runner` — :func:`run_evals`, :func:`check_regression`
  and :func:`write_baseline` (the regression guard).

The split mirrors the sibling projects (evalforge, smart-contract-rag):
*deterministic, reproducible evals* run anywhere with zero network (CI), and
an *optional LLM-as-judge* layer provides qualitative feedback on demand —
never a CI dependency.
"""

from pcbai.eval.golden import (
    SIZING_FUNCTIONS,
    AuditCase,
    GoldenDataError,
    GoldenSet,
    SizingCase,
    load_audit_cases,
    load_golden,
    load_sizing_cases,
)
from pcbai.eval.judge import HeuristicJudge, JudgeVerdict, LLMJudge
from pcbai.eval.runner import (
    REGRESSION_TOLERANCE,
    EvalReport,
    EvalResult,
    RegressionVerdict,
    check_regression,
    run_evals,
    write_baseline,
)

__all__ = [
    "AuditCase",
    "EvalReport",
    "EvalResult",
    "GoldenDataError",
    "GoldenSet",
    "HeuristicJudge",
    "JudgeVerdict",
    "LLMJudge",
    "REGRESSION_TOLERANCE",
    "RegressionVerdict",
    "SIZING_FUNCTIONS",
    "SizingCase",
    "check_regression",
    "load_audit_cases",
    "load_golden",
    "load_sizing_cases",
    "run_evals",
    "write_baseline",
]
