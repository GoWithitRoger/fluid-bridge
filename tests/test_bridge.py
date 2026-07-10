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

from fluid_bridge import (
    CapabilityReport,
    CommandResult,
    FluidAudioBridge,
    FluidAudioBridgeError,
    FluidAudioCLIConfig,
)
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


def test_transcribe_parses_named_json_artifact(tmp_path: Path) -> None:
    output = tmp_path / "transcript.json"
    payload = {"text": "hello", "wordTimings": []}
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

    bridge = FluidAudioBridge(
        FluidAudioCLIConfig(command=("fluidaudiocli",)),
        runner=run,
    )

    result = bridge.transcribe(
        "audio.wav",
        streaming=True,
        language="en",
        output_json=output,
    )

    assert calls == [
        [
            "fluidaudiocli",
            "transcribe",
            "audio.wav",
            "--streaming",
            "--language",
            "en",
            "--output-json",
            str(output),
        ]
    ]
    assert result.parsed_json == payload
    assert result.parse_error is None
    assert result.output_path == output
    assert result.artifacts == {"transcript": output}


def test_transcribe_preserves_raw_output_when_json_artifact_is_malformed(
    tmp_path: Path,
) -> None:
    output = tmp_path / "transcript.json"

    def run(
        command: Sequence[str],
        env: Mapping[str, str],
        cwd: Path | None,
        timeout_s: float | None,
    ) -> subprocess.CompletedProcess[str]:
        output.write_text("{partial", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "transcript complete\n", "")

    bridge = FluidAudioBridge(
        FluidAudioCLIConfig(command=("fluidaudiocli",)),
        runner=run,
    )

    result = bridge.transcribe("audio.wav", output_json=output)

    assert result.returncode == 0
    assert result.stdout == "transcript complete\n"
    assert result.stderr == ""
    assert result.parsed_json is None
    assert result.parse_error is not None
    assert result.artifacts == {"transcript": output}


def test_transcribe_captures_json_decode_errors(tmp_path: Path) -> None:
    output = tmp_path / "transcript.json"

    def run(
        command: Sequence[str],
        env: Mapping[str, str],
        cwd: Path | None,
        timeout_s: float | None,
    ) -> subprocess.CompletedProcess[str]:
        output.write_bytes(b"\xff\xfe")
        return subprocess.CompletedProcess(command, 0, "raw output\n", "")

    bridge = FluidAudioBridge(
        FluidAudioCLIConfig(command=("fluidaudiocli",)),
        runner=run,
    )

    result = bridge.transcribe("audio.wav", output_json=output)

    assert result.returncode == 0
    assert result.stdout == "raw output\n"
    assert result.parsed_json is None
    assert result.parse_error is not None
    assert result.artifacts == {"transcript": output}


def test_preexisting_unchanged_output_is_not_reported_or_parsed(tmp_path: Path) -> None:
    output = tmp_path / "transcript.json"
    output.write_text('{"text": "stale"}', encoding="utf-8")
    bridge = FluidAudioBridge(
        FluidAudioCLIConfig(command=("fluidaudiocli",)),
        runner=_runner([], stdout='{"text": "fresh"}'),
    )

    result = bridge.transcribe("audio.wav", output_json=output)

    assert result.parsed_json == {"text": "fresh"}
    assert result.artifacts == {}


def test_relative_artifact_is_resolved_against_command_cwd(tmp_path: Path) -> None:
    def run(
        command: Sequence[str],
        env: Mapping[str, str],
        cwd: Path | None,
        timeout_s: float | None,
    ) -> subprocess.CompletedProcess[str]:
        assert cwd is not None
        (cwd / "speech.wav").write_bytes(b"RIFF")
        return subprocess.CompletedProcess(command, 0, "", "")

    bridge = FluidAudioBridge(
        FluidAudioCLIConfig(command=("fluidaudiocli",), cwd=tmp_path),
        runner=run,
    )

    result = bridge.vad_analyze("audio.wav", export_wav="speech.wav")

    assert result.artifacts == {"speech_audio": tmp_path / "speech.wav"}


