from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from fluid_bridge import CommandResult, FluidAudioBridge, FluidAudioBridgeError, FluidAudioCLIConfig
from fluid_bridge.cli import main as cli_main


def _runner(
    calls: list[list[str]],
    *,
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
):
    def run(
        command: Sequence[str],
        env: Mapping[str, str],
        cwd: Path | None,
        timeout_s: float | None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(command))
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)

    return run


def test_explicit_command_takes_precedence() -> None:
    calls: list[list[str]] = []
    bridge = FluidAudioBridge(
        FluidAudioCLIConfig(command=("fluid-custom", "--quiet")),
        runner=_runner(calls, stdout="hello\n"),
    )

    result = bridge.transcribe("audio.wav", model_version="v2")

    assert result.stdout == "hello\n"
    assert calls == [["fluid-custom", "--quiet", "transcribe", "audio.wav", "--model-version", "v2"]]


def test_env_command_is_split(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setenv("FLUID_BRIDGE_CLI", "/opt/bin/fluidaudiocli --json")
    bridge = FluidAudioBridge(runner=_runner(calls))

    bridge.vad_analyze("audio.wav", streaming=True, threshold=0.65)

    assert calls == [
        ["/opt/bin/fluidaudiocli", "--json", "vad-analyze", "audio.wav", "--streaming", "--threshold", "0.65"]
    ]


def test_package_path_is_last_discovery_option(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.delenv("FLUID_BRIDGE_CLI", raising=False)
    monkeypatch.setenv("FLUID_AUDIO_PACKAGE", "/src/FluidAudio")
    monkeypatch.setattr("fluid_bridge.bridge.shutil.which", lambda name: None)
    bridge = FluidAudioBridge(runner=_runner(calls))

    bridge.tts("Hello", "out.wav", backend="kokoro-ane")

    assert calls == [
        [
            "swift",
            "run",
            "--package-path",
            "/src/FluidAudio",
            "fluidaudiocli",
            "tts",
            "Hello",
            "--output",
            "out.wav",
            "--backend",
            "kokoro-ane",
        ]
    ]


def test_missing_cli_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLUID_BRIDGE_CLI", raising=False)
    monkeypatch.delenv("FLUID_AUDIO_PACKAGE", raising=False)
    monkeypatch.setattr("fluid_bridge.bridge.shutil.which", lambda name: None)
    bridge = FluidAudioBridge()

    with pytest.raises(FluidAudioBridgeError, match="FluidAudio CLI not found"):
        bridge.transcribe("audio.wav")


def test_diarize_parses_output_json(tmp_path: Path) -> None:
    output = tmp_path / "result.json"
    payload = {"segments": [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]}
    calls: list[list[str]] = []

    def run(
        command: Sequence[str],
        env: Mapping[str, str],
        cwd: Path | None,
        timeout_s: float | None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(command))
        output.write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    bridge = FluidAudioBridge(FluidAudioCLIConfig(command=("fluidaudiocli",)), runner=run)

    result = bridge.diarize("meeting.wav", mode="offline", threshold=0.6, output_path=output)

    assert result.parsed_json == payload
    assert calls == [
        [
            "fluidaudiocli",
            "process",
            "meeting.wav",
            "--output",
            str(output),
            "--mode",
            "offline",
            "--threshold",
            "0.6",
        ]
    ]


def test_failed_command_preserves_stderr() -> None:
    bridge = FluidAudioBridge(
        FluidAudioCLIConfig(command=("fluidaudiocli",)),
        runner=_runner([], stderr="model missing\n", returncode=2),
    )

    result = bridge.transcribe("audio.wav")

    assert result.returncode == 2
    assert result.stderr == "model missing\n"
    with pytest.raises(FluidAudioBridgeError, match="model missing"):
        result.raise_for_error()


def test_failed_diarize_does_not_parse_non_json_stdout() -> None:
    bridge = FluidAudioBridge(
        FluidAudioCLIConfig(command=("fluidaudiocli",)),
        runner=_runner([], stdout="not json\n", stderr="bad input\n", returncode=2),
    )

    result = bridge.diarize("audio.wav")

    assert result.returncode == 2
    assert result.stdout == "not json\n"
    assert result.stderr == "bad input\n"
    assert result.parsed_json is None


def test_cli_diarize_failure_preserves_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class FakeBridge:
        def diarize(self, *args: object, **kwargs: object) -> CommandResult:
            return CommandResult(
                command=("fluidaudiocli", "process"),
                returncode=2,
                stdout="not json\n",
                stderr="bad input\n",
            )

    monkeypatch.setattr("fluid_bridge.cli.FluidAudioBridge", lambda: FakeBridge())

    code = cli_main(["diarize", "audio.wav"])

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == "not json\n"
    assert captured.err == "bad input\n"
