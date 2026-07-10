from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
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


def test_run_live_uses_configured_invocation_context(tmp_path: Path) -> None:
    calls: list[tuple[list[str], Mapping[str, str], Path | None, float | None]] = []

    def live_runner(
        command: Sequence[str],
        env: Mapping[str, str],
        cwd: Path | None,
        timeout_s: float | None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((list(command), env, cwd, timeout_s))
        return subprocess.CompletedProcess(command, 19, None, None)

    bridge = FluidAudioBridge(
        FluidAudioCLIConfig(
            command=("fluid-custom", "--quiet"),
            env={"FLUID_TEST_MODE": "live"},
            cwd=tmp_path,
            timeout_s=12.5,
        ),
        live_runner=live_runner,
    )

    result = bridge.run_live(["future-command", "--new-option", "value"])

    assert len(calls) == 1
    command, env, cwd, timeout_s = calls[0]
    assert command == [
        "fluid-custom",
        "--quiet",
        "future-command",
        "--new-option",
        "value",
    ]
    assert env["FLUID_TEST_MODE"] == "live"
    assert cwd == tmp_path
    assert timeout_s == 12.5
    assert result.returncode == 19
    assert result.stdout == ""
    assert result.stderr == ""


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


def test_cli_raw_forwards_arguments_and_preserves_process_result(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[list[str]] = []

    class FakeBridge:
        def run(self, args: Sequence[str]) -> CommandResult:
            calls.append(list(args))
            return CommandResult(
                command=("fluidaudiocli", *args),
                returncode=7,
                stdout="partial output",
                stderr="upstream failure",
            )

    monkeypatch.setattr("fluid_bridge.cli.FluidAudioBridge", lambda: FakeBridge())

    code = cli_main(
        [
            "raw",
            "--",
            "nemotron-transcribe",
            "--input",
            "audio.wav",
            "--chunk-ms",
            "160",
        ]
    )

    captured = capsys.readouterr()
    assert calls == [
        ["nemotron-transcribe", "--input", "audio.wav", "--chunk-ms", "160"]
    ]
    assert captured.out == "partial output"
    assert captured.err == "upstream failure"
    assert code == 7


def test_cli_raw_live_inherits_stdio_and_preserves_exit_status(tmp_path: Path) -> None:
    fake_cli = tmp_path / "fake_fluidaudio.py"
    fake_cli.write_text(
        """\
import sys

payload = sys.stdin.read()
sys.stdout.write(f"stdout:{payload}|args:{sys.argv[1:]}")
sys.stderr.write("stderr:live")
raise SystemExit(23)
""",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["FLUID_BRIDGE_CLI"] = shlex.join([sys.executable, str(fake_cli)])

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "fluid_bridge.cli",
            "raw",
            "--live",
            "--",
            "future-command",
            "--new-option",
            "value",
        ],
        input="microphone-input",
        capture_output=True,
        check=False,
        cwd=Path(__file__).parents[1],
        env=env,
        text=True,
    )

    assert proc.stdout == (
        "stdout:microphone-input|args:['future-command', '--new-option', 'value']"
    )
    assert proc.stderr == "stderr:live"
    assert proc.returncode == 23


@pytest.mark.parametrize("child_signal", [signal.SIGTERM, signal.SIGKILL])
def test_cli_raw_live_mirrors_child_signal(tmp_path: Path, child_signal: signal.Signals) -> None:
    fake_cli = tmp_path / "signaled_fluidaudio.py"
    fake_cli.write_text(
        f"""\
import os
import signal

os.kill(os.getpid(), signal.Signals({int(child_signal)}))
""",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["FLUID_BRIDGE_CLI"] = shlex.join([sys.executable, str(fake_cli)])

    proc = subprocess.run(
        [sys.executable, "-m", "fluid_bridge.cli", "raw", "--live", "--", "stream"],
        capture_output=True,
        check=False,
        cwd=Path(__file__).parents[1],
        env=env,
        text=True,
    )

    assert proc.stdout == ""
    assert proc.stderr == ""
    assert proc.returncode == -child_signal


def test_cli_raw_live_handles_terminal_interrupt_without_traceback(tmp_path: Path) -> None:
    fake_cli = tmp_path / "streaming_fluidaudio.py"
    fake_cli.write_text(
        """\
import os
import signal
import time

def stop(*_args):
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    os.kill(os.getpid(), signal.SIGINT)

signal.signal(signal.SIGINT, stop)
print("ready", flush=True)
time.sleep(30)
""",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["FLUID_BRIDGE_CLI"] = shlex.join([sys.executable, str(fake_cli)])
    proc = subprocess.Popen(
        [sys.executable, "-m", "fluid_bridge.cli", "raw", "--live", "--", "stream"],
        cwd=Path(__file__).parents[1],
        env=env,
        start_new_session=True,
        stderr=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )

    assert proc.stdout is not None
    assert proc.stdout.readline() == "ready\n"
    os.killpg(proc.pid, signal.SIGINT)
    remaining_stdout, stderr = proc.communicate(timeout=5)

    assert remaining_stdout == ""
    assert stderr == ""
    assert proc.returncode == -signal.SIGINT
