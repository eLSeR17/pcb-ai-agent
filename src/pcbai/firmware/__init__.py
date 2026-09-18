"""Firmware generation layer (Layer 2, deterministic write path).

The LLM is only allowed to pick a template and parameters;
this layer renders the chosen fixed template and statically validates the
result, so no language model ever writes firmware code here. The project
invariant: the LLM decides, the code generates.

Public API:

- :func:`list_templates` / :func:`get_template` — the deterministic
  template catalog (STM32 HAL + Arduino).
- :func:`generate_firmware` — render a :class:`FirmwareSpec` into a
  :class:`FirmwareArtifact`, grounded against a KiCad netlist when
  ``design_path`` is given. Always ``requires_human_review=True``.
- :func:`validate_firmware` — deterministic, non-compiling static checks
  over a generated artifact.
- :func:`compile_check` — real-compiler syntax check (``gcc``/``g++``)
  over generated artifacts against the bundled 100 %-own HAL/Arduino
  stubs; opt-in, outside the static validator.
"""

from pcbai.firmware.compile_check import (
    CompileReport,
    CompileResult,
    compile_check,
    resolve_stub_dir,
    write_stub_tree,
)
from pcbai.firmware.generator import (
    FirmwareArtifact,
    FirmwareSpec,
    GroundingError,
    TargetMismatchError,
    generate_firmware,
)
from pcbai.firmware.templates import (
    CATEGORIES,
    TARGETS,
    TEMPLATES,
    Template,
    TemplateError,
    TemplateParamError,
    UnknownTemplateError,
    get_template,
    list_templates,
)
from pcbai.firmware.validator import (
    KNOWN_HAL_FUNCTIONS,
    KNOWN_HAL_MACROS,
    ValidationIssue,
    ValidationReport,
    validate_firmware,
)

__all__ = [
    "CATEGORIES",
    "KNOWN_HAL_FUNCTIONS",
    "KNOWN_HAL_MACROS",
    "TARGETS",
    "TEMPLATES",
    "CompileReport",
    "CompileResult",
    "FirmwareArtifact",
    "FirmwareSpec",
    "GroundingError",
    "TargetMismatchError",
    "Template",
    "TemplateError",
    "TemplateParamError",
    "UnknownTemplateError",
    "ValidationIssue",
    "ValidationReport",
    "compile_check",
    "generate_firmware",
    "get_template",
    "list_templates",
    "resolve_stub_dir",
    "validate_firmware",
    "write_stub_tree",
]
