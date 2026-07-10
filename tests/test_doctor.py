from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from fluid_bridge import DoctorFinding, DoctorReport, FluidAudioBridge, FluidAudioCLIConfig
from fluid_bridge.cli import main as cli_main


def _patch_environment(
    monkeypatch: pytest.MonkeyPatch,
    *,
    system: str = "Darwin",
    developer_dir: str | None = "/Applications/Xcode.app/Contents/Developer",
    executables: Mapping[str, str] | None = None,
) -> None:
    paths = dict(executables or {})
    monkeypatch.setattr("fluid_bridge.bridge.platform.system", lambda: system)
    monkeypatch.setattr("fluid_bridge.bridge.platform.platform", lambda: f"{system}-test")
    monkeypatch.setattr("fluid_bridge.bridge.shutil.which", lambda name: paths.get(name))
    monkeypatch.setattr(
        "fluid_bridge.bridge.FluidAudioBridge._swift_version",
        staticmethod(lambda path: None),
    )
    monkeypatch.setattr(
        "fluid_bridge.bridge.FluidAudioBridge._developer_dir",
        staticmethod(lambda: developer_dir),
    )


def test_doctor_probe_reports_ready_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_environment(
        monkeypatch,
        executables={"swift": "/usr/bin/swift", "fluidaudiocli": "/opt/bin/fluidaudiocli"},
    )
    calls: list[list[str]] = []

    def run(
        command: Sequence[str],
        env: Mapping[str, str],
        cwd: Path | None,
        timeout_s: float | None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(command))
        return subprocess.CompletedProcess(command, 0, "FluidAudio CLI\n", "")

    report = FluidAudioBridge(runner=run).doctor(probe_cli=True)

    assert calls == [["/opt/bin/fluidaudiocli", "--help"]]
    assert report.ready is True
    assert report.probe_attempted is True
    assert report.probe_stdout == "FluidAudio CLI\n"
    assert report.findings == ()


def test_doctor_reports_missing_cli_and_wrong_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_environment(monkeypatch, system="Linux", developer_dir=None)
    monkeypatch.delenv("FLUID_BRIDGE_CLI", raising=False)
    monkeypatch.delenv("FLUID_AUDIO_PACKAGE", raising=False)

    report = FluidAudioBridge().doctor(probe_cli=True)

    assert report.ready is False
    assert report.probe_attempted is False
    assert {finding.code for finding in report.findings} == {
        "macos_required",
        "cli_not_configured",
    }


def test_doctor_recognizes_swift_sdk_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_environment(
        monkeypatch,
        developer_dir="/Library/Developer/CommandLineTools",
        executables={"swift": "/usr/bin/swift"},
    )
    stderr = (
        "module 'CoreAudioTypes' was compiled with Swift 6.3 and cannot be imported "
        "by the Swift 6.3.1 compiler\n"
    )
    bridge = FluidAudioBridge(
        FluidAudioCLIConfig(package_path="/src/FluidAudio"),
        runner=lambda command, env, cwd, timeout: subprocess.CompletedProcess(
            command, 1, "", stderr
        ),
    )

    report = bridge.doctor(probe_cli=True)

    assert report.ready is False
    assert report.probe_command == (
        "swift",
        "run",
        "--package-path",
        "/src/FluidAudio",
        "fluidaudiocli",
        "--help",
    )
    assert report.probe_stderr == stderr
    assert {finding.code for finding in report.findings} == {
        "command_line_tools_selected",
        "swift_toolchain_mismatch",
    }


def test_cli_doctor_probe_returns_nonzero_for_unready_report(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    report = DoctorReport(
        platform="Darwin-test",
        is_macos=True,
        swift_path=None,
        swift_version=None,
        fluidaudio_cli_path=None,
        fluid_bridge_cli_env=None,
        fluid_audio_package_env=None,
        configured_command=None,
        resolved_command=None,
        model_cache_note="models",
        findings=(
            DoctorFinding(
                code="cli_not_configured",
                severity="error",
                message="missing",
                action="configure it",
            ),
        ),
    )

    class FakeBridge:
        def doctor(self, *, probe_cli: bool) -> DoctorReport:
            assert probe_cli is True
            return report

    monkeypatch.setattr("fluid_bridge.cli.FluidAudioBridge", lambda: FakeBridge())

    assert cli_main(["doctor", "--probe"]) == 1
    assert '"ready": false' in capsys.readouterr().out
