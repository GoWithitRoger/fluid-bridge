"""Pinned FluidAudio CLI command baseline and help-based drift reporting."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

UPSTREAM_BASELINE_COMMIT = "372eb32a3b23342d11dca41ed75cd4d11d3f8955"

UPSTREAM_COMMAND_GROUPS: dict[str, tuple[str, ...]] = {
    "asr": (
        "asr-benchmark",
        "unified-benchmark",
        "fleurs-benchmark",
        "transcribe",
        "multi-stream",
        "parakeet-eou",
        "ctc-earnings-benchmark",
        "emission-delay-benchmark",
        "nemotron-benchmark",
        "nemotron-transcribe",
        "nemotron-multilingual-transcribe",
        "nemotron-multilingual-benchmark",
        "nemotron-multilingual-multi-stream-bench",
        "sensevoice-transcribe",
        "sensevoice-benchmark",
        "paraformer-transcribe",
        "ja-benchmark",
        "cohere-transcribe",
        "cohere-benchmark",
    ),
    "diarization": (
        "diarization-benchmark",
        "process",
        "sortformer",
        "sortformer-benchmark",
        "lseend",
        "lseend-benchmark",
    ),
    "vad": ("vad-analyze", "vad-benchmark"),
    "tts": (
        "tts",
        "tts-asr-verify",
        "tts-benchmark",
        "minimax-corpus",
        "g2p-benchmark",
    ),
    "datasets": ("download",),
}

UPSTREAM_COMMANDS = tuple(
    command for commands in UPSTREAM_COMMAND_GROUPS.values() for command in commands
)

# These commands do not intercept --help before starting network-backed work at the pinned commit.
UNSAFE_HELP_COMMANDS: dict[str, str] = {
    "unified-benchmark": "Upstream does not implement --help and starts a benchmark download.",
    "multi-stream": "Upstream consumes --help as the first audio path before checking options.",
    "lseend": "Upstream consumes --help as the first audio path before checking options.",
    "cohere-transcribe": "Upstream consumes --help as the first audio path before checking options.",
    "download": "Upstream treats --help as unknown and starts the default dataset download.",
}

_COMMAND_LINE = re.compile(r"^\s{2,}([a-z][a-z0-9-]*)\s{2,}\S")
_LONG_OPTION = re.compile(r"(?<![\w-])--[a-zA-Z0-9][a-zA-Z0-9-]*\b")


@dataclass(frozen=True)
class CommandCapability:
    """Installed help probe for one FluidAudio command."""

    command: str
    baseline: bool
    returncode: int | None
    options: tuple[str, ...]
    stdout: str
    stderr: str
    error: str | None = None
    skipped_reason: str | None = None

    @property
    def probe_ok(self) -> bool:
        """Return whether this command's help invocation succeeded."""
        return self.returncode == 0 and self.error is None

    @property
    def skipped(self) -> bool:
        """Return whether the probe was intentionally not executed."""
        return self.skipped_reason is not None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable probe result."""
        return {
            "command": self.command,
            "baseline": self.baseline,
            "returncode": self.returncode,
            "probe_ok": self.probe_ok,
            "options": list(self.options),
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error": self.error,
            "skipped": self.skipped,
            "skipped_reason": self.skipped_reason,
        }

    @classmethod
    def from_probe(
        cls,
        command: str,
        *,
        baseline: bool,
        stdout: str,
        stderr: str,
        returncode: int,
    ) -> CommandCapability:
        """Build a command capability from its help process result."""
        help_text = "\n".join((stdout, stderr))
        return cls(
            command=command,
            baseline=baseline,
            returncode=returncode,
            options=tuple(sorted(set(_LONG_OPTION.findall(help_text)))),
            stdout=stdout,
            stderr=stderr,
        )

    @classmethod
    def from_error(
        cls, command: str, *, baseline: bool, error: BaseException
    ) -> CommandCapability:
        """Build a failed probe without aborting the full traversal."""
        return cls(
            command=command,
            baseline=baseline,
            returncode=None,
            options=(),
            stdout="",
            stderr="",
            error=str(error),
        )

    @classmethod
    def from_skip(
        cls, command: str, *, baseline: bool, reason: str
    ) -> CommandCapability:
        """Build a result for an upstream help path that is not non-invasive."""
        return cls(
            command=command,
            baseline=baseline,
            returncode=None,
            options=(),
            stdout="",
            stderr="",
            skipped_reason=reason,
        )


@dataclass(frozen=True)
class CapabilityReport:
    """Comparison between the pinned source baseline and installed CLI root help."""

    baseline_commit: str
    baseline_commands: tuple[str, ...]
    advertised_commands: tuple[str, ...]
    baseline_not_advertised: tuple[str, ...]
    additional_commands: tuple[str, ...]
    probe_returncode: int
    baseline_groups: Mapping[str, tuple[str, ...]] = field(
        default_factory=lambda: UPSTREAM_COMMAND_GROUPS
    )

    @property
    def probe_ok(self) -> bool:
        """Return whether root help succeeded and advertised at least one command."""
        return self.probe_returncode == 0 and bool(self.advertised_commands)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable report."""
        return {
            "baseline_commit": self.baseline_commit,
            "baseline_commands": list(self.baseline_commands),
            "baseline_groups": {
                group: list(commands) for group, commands in self.baseline_groups.items()
            },
            "advertised_commands": list(self.advertised_commands),
            "baseline_not_advertised": list(self.baseline_not_advertised),
            "additional_commands": list(self.additional_commands),
            "probe_returncode": self.probe_returncode,
            "probe_ok": self.probe_ok,
        }

    @classmethod
    def from_probe(cls, stdout: str, stderr: str, returncode: int) -> CapabilityReport:
        """Build a report from FluidAudio's root help output."""
        advertised = (
            _parse_advertised_commands("\n".join((stdout, stderr)))
            if returncode == 0
            else set()
        )
        baseline = set(UPSTREAM_COMMANDS)
        comparison_available = bool(advertised)
        return cls(
            baseline_commit=UPSTREAM_BASELINE_COMMIT,
            baseline_commands=UPSTREAM_COMMANDS,
            advertised_commands=tuple(sorted(advertised)),
            baseline_not_advertised=(
                tuple(sorted(baseline - advertised)) if comparison_available else ()
            ),
            additional_commands=(
                tuple(sorted(advertised - baseline)) if comparison_available else ()
            ),
            probe_returncode=returncode,
        )