def test_env_command_is_split(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setenv("FLUID_BRIDGE_CLI", "/opt/bin/fluidaudiocli --json")
    bridge = FluidAudioBridge(runner=_runner(calls))

    bridge.vad_analyze("audio.wav", streaming=True, threshold=0.65)

    assert calls == [
        ["/opt/bin/fluidaudiocli", "--json", "vad-analyze", "audio.wav", "--streaming", "--threshold", "0.65"]
    ]


def test_package_path_is_last_discovery_option(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[list[str]] = []
    monkeypatch.delenv("FLUID_BRIDGE_CLI", raising=False)
    monkeypatch.setenv("FLUID_AUDIO_PACKAGE", "/src/FluidAudio")
    monkeypatch.setattr("fluid_bridge.bridge.shutil.which", lambda name: None)
    output = tmp_path / "out.wav"

    def run(
        command: Sequence[str],
        env: Mapping[str, str],
        cwd: Path | None,
        timeout_s: float | None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(command))
        output.write_bytes(b"RIFF")
        return subprocess.CompletedProcess(command, 0, "", "")

    bridge = FluidAudioBridge(runner=run)

    result = bridge.tts("Hello", output, backend="kokoro-ane")

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
            str(output),
            "--backend",
            "kokoro-ane",
        ]
    ]
    assert result.output_path == output
    assert result.artifacts == {"audio": output}


