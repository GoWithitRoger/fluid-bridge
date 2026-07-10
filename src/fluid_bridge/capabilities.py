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

_COMMAND_LINE = re.compile(r"^\s{2,}([a-z][a-z0-9-]*)\s{2,}\S")


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
