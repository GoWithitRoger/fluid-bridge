"""Official FluidAudio CLI adapter used by the public Python API."""

from __future__ import annotations

import json
import os
import platform
import shlex
import shutil
import signal
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fluid_bridge.capabilities import (
    UNSAFE_HELP_COMMANDS,
    UPSTREAM_COMMANDS,
    CapabilityReport,
    CommandCapability,
    DeepCapabilityReport,
)

if TYPE_CHECKING:
    from fluid_bridge.streaming import StreamingCommand


class FluidAudioBridgeError(RuntimeError):
    """Raised when FluidAudio CLI discovery or execution fails."""


Runner = Callable[
    [Sequence[str], Mapping[str, str], Path | None, float | None],
    subprocess.CompletedProcess[str],
]


@dataclass(frozen=True)
class FluidAudioCLIConfig:
    """Configuration for locating and running FluidAudio's official CLI."""

    command: tuple[str, ...] | None = None
    executable: str | Path | None = None
    package_path: str | Path | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    cwd: str | Path | None = None
    timeout_s: float | None = None


@dataclass(frozen=True)
class CommandResult:
    """Completed FluidAudio CLI invocation."""

    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    parsed_json: Any | None = None
    output_path: Path | None = None
    artifacts: Mapping[str, Path] = field(default_factory=dict)
    parse_error: str | None = None

    @property
    def ok(self) -> bool:
        """Return True when the command exited successfully."""
        return self.returncode == 0

    def raise_for_error(self) -> None:
        """Raise a bridge error if the command failed."""
        if self.ok:
            return
        detail = self.stderr.strip() or self.stdout.strip() or "no output"
        raise FluidAudioBridgeError(
            f"FluidAudio CLI failed with exit code {self.returncode}: {detail}"
        )


