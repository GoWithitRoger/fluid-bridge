from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from fluid_bridge import FluidAudioBridge, FluidAudioBridgeError, FluidAudioCLIConfig


def test_stream_yields_both_output_channels_and_final_result(tmp_path: Path) -> None:
    fake_cli = tmp_path / "streaming_cli.py"
    fake_cli.write_text(
        """\
import os
import sys
import time

print(f"args:{sys.argv[1:]}", flush=True)
print(f"context:{os.getcwd()}:{os.environ['FLUID_STREAM_TEST']}", flush=True)
time.sleep(0.02)
print("warning", file=sys.stderr, flush=True)
time.sleep(0.02)
print("final", flush=True)
raise SystemExit(7)
""",
        encoding="utf-8",
    )
    bridge = FluidAudioBridge(
        FluidAudioCLIConfig(
            command=(sys.executable, str(fake_cli)),
            cwd=tmp_path,
            env={"FLUID_STREAM_TEST": "configured"},
        )
    )

    command = bridge.stream(["future-command", "--new-option", "value"])
    events = list(command)
    result = command.wait()

    assert [event.text for event in events if event.stream == "stdout"] == [
        "args:['future-command', '--new-option', 'value']\n",
        f"context:{tmp_path}:configured\n",
        "final\n",
    ]
    assert [event.text for event in events if event.stream == "stderr"] == ["warning\n"]
    assert result.command == (
        sys.executable,
        str(fake_cli),
        "future-command",
        "--new-option",
        "value",
    )
    assert result.stdout == (
        "args:['future-command', '--new-option', 'value']\n"
        f"context:{tmp_path}:configured\n"
        "final\n"
    )
    assert result.stderr == "warning\n"
    assert result.returncode == 7
    with pytest.raises(FluidAudioBridgeError, match="only be consumed once"):
        list(command)


def test_stream_can_be_cancelled_after_an_event(tmp_path: Path) -> None:
    fake_cli = tmp_path / "long_running_cli.py"
    fake_cli.write_text(
        """\
import time

print("ready", flush=True)
time.sleep(30)
""",
        encoding="utf-8",
    )
    bridge = FluidAudioBridge(
        FluidAudioCLIConfig(command=(sys.executable, str(fake_cli)))
    )
    command = bridge.stream(["stream"])
    events = iter(command)

    assert next(events).text == "ready\n"
    started = time.monotonic()
    command.cancel()
    result = command.wait()

    assert time.monotonic() - started < 2
    assert result.stdout == "ready\n"
    assert result.returncode < 0


def test_stream_timeout_stops_process_and_keeps_diagnostics(tmp_path: Path) -> None:
    fake_cli = tmp_path / "timed_cli.py"
    fake_cli.write_text(
        """\
import signal
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
print("started", flush=True)
time.sleep(2)
""",
        encoding="utf-8",
    )
    bridge = FluidAudioBridge(
        FluidAudioCLIConfig(
            command=(sys.executable, str(fake_cli)),
            timeout_s=0.05,
        )
    )
    command = bridge.stream(["slow-command"])

    started = time.monotonic()
    with pytest.raises(FluidAudioBridgeError, match="timed out after 0.05 seconds"):
        list(command)

    assert time.monotonic() - started < 0.3
    result = command.wait()
    assert result.stdout == "started\n"
    assert result.returncode < 0


def test_stream_reports_process_start_failure() -> None:
    bridge = FluidAudioBridge(
        FluidAudioCLIConfig(command=("/definitely/missing/fluidaudiocli",))
    )

    with pytest.raises(FluidAudioBridgeError, match="Unable to start FluidAudio CLI"):
        bridge.stream(["transcribe", "meeting.wav"])
