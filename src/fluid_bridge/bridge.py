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
from typing import TYPE_CHECKING, Any, Literal, overload

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
class DoctorFinding:
    """Actionable environment finding from a doctor inspection."""

    code: str
    severity: Literal["warning", "error"]
    message: str
    action: str

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-serializable finding."""
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "action": self.action,
        }


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
    developer_dir: str | None = None
    probe_command: tuple[str, ...] | None = None
    probe_returncode: int | None = None
    probe_stdout: str = ""
    probe_stderr: str = ""
    probe_error: str | None = None
    findings: tuple[DoctorFinding, ...] = ()

    @property
    def probe_attempted(self) -> bool:
        """Return whether an executable CLI probe was requested and prepared."""
        return self.probe_command is not None

    @property
    def ready(self) -> bool | None:
        """Return readiness, or None when executable readiness was not probed."""
        if any(finding.severity == "error" for finding in self.findings):
            return False
        if not self.probe_attempted:
            return None
        return self.probe_error is None and self.probe_returncode == 0

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
            "developer_dir": self.developer_dir,
            "probe_attempted": self.probe_attempted,
            "probe_command": list(self.probe_command) if self.probe_command else None,
            "probe_returncode": self.probe_returncode,
            "probe_stdout": self.probe_stdout,
            "probe_stderr": self.probe_stderr,
            "probe_error": self.probe_error,
            "ready": self.ready,
            "findings": [finding.to_dict() for finding in self.findings],
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

        try:
            proc = self._runner(command, env, cwd, self.config.timeout_s)
        except subprocess.TimeoutExpired as exc:
            raise FluidAudioBridgeError(
                f"FluidAudio CLI timed out after {exc.timeout} seconds"
            ) from exc
        except OSError as exc:
            raise FluidAudioBridgeError(f"Unable to run FluidAudio CLI: {exc}") from exc
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
        except subprocess.TimeoutExpired as exc:
            raise FluidAudioBridgeError(
                f"FluidAudio CLI timed out after {exc.timeout} seconds"
            ) from exc
        except OSError as exc:
            raise FluidAudioBridgeError(f"Unable to run FluidAudio CLI: {exc}") from exc
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

    def doctor(self, *, probe_cli: bool = False) -> DoctorReport:
        """Inspect prerequisites and optionally execute non-invasive root help."""
        swift_path = shutil.which("swift")
        swift_version = self._swift_version(swift_path)
        fluidaudio_cli_path = shutil.which("fluidaudiocli")
        developer_dir = self._developer_dir()
        resolved_command: tuple[str, ...] | None
        try:
            resolved_command = tuple(self._resolve_command())
        except FluidAudioBridgeError:
            resolved_command = None

        findings: list[DoctorFinding] = []
        if platform.system() != "Darwin":
            findings.append(
                DoctorFinding(
                    code="macos_required",
                    severity="error",
                    message="FluidAudioCLI is supported only on macOS.",
                    action="Run fluid-bridge on a supported macOS host.",
                )
            )
        if resolved_command is None:
            findings.append(
                DoctorFinding(
                    code="cli_not_configured",
                    severity="error",
                    message="No FluidAudio CLI command could be resolved.",
                    action=(
                        "Put fluidaudiocli on PATH, set FLUID_AUDIO_PACKAGE, or set "
                        "FLUID_BRIDGE_CLI."
                    ),
                )
            )

        uses_swift = bool(resolved_command and Path(resolved_command[0]).name == "swift")
        if uses_swift and swift_path is None:
            findings.append(
                DoctorFinding(
                    code="swift_not_found",
                    severity="error",
                    message="The configured FluidAudio command requires Swift, but Swift was not found.",
                    action="Install Xcode and select its developer directory with xcode-select.",
                )
            )
        elif uses_swift and developer_dir and "CommandLineTools" in developer_dir:
            findings.append(
                DoctorFinding(
                    code="command_line_tools_selected",
                    severity="warning",
                    message="xcode-select points to Command Line Tools rather than a full Xcode toolchain.",
                    action=(
                        "If Swift cannot build FluidAudio, install a matching Xcode release and select "
                        "its Contents/Developer directory."
                    ),
                )
            )

        probe_command: tuple[str, ...] | None = None
        probe_returncode: int | None = None
        probe_stdout = ""
        probe_stderr = ""
        probe_error: str | None = None
        if probe_cli and resolved_command is not None:
            command, env, cwd = self._prepare_invocation(["--help"])
            probe_command = tuple(command)
            try:
                proc = self._runner(command, env, cwd, self.config.timeout_s)
            except (FluidAudioBridgeError, OSError, subprocess.SubprocessError) as exc:
                probe_error = str(exc)
                findings.append(
                    DoctorFinding(
                        code="cli_probe_error",
                        severity="error",
                        message="FluidAudio CLI root help could not be executed.",
                        action="Inspect probe_error and verify the configured command and timeout.",
                    )
                )
            else:
                probe_returncode = proc.returncode
                probe_stdout = proc.stdout or ""
                probe_stderr = proc.stderr or ""
                if proc.returncode != 0:
                    combined_output = "\n".join((probe_stdout, probe_stderr))
                    if self._is_swift_toolchain_mismatch(combined_output):
                        findings.append(
                            DoctorFinding(
                                code="swift_toolchain_mismatch",
                                severity="error",
                                message=(
                                    "The selected Swift compiler is incompatible with the macOS SDK "
                                    "used to build FluidAudio."
                                ),
                                action=(
                                    "Select an Xcode toolchain whose Swift compiler matches its SDK, "
                                    "then rerun doctor --probe."
                                ),
                            )
                        )
                    else:
                        findings.append(
                            DoctorFinding(
                                code="cli_probe_failed",
                                severity="error",
                                message=f"FluidAudio CLI root help exited with {proc.returncode}.",
                                action="Inspect probe_stdout and probe_stderr for the upstream failure.",
                            )
                        )

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
            developer_dir=developer_dir,
            probe_command=probe_command,
            probe_returncode=probe_returncode,
            probe_stdout=probe_stdout,
            probe_stderr=probe_stderr,
            probe_error=probe_error,
            findings=tuple(findings),
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
    @overload
    def _resolve_io_path(path: Path, cwd: Path | None) -> Path: ...

    @staticmethod
    @overload
    def _resolve_io_path(path: None, cwd: Path | None) -> None: ...

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
    def _developer_dir() -> str | None:
        try:
            proc = subprocess.run(
                ["xcode-select", "-p"],
                capture_output=True,
                check=False,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0:
            return None
        return proc.stdout.strip() or None

    @staticmethod
    def _is_swift_toolchain_mismatch(output: str) -> bool:
        text = output.lower()
        return (
            "compiled with swift" in text and "cannot be imported by the swift" in text
        ) or "sdk is not supported by the compiler" in text

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