def test_vad_reports_exported_speech_audio_artifact(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    output = tmp_path / "speech.wav"

    def run(
        command: Sequence[str],
        env: Mapping[str, str],
        cwd: Path | None,
        timeout_s: float | None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(list(command))
        output.write_bytes(b"RIFF")
        return subprocess.CompletedProcess(command, 0, "", "")

    bridge = FluidAudioBridge(
        FluidAudioCLIConfig(command=("fluidaudiocli",)),
        runner=run,
    )

    result = bridge.vad_analyze("audio.wav", export_wav=output)

    assert calls == [
        ["fluidaudiocli", "vad-analyze", "audio.wav", "--export-wav", str(output)]
    ]
    assert result.output_path == output
    assert result.artifacts == {"speech_audio": output}


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


@pytest.mark.parametrize("live", [False, True])
def test_execution_wraps_subprocess_timeout(live: bool) -> None:
    def timeout_runner(
        command: Sequence[str],
        env: Mapping[str, str],
        cwd: Path | None,
        timeout_s: float | None,
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, 0.25)

    bridge = FluidAudioBridge(
        FluidAudioCLIConfig(command=("fluidaudiocli",)),
        runner=timeout_runner,
        live_runner=timeout_runner,
    )

    with pytest.raises(FluidAudioBridgeError, match="timed out after 0.25 seconds"):
        (bridge.run_live if live else bridge.run)(["future-command"])


@pytest.mark.parametrize("live", [False, True])
def test_execution_wraps_process_start_error(live: bool) -> None:
    def missing_runner(
        command: Sequence[str],
        env: Mapping[str, str],
        cwd: Path | None,
        timeout_s: float | None,
    ) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("missing executable")

    bridge = FluidAudioBridge(
        FluidAudioCLIConfig(command=("fluidaudiocli",)),
        runner=missing_runner,
        live_runner=missing_runner,
    )

    with pytest.raises(FluidAudioBridgeError, match="Unable to run FluidAudio CLI"):
        (bridge.run_live if live else bridge.run)(["future-command"])


def test_capabilities_compares_advertised_commands_with_pinned_baseline() -> None:
    help_text = """\
FluidAudio CLI

Commands:
    process                 Process an audio file
    transcribe              Transcribe audio
    future-command          A newly added upstream command
    help                    Show help

Run 'fluidaudio <command> --help' for command-specific options.
"""
    bridge = FluidAudioBridge(
        FluidAudioCLIConfig(command=("fluidaudiocli",)),
        runner=_runner([], stdout=help_text),
    )

    report = bridge.capabilities()

    assert report.baseline_commit == "372eb32a3b23342d11dca41ed75cd4d11d3f8955"
    assert len(report.baseline_commands) == 33
    assert report.advertised_commands == ("future-command", "process", "transcribe")
    assert "vad-analyze" in report.baseline_not_advertised
    assert report.additional_commands == ("future-command",)
    assert report.probe_ok is True
    report_dict = report.to_dict()
    assert len(report_dict["baseline_groups"]["asr"]) == 19
    assert report_dict["baseline_groups"]["diarization"] == [
        "diarization-benchmark",
        "process",
        "sortformer",
        "sortformer-benchmark",
        "lseend",
        "lseend-benchmark",
    ]


@pytest.mark.parametrize(
    ("returncode", "probe_output"),
    [(2, "FluidAudio failed to start"), (0, "Unrecognized help format")],
)
def test_capabilities_does_not_report_deltas_without_a_valid_comparison(
    returncode: int, probe_output: str
) -> None:
    bridge = FluidAudioBridge(
        FluidAudioCLIConfig(command=("fluidaudiocli",)),
        runner=_runner([], stderr=probe_output, returncode=returncode),
    )

    report = bridge.capabilities()

    assert report.probe_ok is False
    assert report.advertised_commands == ()
    assert report.baseline_not_advertised == ()
    assert report.additional_commands == ()


def test_cli_capabilities_prints_machine_readable_report(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    report = CapabilityReport(
        baseline_commit="baseline-sha",
        baseline_commands=("process", "transcribe"),
        advertised_commands=("process", "transcribe", "future-command"),
        baseline_not_advertised=(),
        additional_commands=("future-command",),
        probe_returncode=0,
    )

    class FakeBridge:
        def capabilities(self) -> CapabilityReport:
            return report

    monkeypatch.setattr("fluid_bridge.cli.FluidAudioBridge", lambda: FakeBridge())

    code = cli_main(["capabilities"])

    assert json.loads(capsys.readouterr().out) == report.to_dict()
    assert code == 0


def test_cli_transcribe_forwards_upstream_option_tail(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[Path, str | None, bool, str | None, Path | None, list[str]]] = []

    class FakeBridge:
        def transcribe(
            self,
            audio: Path,
            *,
            model_version: str | None,
            streaming: bool,
            language: str | None,
            output_json: Path | None,
            extra_args: Sequence[str],
        ) -> CommandResult:
            calls.append(
                (audio, model_version, streaming, language, output_json, list(extra_args))
            )
            return CommandResult(("fluidaudiocli", "transcribe"), 0, "done", "")

    monkeypatch.setattr("fluid_bridge.cli.FluidAudioBridge", lambda: FakeBridge())

    code = cli_main(
        [
            "transcribe",
            "meeting.wav",
            "--model-version",
            "v3",
            "--streaming",
            "--language",
            "en",
            "--output-json",
            "transcript.json",
            "--",
            "--future-flag",
        ]
    )

    assert calls == [
        (
            Path("meeting.wav"),
            "v3",
            True,
            "en",
            Path("transcript.json"),
            ["--future-flag"],
        )
    ]
    assert capsys.readouterr().out == "done\n"
    assert code == 0


@pytest.mark.parametrize(
    ("argv", "expected_call"),
    [
        (
            [
                "diarize",
                "meeting.wav",
                "--export-embeddings",
                "vectors.json",
                "--",
                "--future-diarizer-flag",
            ],
            ("diarize", ["--future-diarizer-flag"]),
        ),
        (
            [
                "vad",
                "meeting.wav",
                "--export-wav",
                "speech.wav",
                "--",
                "--min-silence-ms",
                "400",
            ],
            ("vad", ["--min-silence-ms", "400"]),
        ),
        (
            [
                "tts",
                "Hello",
                "--output",
                "voice.wav",
                "--backend",
                "pocket",
                "--clone-voice",
                "speaker.wav",
                "--",
                "--temperature",
                "0.7",
            ],
            ("tts", ["--temperature", "0.7"]),
        ),
    ],
)
def test_cli_friendly_commands_forward_upstream_option_tail(
    argv: list[str],
    expected_call: tuple[str, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, list[str]]] = []

    class FakeBridge:
        def diarize(self, *args: object, **kwargs: object) -> CommandResult:
            assert kwargs["export_embeddings"] == Path("vectors.json")
            calls.append(("diarize", list(kwargs["extra_args"])))
            return CommandResult(("fluidaudiocli", "process"), 0, "", "")

        def vad_analyze(self, *args: object, **kwargs: object) -> CommandResult:
            assert kwargs["export_wav"] == Path("speech.wav")
            calls.append(("vad", list(kwargs["extra_args"])))
            return CommandResult(("fluidaudiocli", "vad-analyze"), 0, "", "")

        def tts(self, *args: object, **kwargs: object) -> CommandResult:
            assert kwargs["clone_voice"] == Path("speaker.wav")
            calls.append(("tts", list(kwargs["extra_args"])))
            return CommandResult(("fluidaudiocli", "tts"), 0, "", "")

    monkeypatch.setattr("fluid_bridge.cli.FluidAudioBridge", lambda: FakeBridge())

    assert cli_main(argv) == 0
    assert calls == [expected_call]


def test_missing_cli_raises_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLUID_BRIDGE_CLI", raising=False)
    monkeypatch.delenv("FLUID_AUDIO_PACKAGE", raising=False)
    monkeypatch.setattr("fluid_bridge.bridge.shutil.which", lambda name: None)
    bridge = FluidAudioBridge()

    with pytest.raises(FluidAudioBridgeError, match="FluidAudio CLI not found"):
        bridge.transcribe("audio.wav")


def test_diarize_parses_output_json(tmp_path: Path) -> None:
    output = tmp_path / "result.json"
    embeddings = tmp_path / "embeddings.json"
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
        embeddings.write_text("[]", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    bridge = FluidAudioBridge(FluidAudioCLIConfig(command=("fluidaudiocli",)), runner=run)

    result = bridge.diarize(
        "meeting.wav",
        mode="offline",
        threshold=0.6,
        output_path=output,
        export_embeddings=embeddings,
    )

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
            "--export-embeddings",
            str(embeddings),
        ]
    ]
    assert result.artifacts == {
        "diarization": output,
        "embeddings": embeddings,
    }


def test_diarize_does_not_return_deleted_temporary_artifact() -> None:
    payload = {"segments": []}
    temporary_outputs: list[Path] = []

    def run(
        command: Sequence[str],
        env: Mapping[str, str],
        cwd: Path | None,
        timeout_s: float | None,
    ) -> subprocess.CompletedProcess[str]:
        output = Path(command[command.index("--output") + 1])
        temporary_outputs.append(output)
        output.write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    bridge = FluidAudioBridge(
        FluidAudioCLIConfig(command=("fluidaudiocli",)),
        runner=run,
    )

    result = bridge.diarize("meeting.wav")

    assert result.parsed_json == payload
    assert result.output_path is None
    assert result.artifacts == {}
    assert len(temporary_outputs) == 1
    assert temporary_outputs[0].exists() is False


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


def test_failed_command_does_not_claim_requested_artifacts(tmp_path: Path) -> None:
    output = tmp_path / "missing.wav"
    bridge = FluidAudioBridge(
        FluidAudioCLIConfig(command=("fluidaudiocli",)),
        runner=_runner([], stderr="model missing\n", returncode=2),
    )

    result = bridge.tts("Hello", output)

    assert result.returncode == 2
    assert result.output_path == output
    assert result.artifacts == {}


def test_command_result_preserves_legacy_positional_output_path() -> None:
    result = CommandResult(("fluidaudiocli",), 0, "", "", None, Path("out.json"))

    assert result.output_path == Path("out.json")
    assert result.parse_error is None


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