@dataclass(frozen=True)
class DeepCapabilityReport:
    """Per-command installed help results for the complete pinned baseline."""

    root: CapabilityReport
    commands: Mapping[str, CommandCapability]

    @property
    def probe_ok(self) -> bool:
        """Return whether root help and every attempted command help probe succeeded."""
        return (
            self.root.probe_returncode == 0
            and self.baseline_complete
            and not self.failed_baseline_commands
            and not self.failed_additional_commands
        )

    @property
    def baseline_complete(self) -> bool:
        """Return whether every pinned command has a result or intentional skip."""
        return set(self.root.baseline_commands).issubset(self.commands)

    @property
    def failed_baseline_commands(self) -> tuple[str, ...]:
        """Return pinned commands whose help probe failed."""
        return tuple(
            command
            for command, probe in self.commands.items()
            if probe.baseline and not probe.probe_ok and not probe.skipped
        )

    @property
    def failed_additional_commands(self) -> tuple[str, ...]:
        """Return newly advertised commands whose help probe failed."""
        return tuple(
            command
            for command, probe in self.commands.items()
            if not probe.baseline and not probe.probe_ok and not probe.skipped
        )

    @property
    def skipped_commands(self) -> tuple[str, ...]:
        """Return commands intentionally not invoked because help is unsafe."""
        return tuple(command for command, probe in self.commands.items() if probe.skipped)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable deep capability report."""
        return {
            "root": self.root.to_dict(),
            "probe_ok": self.probe_ok,
            "baseline_complete": self.baseline_complete,
            "failed_baseline_commands": list(self.failed_baseline_commands),
            "failed_additional_commands": list(self.failed_additional_commands),
            "skipped_commands": list(self.skipped_commands),
            "commands": {
                command: probe.to_dict() for command, probe in self.commands.items()
            },
        }


def _parse_advertised_commands(help_text: str) -> set[str]:
    commands: set[str] = set()
    in_commands = False
    for line in help_text.splitlines():
        stripped = line.strip()
        if line.rstrip().endswith("Commands:"):
            in_commands = True
            continue
        if in_commands and (stripped.startswith("Run '") or stripped == "Examples:"):
            break
        if not in_commands:
            continue
        match = _COMMAND_LINE.match(line)
        if match and match.group(1) != "help":
            commands.add(match.group(1))
    return commands
