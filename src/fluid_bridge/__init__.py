"""Unofficial Python bindings for FluidAudio's macOS CLI."""

from __future__ import annotations

from fluid_bridge.bridge import (
    CommandResult,
    DoctorReport,
    FluidAudioBridge,
    FluidAudioBridgeError,
    FluidAudioCLIConfig,
)

FluidCLI = FluidAudioBridge
__version__ = "0.1.0"

__all__ = [
    "CommandResult",
    "DoctorReport",
    "FluidAudioBridge",
    "FluidAudioBridgeError",
    "FluidAudioCLIConfig",
    "FluidCLI",
    "__version__",
]

