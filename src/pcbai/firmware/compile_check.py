"""Real-compiler syntax check over generated firmware artifacts.

The static validator (``pcbai.firmware.validator``) is deliberately
non-compiling: it balances braces and checks includes against a whitelist,
but it is not a C/C++ parser. ``compile_check`` closes that gap with the
system compiler — no vendor SDK needed:

- The generated code is written to a throwaway temporary directory.
- The bundled stubs (``stubs/``, 100 % own minimal headers) provide the
  HAL/Arduino API surface the 8 templates reference, so compilation does
  not require vendoring real vendor toolchains (STM32 HAL is huge and
  hardware-specific; the Arduino core is C++ toolchain glue).
- STM32 HAL code is compiled as C11 and Arduino sketches as C++11 with
  ``-include Arduino.h`` (mirroring how the real Arduino toolchain
  injects its core). Both run ``gcc``/``g++`` with ``-Wall -Werror
  -fsyntax-only``: a syntax check only, never a link, never an object.
- The target STM32 compiler is a separate ``cc`` vs ``cc_arduino`` pair
  (default ``gcc``/``g++``), so an embedded project can later point
  ``cc`` at ``arm-none-eabi-gcc`` without touching the Arduino path.

The check is deterministic and side-effect free: it writes only inside a
``tempfile.TemporaryDirectory`` that is removed before returning, and it
never modifies the artifact. If a required compiler is not installed the
report carries a descriptive ``error`` instead of a partial result set.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from pcbai.firmware.generator import FirmwareArtifact

__all__ = [
    "STUB_FILENAMES",
    "CompileReport",
    "CompileResult",
    "compile_check",
    "resolve_stub_dir",
    "write_stub_tree",
]

#: Bundled stub headers shipped with the package (package-data in
#: ``pyproject.toml``). :func:`write_stub_tree` copies exactly these files.
STUB_FILENAMES: tuple[str, ...] = (
    "Arduino.h",
    "Wire.h",
    "main.h",
    "stm32f1xx_hal.h",
    "stm32f4xx_hal.h",
)


@dataclass(frozen=True)
class CompileResult:
    """Outcome of compiling one artifact.

    Attributes:
        filename: The artifact's output filename (``main.c``/``sketch.ino``).
        target: Firmware target (``"stm32-hal"``/``"arduino"``).
        template: Template name that produced the code.
        ok: ``True`` when the compiler accepted the file (exit code 0).
        stderr: Stripped compiler stderr — the actionable error when ``ok``
            is ``False``.
    """

    filename: str
    target: str
    template: str
    ok: bool
    stderr: str = ""


@dataclass(frozen=True)
class CompileReport:
    """The result of :func:`compile_check`.

    Attributes:
        results: One :class:`CompileResult` per artifact, in input order.
        cc: The C (STM32) compiler actually invoked (``gcc`` by default).
        error: When a required compiler is missing, a human message; the
            results list is then empty (a partial report would be noise).
    """

    results: list[CompileResult]
    cc: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        """True when every artifact compiled and no compiler was missing."""
        return self.error is None and all(result.ok for result in self.results)


def resolve_stub_dir() -> Path:
    """Absolute path of the bundled stubs directory shipped with the package."""
    return Path(__file__).resolve().parent / "stubs"


def write_stub_tree(dest_dir: str | Path) -> list[Path]:
    """Copy every bundled stub header into ``dest_dir`` (created if needed).

    Returns the written file paths. The stub headers are the compile-check
    contract: they must always travel with the package, so they are copied
    rather than referenced in place (the caller may run from a read-only or
    relocated install).
    """
    source_dir = resolve_stub_dir()
    destination = Path(dest_dir)
    destination.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name in STUB_FILENAMES:
        target = destination / name
        target.write_text((source_dir / name).read_text(encoding="utf-8"), encoding="utf-8")
        written.append(target)
    return written


def compile_check(
    artifacts: Iterable[FirmwareArtifact],
    *,
    cc: str = "gcc",
    cc_arduino: str = "g++",
    timeout: float = 30.0,
) -> CompileReport:
    """Syntax-check generated firmware artifacts with the system compilers.

    Each artifact is written to its own temporary directory together with a
    copy of the bundled stubs and compiled with ``-Wall -Werror
    -fsyntax-only`` — C11 for ``stm32-hal`` (``cc``), C++11 with
    ``-include Arduino.h`` for ``arduino`` (``cc_arduino``). Every artifact
    is compiled even when an earlier one failed, so a batch report lists
    every breakage in one run.

    Raises:
        ValueError: an artifact targets something other than ``"stm32-hal"``
            or ``"arduino"`` (the generator never produces such artifacts).
    """
    artifact_list = list(artifacts)
    missing = sorted(
        compiler
        for compiler in _required_compilers(artifact_list, cc, cc_arduino)
        if shutil.which(compiler) is None
    )
    if missing:
        return CompileReport(
            results=[], cc=cc, error=f"compiler(s) not found: {', '.join(missing)}"
        )

    results: list[CompileResult] = []
    with tempfile.TemporaryDirectory(prefix="pcbai-compile-") as tmp:
        work_root = Path(tmp)
        stub_dir = work_root / "stubs"
        write_stub_tree(stub_dir)
        for index, artifact in enumerate(artifact_list):
            work_dir = work_root / str(index)
            work_dir.mkdir(parents=True, exist_ok=True)
            source = work_dir / artifact.filename
            source.write_text(artifact.code, encoding="utf-8")
            command = _command(artifact.target, source, stub_dir, cc, cc_arduino)
            ok = False
            stderr = ""
            try:
                completed = subprocess.run(
                    command, capture_output=True, text=True, timeout=timeout, check=False
                )
                ok = completed.returncode == 0
                stderr = (completed.stderr or "").strip()
            except subprocess.TimeoutExpired:
                stderr = f"compilation timed out after {timeout:g}s"
            results.append(
                CompileResult(
                    filename=artifact.filename,
                    target=artifact.target,
                    template=artifact.template,
                    ok=ok,
                    stderr=stderr,
                )
            )
    return CompileReport(results=results, cc=cc, error=None)


def _required_compilers(artifacts: list[FirmwareArtifact], cc: str, cc_arduino: str) -> set[str]:
    """The subset of compilers actually needed by the artifacts' targets."""
    targets = {artifact.target for artifact in artifacts}
    required: set[str] = set()
    if "stm32-hal" in targets:
        required.add(cc)
    if "arduino" in targets:
        required.add(cc_arduino)
    return required


def _command(target: str, source: Path, stub_dir: Path, cc: str, cc_arduino: str) -> list[str]:
    """Build the compiler command line for one artifact."""
    include = f"-I{stub_dir}"
    if target == "stm32-hal":
        return [
            cc,
            "-std=c11",
            "-fsyntax-only",
            "-Wall",
            "-Werror",
            "-x",
            "c",
            include,
            str(source),
        ]
    if target == "arduino":
        return [
            cc_arduino,
            "-std=c++11",
            "-fsyntax-only",
            "-Wall",
            "-Werror",
            "-x",
            "c++",
            "-include",
            "Arduino.h",
            include,
            str(source),
        ]
    raise ValueError(f"unsupported target {target!r} (supported: 'stm32-hal', 'arduino')")