@dataclass(frozen=True)
class DoctorReport:
    """Environment report for FluidAudio CLI setup."""

    platform: str
    is_macos: bool
    swift_path: str | None
    swift_version: str | None
    fluidaudio_cli_path: str | None
    fluid_bridge_cli_env: str | None
    fluid_audio_package_env: str | None
    configured_command: tuple[str, ...] | None
    resolved_command: tuple[str, ...] | None
    model_cache_note: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable report."""
        return {
            "platform": self.platform,
            "is_macos": self.is_macos,
            "swift_path": self.swift_path,
            "swift_version": self.swift_version,
            "fluidaudio_cli_path": self.fluidaudio_cli_path,
            "fluid_bridge_cli_env": self.fluid_bridge_cli_env,
            "fluid_audio_package_env": self.fluid_audio_package_env,
            "configured_command": list(self.configured_command)
            if self.configured_command
            else None,
            "resolved_command": list(self.resolved_command) if self.resolved_command else None,
            "model_cache_note": self.model_cache_note,
        }


class FluidAudioBridge:
    """Run FluidAudio's official CLI from Python."""

    def __init__(
        self,
        config: FluidAudioCLIConfig | None = None,
        *,
        runner: Runner | None = None,
        live_runner: Runner | None = None,
    ) -> None:
        self.config = config or FluidAudioCLIConfig()
        self._runner = runner or self._subprocess_runner
        self._live_runner = live_runner or self._subprocess_live_runner

    def run(
        self,
        args: Sequence[str],
        *,
        parse_json: bool = False,
        output_path: str | Path | None = None,
        artifacts: Mapping[str, str | Path] | None = None,
    ) -> CommandResult:
        """Run FluidAudio CLI with ``args`` appended to the resolved command prefix."""
        command, env, cwd = self._prepare_invocation(args)
        resolved_output_path = Path(output_path) if output_path is not None else None
        io_output_path = self._resolve_io_path(resolved_output_path, cwd)
        requested_artifacts = {
            name: self._resolve_io_path(Path(path), cwd)
            for name, path in (artifacts or {}).items()
        }
        prior_artifact_states = {
            name: self._file_state(path) for name, path in requested_artifacts.items()
        }
        prior_output_state = self._file_state(io_output_path)

        proc = self._runner(command, env, cwd, self.config.timeout_s)
        parsed_json = None
        parse_error = None

        if parse_json and proc.returncode == 0:
            try:
                parse_path = (
                    io_output_path
                    if self._file_state(io_output_path) != prior_output_state
                    else None
                )
                parsed_json = self._parse_json(proc.stdout, parse_path)
            except (OSError, ValueError) as exc:
                parse_error = str(exc)

        resolved_artifacts = {
            name: path
            for name, path in requested_artifacts.items()
            if proc.returncode == 0
            and path.is_file()
            and self._file_state(path) != prior_artifact_states[name]
        }

        return CommandResult(
            command=tuple(command),
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            parsed_json=parsed_json,
            parse_error=parse_error,
            output_path=resolved_output_path,
            artifacts=resolved_artifacts,
        )

    def run_live(self, args: Sequence[str]) -> CommandResult:
        """Run FluidAudio CLI with stdin, stdout, and stderr inherited from the caller."""
        command, env, cwd = self._prepare_invocation(args)
        try:
            proc = self._live_runner(command, env, cwd, self.config.timeout_s)
        except KeyboardInterrupt:
            return CommandResult(
                command=tuple(command),
                returncode=-signal.SIGINT,
                stdout="",
                stderr="",
            )
        return CommandResult(
            command=tuple(command),
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
        )

    def stream(self, args: Sequence[str]) -> StreamingCommand:
        """Start FluidAudio and expose incremental stdout and stderr events."""
        from fluid_bridge.streaming import StreamingCommand

        command, env, cwd = self._prepare_invocation(args)
        return StreamingCommand.start(command, env, cwd, self.config.timeout_s)

    def transcribe(
        self,
        audio_path: str | Path,
        *,
        model_version: str | None = None,
        streaming: bool = False,
        language: str | None = None,
        output_json: str | Path | None = None,
        extra_args: Sequence[str] | None = None,
    ) -> CommandResult:
        """Run ``fluidaudiocli transcribe`` for an audio file."""
        args = ["transcribe", str(audio_path)]
        if model_version:
            args += ["--model-version", model_version]
        if streaming:
            args.append("--streaming")
        if language:
            args += ["--language", language]
        if output_json is not None:
            args += ["--output-json", str(output_json)]
        if extra_args:
            args += list(extra_args)
        return self.run(
            args,
            parse_json=output_json is not None,
            output_path=output_json,
            artifacts={"transcript": output_json} if output_json is not None else None,
        )

    def diarize(
        self,
        audio_path: str | Path,
        *,
        mode: str | None = None,
        threshold: float | None = None,
        output_path: str | Path | None = None,
        export_embeddings: str | Path | None = None,
        extra_args: Sequence[str] | None = None,
    ) -> CommandResult:
        """Run ``fluidaudiocli process`` and parse JSON output when available."""
        cleanup_path: Path | None = None
        requested_output = output_path is not None
        if output_path is None:
            with tempfile.NamedTemporaryFile(
                prefix="fluid-bridge-", suffix=".json", delete=False
            ) as handle:
                output_path = handle.name
            cleanup_path = Path(output_path)

        resolved_output_path = Path(output_path)
        args = ["process", str(audio_path), "--output", str(resolved_output_path)]
        if mode:
            args += ["--mode", mode]
        if threshold is not None:
            args += ["--threshold", str(threshold)]
        if export_embeddings is not None:
            args += ["--export-embeddings", str(export_embeddings)]
        if extra_args:
            args += list(extra_args)

        artifacts: dict[str, str | Path] = {}
        if requested_output:
            artifacts["diarization"] = resolved_output_path
        if export_embeddings is not None:
            artifacts["embeddings"] = export_embeddings

        try:
            result = self.run(
                args,
                parse_json=True,
                output_path=resolved_output_path,
                artifacts=artifacts,
            )
            return replace(result, output_path=None) if cleanup_path is not None else result
        finally:
            if cleanup_path is not None:
                cleanup_path.unlink(missing_ok=True)

    def vad_analyze(
        self,
        audio_path: str | Path,
        *,
        streaming: bool = False,
        threshold: float | None = None,
        export_wav: str | Path | None = None,
        extra_args: Sequence[str] | None = None,
    ) -> CommandResult:
        """Run ``fluidaudiocli vad-analyze`` for an audio file."""
        args = ["vad-analyze", str(audio_path)]
        if streaming:
            args.append("--streaming")
        if threshold is not None:
            args += ["--threshold", str(threshold)]
        if export_wav is not None:
            args += ["--export-wav", str(export_wav)]
        if extra_args:
            args += list(extra_args)
        return self.run(
            args,
            output_path=export_wav,
            artifacts={"speech_audio": export_wav} if export_wav is not None else None,
        )

    def tts(
        self,
        text: str,
        output_path: str | Path,
        *,
        backend: str | None = None,
        language: str | None = None,
        clone_voice: str | Path | None = None,
        extra_args: Sequence[str] | None = None,
    ) -> CommandResult:
        """Run ``fluidaudiocli tts`` and write audio to ``output_path``."""
        args = ["tts", text, "--output", str(output_path)]
        if backend:
            args += ["--backend", backend]
        if language:
            args += ["--language", language]
        if clone_voice:
            args += ["--clone-voice", str(clone_voice)]
        if extra_args:
            args += list(extra_args)
        return self.run(
            args,
            output_path=output_path,
            artifacts={"audio": output_path},
        )

    def doctor(self) -> DoctorReport:
        """Inspect the current machine for FluidAudio CLI prerequisites."""
        swift_path = shutil.which("swift")
        swift_version = self._swift_version(swift_path)
        fluidaudio_cli_path = shutil.which("fluidaudiocli")
        resolved_command: tuple[str, ...] | None
        try:
            resolved_command = tuple(self._resolve_command())
        except FluidAudioBridgeError:
            resolved_command = None

        return DoctorReport(
            platform=platform.platform(),
            is_macos=platform.system() == "Darwin",
            swift_path=swift_path,
            swift_version=swift_version,
            fluidaudio_cli_path=fluidaudio_cli_path,
            fluid_bridge_cli_env=os.environ.get("FLUID_BRIDGE_CLI"),
            fluid_audio_package_env=os.environ.get("FLUID_AUDIO_PACKAGE"),
            configured_command=self.config.command,
            resolved_command=resolved_command,
            model_cache_note=(
                "FluidAudio downloads and caches models on first use; see FluidAudio upstream "
                "documentation for registry, proxy, and offline-mode controls."
            ),
        )

    def capabilities(self) -> CapabilityReport:
        """Compare installed FluidAudio root help with the pinned command baseline."""
        result = self.run(["--help"])
        return CapabilityReport.from_probe(result.stdout, result.stderr, result.returncode)

    def deep_capabilities(self, *, include_additional: bool = False) -> DeepCapabilityReport:
        """Probe help for every pinned command and optionally new untrusted commands."""
        root = self.capabilities()
        commands = list(UPSTREAM_COMMANDS)
        if include_additional:
            commands.extend(root.additional_commands)

        probes: dict[str, CommandCapability] = {}
        baseline = set(UPSTREAM_COMMANDS)
        for command in commands:
            if reason := UNSAFE_HELP_COMMANDS.get(command):
                probes[command] = CommandCapability.from_skip(
                    command, baseline=command in baseline, reason=reason
                )
                continue
            try:
                result = self.run([command, "--help"])
            except (FluidAudioBridgeError, OSError, subprocess.SubprocessError) as exc:
                probes[command] = CommandCapability.from_error(
                    command, baseline=command in baseline, error=exc
                )
                continue
            probes[command] = CommandCapability.from_probe(
                command,
                baseline=command in baseline,
                stdout=result.stdout,
                stderr=result.stderr,
                returncode=result.returncode,
            )
        return DeepCapabilityReport(root=root, commands=probes)

    def _prepare_invocation(
        self, args: Sequence[str]
    ) -> tuple[list[str], dict[str, str], Path | None]:
        command = [*self._resolve_command(), *map(str, args)]
        env = os.environ.copy()
        env.update(self.config.env)
        cwd = Path(self.config.cwd) if self.config.cwd is not None else None
        return command, env, cwd

    def _resolve_command(self) -> list[str]:
        if self.config.command:
            return list(self.config.command)
        if self.config.executable:
            return [str(self.config.executable)]

        env_command = os.environ.get("FLUID_BRIDGE_CLI")
        if env_command:
            return shlex.split(env_command)

        executable = shutil.which("fluidaudiocli")
        if executable:
            return [executable]

        package_path = self.config.package_path or os.environ.get("FLUID_AUDIO_PACKAGE")
        if package_path:
            return ["swift", "run", "--package-path", str(package_path), "fluidaudiocli"]

        raise FluidAudioBridgeError(
            "FluidAudio CLI not found. Set FLUID_BRIDGE_CLI to a command, put "
            "fluidaudiocli on PATH, or set FLUID_AUDIO_PACKAGE to a FluidAudio checkout."
        )

    @staticmethod
    def _parse_json(stdout: str, output_path: Path | None) -> Any | None:
        if output_path is not None and output_path.exists() and output_path.stat().st_size:
            with output_path.open("r", encoding="utf-8") as file:
                return json.load(file)
        text = stdout.strip()
        if not text:
            return None
        return json.loads(text)

    @staticmethod
    def _resolve_io_path(path: Path | None, cwd: Path | None) -> Path | None:
        if path is None or path.is_absolute() or cwd is None:
            return path
        return cwd / path

    @staticmethod
    def _file_state(path: Path | None) -> tuple[int, int, int, int, int] | None:
        if path is None:
            return None
        try:
            stat = path.stat()
        except OSError:
            return None
        return (
            stat.st_dev,
            stat.st_ino,
            stat.st_size,
            stat.st_mtime_ns,
            stat.st_ctime_ns,
        )

    @staticmethod
    def _swift_version(swift_path: str | None) -> str | None:
        if swift_path is None:
            return None
        try:
            proc = subprocess.run(
                [swift_path, "--version"],
                capture_output=True,
                check=False,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else None

    @staticmethod
    def _subprocess_runner(
        command: Sequence[str],
        env: Mapping[str, str],
        cwd: Path | None,
        timeout_s: float | None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(command),
            capture_output=True,
            cwd=cwd,
            env=dict(env),
            text=True,
            timeout=timeout_s,
            check=False,
        )

    @staticmethod
    def _subprocess_live_runner(
        command: Sequence[str],
        env: Mapping[str, str],
        cwd: Path | None,
        timeout_s: float | None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(command),
            cwd=cwd,
            env=dict(env),
            text=True,
            timeout=timeout_s,
            check=False,
        )
