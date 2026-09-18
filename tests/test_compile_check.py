"""Tests for the real-compiler compile-check (pure mocks + opt-in real gcc).

The pure tests never invoke a compiler: ``subprocess.run`` and
``shutil.which`` are patched, so they run on any machine (CI included) and
pin the exact compiler command lines. The ``TestRealCompilers`` tests are
skipped when the system compilers are missing and are selected in CI with
``pytest tests/test_compile_check.py -q -k real``.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from pcbai.firmware import FirmwareArtifact, FirmwareSpec, generate_firmware
from pcbai.firmware.compile_check import (
    STUB_FILENAMES,
    CompileReport,
    CompileResult,
    compile_check,
    resolve_stub_dir,
    write_stub_tree,
)
from tests.test_templates import VALID_PARAMS


class _FakeCompleted:
    """Minimal stand-in for a ``subprocess.CompletedProcess``."""

    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr


@dataclass(frozen=True)
class _FakeArtifact:
    """Artifact stand-in for command-line and error-path tests."""

    filename: str = "main.c"
    code: str = "int main(void) { return 0; }"
    target: str = "stm32-hal"
    template: str = "fake"


def _artifacts(templates: tuple[str, ...], target: str) -> list[FirmwareArtifact]:
    return [
        generate_firmware(
            FirmwareSpec(target=target, template=name, params=dict(VALID_PARAMS[name]))
        )
        for name in templates
    ]


def _patch_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    returncode: int = 0,
    stderr: str = "",
    raise_timeout: bool = False,
    which: object | None = None,
) -> list[dict[str, object]]:
    """Patch ``shutil.which``/``subprocess.run`` and record every command."""
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        shutil,
        "which",
        which if which is not None else (lambda name: f"/usr/bin/{name}"),
    )

    def fake_run(command: list[str], **kwargs: object) -> _FakeCompleted:
        calls.append({"command": command, "kwargs": kwargs})
        if raise_timeout:
            raise subprocess.TimeoutExpired(command, timeout=30)
        return _FakeCompleted(returncode, stderr)

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


# --------------------------------------------------------------------------- #
# Bundled stub tree
# --------------------------------------------------------------------------- #


class TestStubTree:
    def test_stub_dir_is_bundled_with_the_package(self) -> None:
        stub_dir = resolve_stub_dir()
        assert stub_dir.name == "stubs"
        assert stub_dir.parent.name == "firmware"
        assert stub_dir.is_dir()

    @pytest.mark.parametrize("name", STUB_FILENAMES)
    def test_every_stub_file_is_present(self, name: str) -> None:
        assert (resolve_stub_dir() / name).is_file()

    @pytest.mark.parametrize("name", STUB_FILENAMES)
    def test_stub_files_are_non_empty(self, name: str) -> None:
        assert (resolve_stub_dir() / name).read_text(encoding="utf-8").strip()

    def test_no_stray_header_files(self) -> None:
        bundled = {path.name for path in resolve_stub_dir().glob("*.h")}
        assert bundled == set(STUB_FILENAMES)

    def test_write_stub_tree_returns_the_written_paths(self, tmp_path: Path) -> None:
        written = write_stub_tree(tmp_path)
        assert {path.name for path in written} == set(STUB_FILENAMES)
        assert all(path.is_file() for path in written)

    def test_write_stub_tree_copies_identical_content(self, tmp_path: Path) -> None:
        written = write_stub_tree(tmp_path)
        for path in written:
            source = resolve_stub_dir() / path.name
            assert path.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")

    def test_write_stub_tree_creates_nested_directories(self, tmp_path: Path) -> None:
        target = tmp_path / "a" / "b"
        write_stub_tree(target)
        assert (target / "Arduino.h").is_file()


# --------------------------------------------------------------------------- #
# Compiler command lines (compiler never invoked)
# --------------------------------------------------------------------------- #


class TestCommandLines:
    def test_stm32_uses_c11_syntax_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _patch_runtime(monkeypatch)
        compile_check([_FakeArtifact()])
        command = calls[0]["command"]
        assert "-std=c11" in command
        assert "-fsyntax-only" in command
        assert "-Wall" in command
        assert "-Werror" in command
        assert ("-x", "c") in [tuple(command[i : i + 2]) for i in range(len(command) - 1)]

    def test_stm32_uses_gcc_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _patch_runtime(monkeypatch)
        compile_check([_FakeArtifact()])
        assert calls[0]["command"][0] == "gcc"

    def test_stm32_command_includes_the_stub_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _patch_runtime(monkeypatch)
        compile_check([_FakeArtifact()])
        assert any(
            part.startswith("-I") and part.endswith("/stubs") for part in calls[0]["command"]
        )

    def test_arduino_uses_cpp11_with_injected_arduino_core(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _patch_runtime(monkeypatch)
        compile_check([_FakeArtifact(filename="sketch.ino", target="arduino")])
        command = calls[0]["command"]
        assert command[0] == "g++"
        assert "-std=c++11" in command
        assert ("-include", "Arduino.h") in [
            tuple(command[i : i + 2]) for i in range(len(command) - 1)
        ]
        assert ("-x", "c++") in [tuple(command[i : i + 2]) for i in range(len(command) - 1)]

    def test_custom_compilers_are_respected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _patch_runtime(monkeypatch)
        compile_check(
            [_FakeArtifact(), _FakeArtifact(filename="sketch.ino", target="arduino")],
            cc="arm-none-eabi-gcc",
            cc_arduino="clang++",
        )
        commands = [call["command"][0] for call in calls]
        assert commands == ["arm-none-eabi-gcc", "clang++"]

    def test_source_filename_is_preserved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _patch_runtime(monkeypatch)
        compile_check([_FakeArtifact(), _FakeArtifact(filename="sketch.ino", target="arduino")])
        sources = [call["command"][-1] for call in calls]
        assert sources[0].endswith("main.c")
        assert sources[1].endswith("sketch.ino")

    def test_unknown_target_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_runtime(monkeypatch)
        with pytest.raises(ValueError, match="unsupported target"):
            compile_check([_FakeArtifact(target="avr")])


# --------------------------------------------------------------------------- #
# Report behaviour (compiler never invoked)
# --------------------------------------------------------------------------- #


class TestReport:
    def _artifacts(self) -> list[FirmwareArtifact]:
        return [
            *_artifacts(("stm32_gpio_output",), "stm32-hal"),
            *_artifacts(("arduino_gpio_output",), "arduino"),
        ]

    def test_everything_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_runtime(monkeypatch)
        report = compile_check(self._artifacts())
        assert isinstance(report, CompileReport)
        assert report.ok
        assert report.error is None
        assert len(report.results) == 2

    def test_failed_compilation_is_detected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_runtime(monkeypatch, returncode=1, stderr="error: expected ';'")
        report = compile_check(self._artifacts())
        assert not report.ok
        assert all(not result.ok for result in report.results)
        assert report.results[0].stderr == "error: expected ';'"

    def test_every_artifact_compiled_even_if_an_earlier_one_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[dict[str, object]] = []
        monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")

        def fake_run(command: list[str], **kwargs: object) -> _FakeCompleted:
            calls.append({"command": command})
            return _FakeCompleted(returncode=1 if len(calls) == 1 else 0, stderr="boom")

        monkeypatch.setattr(subprocess, "run", fake_run)
        report = compile_check(self._artifacts())
        assert len(calls) == 2
        assert report.results[0].ok is False
        assert report.results[1].ok is True

    def test_result_metadata(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_runtime(monkeypatch)
        results = compile_check(self._artifacts()).results
        assert results[0].filename == "main.c"
        assert results[0].target == "stm32-hal"
        assert results[0].template == "stm32_gpio_output"
        assert results[1].filename == "sketch.ino"
        assert results[1].target == "arduino"
        assert results[1].template == "arduino_gpio_output"

    def test_results_keep_input_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _patch_runtime(monkeypatch)
        report = compile_check(self._artifacts())
        sources = [Path(call["command"][-1]).name for call in calls]
        assert [result.template for result in report.results] == [
            "stm32_gpio_output",
            "arduino_gpio_output",
        ]
        assert sources == ["main.c", "sketch.ino"]

    def test_empty_input_is_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_runtime(monkeypatch)
        report = compile_check([])
        assert report.ok
        assert report.results == []

    def test_missing_compiler_sets_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _patch_runtime(monkeypatch, which=lambda name: None)
        report = compile_check(self._artifacts())
        assert not report.ok
        assert report.results == []
        assert report.error == "compiler(s) not found: g++, gcc"
        assert calls == []  # nothing was compiled

    def test_only_the_required_compiler_is_checked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_runtime(
            monkeypatch, which=lambda name: f"/usr/bin/{name}" if name != "g++" else None
        )
        report = compile_check(_artifacts(("arduino_gpio_output",), "arduino"))
        assert "g++" in report.error
        assert "gcc" not in report.error

    def test_arduino_only_run_does_not_require_gcc(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_runtime(
            monkeypatch, which=lambda name: f"/usr/bin/{name}" if name != "gcc" else None
        )
        report = compile_check(_artifacts(("arduino_gpio_output",), "arduino"))
        assert report.ok

    def test_timeout_is_reported_as_a_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_runtime(monkeypatch, raise_timeout=True)
        report = compile_check(self._artifacts())
        assert not report.ok
        assert all(not result.ok for result in report.results)
        assert "timed out" in report.results[0].stderr

    def test_source_file_exists_when_the_compiler_runs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        artifacts = self._artifacts()

        def checked_run(command: list[str], **kwargs: object) -> _FakeCompleted:
            source = Path(command[-1])
            assert source.is_file()
            assert source.read_text(encoding="utf-8") in {artifact.code for artifact in artifacts}
            return _FakeCompleted()

        monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(subprocess, "run", checked_run)
        report = compile_check(artifacts)
        assert report.ok

    def test_temporary_directory_is_removed_afterwards(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sources: list[Path] = []

        def recording_run(command: list[str], **kwargs: object) -> _FakeCompleted:
            sources.append(Path(command[-1]))
            return _FakeCompleted()

        monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(subprocess, "run", recording_run)
        compile_check(self._artifacts())
        assert sources
        assert not any(source.exists() for source in sources)

    def test_each_artifact_gets_its_own_work_directory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[dict[str, object]] = []
        monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")

        def fake_run(command: list[str], **kwargs: object) -> _FakeCompleted:
            calls.append({"command": command})
            return _FakeCompleted()

        monkeypatch.setattr(subprocess, "run", fake_run)
        compile_check(
            [
                _FakeArtifact(),
                _FakeArtifact(code="int second(void) { return 0; }"),
            ]
        )
        parents = [Path(call["command"][-1]).parent for call in calls]
        assert len({str(parent) for parent in parents}) == 2


# --------------------------------------------------------------------------- #
# Real compiler round-trips (opt-in via -k real; skipped without a toolchain)
# --------------------------------------------------------------------------- #

gcc_skip = pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not installed")
gpp_skip = pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not installed")


class TestRealCompilers:
    @gcc_skip
    def test_real_stub_headers_compile_standalone_c(self) -> None:
        artifact = FirmwareArtifact(
            filename="probe.c",
            target="stm32-hal",
            template="probe",
            params={},
            code=(
                '#include "main.h"\n'
                "int probe(void)\n"
                "{\n"
                "    __HAL_RCC_GPIOA_CLK_ENABLE();\n"
                "    HAL_GPIO_WritePin(GPIOA, GPIO_PIN_5, GPIO_PIN_SET);\n"
                "    return 0;\n"
                "}\n"
            ),
        )
        report = compile_check([artifact])
        assert report.ok, report.results[0].stderr

    @gcc_skip
    @pytest.mark.parametrize(
        "template",
        ["stm32_gpio_output", "stm32_pwm_timer", "stm32_adc_poll", "stm32_uart_init"],
    )
    def test_real_stm32_artifacts_compile(self, template: str) -> None:
        report = compile_check(_artifacts((template,), "stm32-hal"))
        assert report.results[0].ok, report.results[0].stderr

    @gpp_skip
    @pytest.mark.parametrize(
        "template",
        ["arduino_gpio_output", "arduino_pwm_analogwrite", "arduino_adc_read", "arduino_i2c_scan"],
    )
    def test_real_arduino_artifacts_compile(self, template: str) -> None:
        report = compile_check(_artifacts((template,), "arduino"))
        assert report.results[0].ok, report.results[0].stderr

    @gcc_skip
    def test_real_broken_code_is_rejected(self) -> None:
        broken = FirmwareArtifact(
            filename="broken.c",
            target="stm32-hal",
            template="broken",
            params={},
            code="int broken(void) { return 0;\n",  # missing closing brace
        )
        report = compile_check([broken])
        assert not report.ok
        assert report.results[0].ok is False
        assert "error" in report.results[0].stderr.lower()

    @pytest.mark.skipif(
        shutil.which("gcc") is None or shutil.which("g++") is None,
        reason="gcc and g++ required",
    )
    def test_real_whole_catalog_round_trip(self) -> None:
        artifacts = [
            *_artifacts(
                ("stm32_gpio_output", "stm32_pwm_timer", "stm32_adc_poll", "stm32_uart_init"),
                "stm32-hal",
            ),
            *_artifacts(
                (
                    "arduino_gpio_output",
                    "arduino_pwm_analogwrite",
                    "arduino_adc_read",
                    "arduino_i2c_scan",
                ),
                "arduino",
            ),
        ]
        report = compile_check(artifacts)
        assert report.cc == "gcc"
        assert len(report.results) == 8
        assert all(result.ok for result in report.results), [
            result.stderr for result in report.results if not result.ok
        ]

    def test_result_type_is_compile_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_runtime(monkeypatch)
        result = compile_check([_FakeArtifact()]).results[0]
        assert isinstance(result, CompileResult)
