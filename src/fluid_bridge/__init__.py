"""Unofficial Python bindings for FluidAudio's macOS CLI."""

from __future__ import annotations

from fluid_bridge.bridge import (
    CommandResult,
    DoctorReport,
    FluidAudioBridge,
    FluidAudioBridgeError,
    FluidAudioCLIConfig,
)
from fluid_bridge.capabilities import CapabilityReport
from fluid_bridge.streaming import StreamEvent, StreamingCommand

FluidCLI = FluidAudioBridge
__version__ = "0.1.0"

__all__ = [
    "CapabilityReport",
    "CommandResult",
    "DoctorReport",
    "FluidAudioBridge",
    "FluidAudioBridgeError",
    "FluidAudioCLIConfig",
    "FluidCLI",
    "StreamEvent",
    "StreamingCommand",
    "__version__",
]
