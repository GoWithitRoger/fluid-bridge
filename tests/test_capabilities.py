from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from fluid_bridge import (
    CapabilityReport,
    CommandCapability,
    DeepCapabilityReport,
    FluidAudioBridge,
    FluidAudioCLIConfig,
)
from fluid_bridge.capabilities import UNSAFE_HELP_COMMANDS, UPSTREAM_COMMANDS
from fluid_bridge.cli import main as cli_main


def test_deep_capabilities_probes_baseline_and_additional_commands() -> None:
    calls: list[list[str]] = []

    def run(
        command: Sequence[str],
        env: Mapping[str, str],
        cwd: Path | None,
        timeout_s: float | None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(command))
        args = list(command[1:])
        if args == ["--help"]:
            stdout = """\
Commands:
    process                 Process audio
    future-command          Future work

Run 'fluidaudio <command> --help' for command-specific options.
"""
            return subprocess.CompletedProcess(command, 0, stdout, "")
        if args == ["vad-benchmark", "--help"]:
            return subprocess.CompletedProcess(command, 2, "", "benchmark unavailable")
        return subprocess.CompletedProcess(
            command,
            0,
            f"Usage: {args[0]} [--input PATH] [--output-json PATH] [--input PATH]\n",
            "",
        )

    bridge = FluidAudioBridge(
        FluidAudioCLIConfig(command=("fluidaudiocli",)),
        runner=run,
    )

    report = bridge.deep_capabilities(include_additional=True)

    assert tuple(report.commands) == (*UPSTREAM_COMMANDS, "future-command")
    assert len(calls) == len(UPSTREAM_COMMANDS) + 2 - len(UNSAFE_HELP_COMMANDS)
    assert report.commands["transcribe"].options == ("--input", "--output-json")
    assert report.commands["future-command"].baseline is False
    assert report.commands["vad-benchmark"].stderr == "benchmark unavailable"
    assert report.failed_baseline_commands == ("vad-benchmark",)
    assert report.failed_additional_commands == ()
    assert report.skipped_commands == tuple(UNSAFE_HELP_COMMANDS)
    assert report.commands["download"].skipped is True
    assert report.probe_ok is False


def test_deep_capabilities_records_probe_exceptions_and_continues() -> None:
    calls: list[list[str]] = []

    def run(
        command: Sequence[str],
        env: Mapping[str, str],
        cwd: Path | None,
        timeout_s: float | None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(command))
        args = list(command[1:])
        if args == ["--help"]:
            return subprocess.CompletedProcess(
                command,
                0,
                "Commands:\n    process                 Process audio\n\nExamples:\n",
                "",
            )
        if args == [UPSTREAM_COMMANDS[0], "--help"]:
            raise subprocess.TimeoutExpired(command, 1)
        return subprocess.CompletedProcess(command, 0, "Usage: command [--verbose]\n", "")

    bridge = FluidAudioBridge(
        FluidAudioCLIConfig(command=("fluidaudiocli",)),
        runner=run,
    )

    report = bridge.deep_capabilities(include_additional=False)

    failed = report.commands[UPSTREAM_COMMANDS[0]]
    assert failed.returncode is None
    assert "timed out" in (failed.error or "")
    assert report.commands["g2p-benchmark"].probe_ok is True
    assert len(calls) == len(UPSTREAM_COMMANDS) + 1 - len(UNSAFE_HELP_COMMANDS)


def test_deep_capabilities_does_not_probe_additional_commands_by_default() -> None:
    calls: list[list[str]] = []

    def run(
        command: Sequence[str],
        env: Mapping[str, str],
        cwd: Path | None,
        timeout_s: float | None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(command))
        args = list(command[1:])
        if args == ["--help"]:
            return subprocess.CompletedProcess(
                command,
                0,
                "Commands:\n    future-command          Unknown behavior\n\nExamples:\n",
                "",
            )
        assert args[0] != "future-command"
        return subprocess.CompletedProcess(command, 0, "Usage: command [--verbose]\n", "")

    bridge = FluidAudioBridge(
        FluidAudioCLIConfig(command=("fluidaudiocli",)),
        runner=run,
    )

    report = bridge.deep_capabilities()

    assert "future-command" not in report.commands
    assert all(call[1:2] != ["future-command"] for call in calls)


def test_cli_deep_capabilities_prints_machine_readable_report(
    monkeypatch, capsys
) -> None:
    root = CapabilityReport(
        baseline_commit="baseline-sha",
        baseline_commands=("process",),
        advertised_commands=("process",),
        baseline_not_advertised=(),
        additional_commands=(),
        probe_returncode=0,
    )
    report = DeepCapabilityReport(
        root=root,
        commands={
            "process": CommandCapability.from_probe(
                "process",
                baseline=True,
                stdout="Usage: process [--output PATH]\n",
                stderr="",
                returncode=0,
            )
        },
    )

    class FakeBridge:
        def deep_capabilities(self, *, include_additional: bool) -> DeepCapabilityReport:
            assert include_additional is False
            return report

    monkeypatch.setattr("fluid_bridge.cli.FluidAudioBridge", lambda: FakeBridge())

    code = cli_main(["capabilities", "--deep"])

    assert code == 0
    assert json.loads(capsys.readouterr().out) == report.to_dict()
