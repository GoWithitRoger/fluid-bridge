from __future__ import annotations

import os
import platform
from pathlib import Path

import pytest

from fluid_bridge import FluidAudioBridge, FluidAudioCLIConfig
from fluid_bridge.capabilities import UNSAFE_HELP_COMMANDS, UPSTREAM_COMMANDS

if os.environ.get("FLUID_BRIDGE_LIVE") != "1":
    pytest.skip("set FLUID_BRIDGE_LIVE=1 to run real macOS FluidAudio checks", allow_module_level=True)
if platform.system() != "Darwin":
    pytest.skip("real FluidAudio CLI checks require macOS", allow_module_level=True)


def _bridge() -> FluidAudioBridge:
    raw_timeout = os.environ.get("FLUID_BRIDGE_LIVE_TIMEOUT", "600")
    try:
        timeout = float(raw_timeout)
    except ValueError:
        pytest.fail(f"FLUID_BRIDGE_LIVE_TIMEOUT must be a number, got {raw_timeout!r}")
    if timeout <= 0:
        pytest.fail("FLUID_BRIDGE_LIVE_TIMEOUT must be greater than zero")
    return FluidAudioBridge(FluidAudioCLIConfig(timeout_s=timeout))


def _require_download_consent() -> None:
    if os.environ.get("FLUID_BRIDGE_LIVE_ALLOW_DOWNLOADS") != "1":
        pytest.skip("set FLUID_BRIDGE_LIVE_ALLOW_DOWNLOADS=1 to permit model downloads")


def _require_audio(variable: str = "FLUID_BRIDGE_LIVE_AUDIO") -> Path:
    raw_path = os.environ.get(variable)
    if not raw_path:
        pytest.skip(f"set {variable} to an absolute audio path")
    path = Path(raw_path)
    if not path.is_absolute() or not path.is_file():
        pytest.fail(f"{variable} must name an existing absolute file: {path}")
    return path


@pytest.mark.live
def test_live_doctor_probe_is_ready() -> None:
    report = _bridge().doctor(probe_cli=True)

    assert report.ready is True, report.to_dict()


@pytest.mark.live
def test_live_safe_command_help_surfaces() -> None:
    report = _bridge().deep_capabilities()

    assert tuple(report.commands) == UPSTREAM_COMMANDS
    assert report.skipped_commands == tuple(
        command for command in UPSTREAM_COMMANDS if command in UNSAFE_HELP_COMMANDS
    )
    assert report.failed_baseline_commands == (), report.to_dict()
    assert report.probe_ok is True, report.to_dict()


@pytest.mark.live_inference
def test_live_asr_smoke(tmp_path: Path) -> None:
    _require_download_consent()
    audio = _require_audio()
    output = tmp_path / "transcript.json"

    result = _bridge().transcribe(audio, output_json=output)

    result.raise_for_error()
    assert result.parsed_json is not None
    assert result.artifacts == {"transcript": output}


@pytest.mark.live_inference
def test_live_diarization_smoke(tmp_path: Path) -> None:
    _require_download_consent()
    audio = _require_audio()
    output = tmp_path / "diarization.json"
    embeddings = tmp_path / "embeddings.json"

    result = _bridge().diarize(audio, output_path=output, export_embeddings=embeddings)

    result.raise_for_error()
    assert result.parsed_json is not None
    assert result.artifacts == {"diarization": output, "embeddings": embeddings}


@pytest.mark.live_inference
def test_live_vad_smoke(tmp_path: Path) -> None:
    _require_download_consent()
    audio = _require_audio()
    output = tmp_path / "speech.wav"

    result = _bridge().vad_analyze(audio, export_wav=output)

    result.raise_for_error()
    assert result.artifacts == {"speech_audio": output}


@pytest.mark.live_inference
def test_live_tts_smoke(tmp_path: Path) -> None:
    _require_download_consent()
    if os.environ.get("FLUID_BRIDGE_LIVE_TTS") != "1":
        pytest.skip("set FLUID_BRIDGE_LIVE_TTS=1 to run TTS")
    output = tmp_path / "speech.wav"

    result = _bridge().tts("Fluid bridge live validation.", output)

    result.raise_for_error()
    assert result.artifacts == {"audio": output}


@pytest.mark.live_inference
def test_live_voice_cloning_smoke(tmp_path: Path) -> None:
    _require_download_consent()
    voice = _require_audio("FLUID_BRIDGE_LIVE_VOICE")
    output = tmp_path / "cloned.wav"

    result = _bridge().tts(
        "Fluid bridge voice cloning validation.",
        output,
        backend="pocket",
        clone_voice=voice,
    )

    result.raise_for_error()
    assert result.artifacts == {"audio": output}
