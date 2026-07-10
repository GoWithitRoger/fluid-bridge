"""Command-line entrypoint for fluid-bridge."""

from __future__ import annotations

import argparse
import json
import signal
import sys
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path

from fluid_bridge.bridge import FluidAudioBridge, FluidAudioBridgeError

_FRIENDLY_COMMANDS = frozenset({"transcribe", "diarize", "vad", "tts"})


def main(argv: Sequence[str] | None = None) -> int:
    """Run the fluid-bridge CLI."""
    parser = _build_parser()
    parse_argv, upstream_args = _split_upstream_args(
        list(argv) if argv is not None else sys.argv[1:]
    )
    args = parser.parse_args(parse_argv)
    bridge = FluidAudioBridge()

    try:
        if args.command == "doctor":
            print(json.dumps(bridge.doctor().to_dict(), indent=2, sort_keys=True))
            return 0
        if args.command == "capabilities":
            report = (
                bridge.deep_capabilities(include_additional=args.include_additional)
                if args.deep
                else bridge.capabilities()
            )
            print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
            return 0 if report.probe_ok else 1
        if args.command == "raw":
            raw_args = args.args[1:] if args.args[:1] == ["--"] else args.args
            result = bridge.run_live(raw_args) if args.live else bridge.run(raw_args)
            if result.stderr:
                sys.stderr.write(result.stderr)
            if result.stdout:
                sys.stdout.write(result.stdout)
            if args.live and result.returncode < 0:
                signal_number = -result.returncode
                if signal_number not in signal.valid_signals():
                    return 128 + signal_number
                with suppress(OSError, RuntimeError, ValueError):
                    signal.signal(signal_number, signal.SIG_DFL)
                signal.raise_signal(signal_number)
            return result.returncode
        elif args.command == "transcribe":
            result = bridge.transcribe(
                args.audio,
                model_version=args.model_version,
                streaming=args.streaming,
                language=args.language,
                output_json=args.output_json,
                extra_args=upstream_args,
            )
        elif args.command == "diarize":
            result = bridge.diarize(
                args.audio,
                mode=args.mode,
                threshold=args.threshold,
                output_path=args.output,
                export_embeddings=args.export_embeddings,
                extra_args=upstream_args,
            )
        elif args.command == "vad":
            result = bridge.vad_analyze(
                args.audio,
                streaming=args.streaming,
                threshold=args.threshold,
                export_wav=args.export_wav,
                extra_args=upstream_args,
            )
        elif args.command == "tts":
            result = bridge.tts(
                args.text,
                args.output,
                backend=args.backend,
                language=args.language,
                clone_voice=args.clone_voice,
                extra_args=upstream_args,
            )
        else:
            parser.print_help()
            return 2
    except FluidAudioBridgeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if result.stderr:
        print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")
    if result.parsed_json is not None:
        print(json.dumps(result.parsed_json, indent=2, sort_keys=True))
    elif result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    return result.returncode


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fluid-bridge",
        description="Unofficial Python adapter for FluidAudio's macOS CLI.",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("doctor", help="Report FluidAudio CLI setup status.")
    capabilities = subparsers.add_parser(
        "capabilities", help="Compare installed commands with the baseline."
    )
    capabilities.add_argument(
        "--deep", action="store_true", help="Probe every known-safe baseline command help."
    )
    capabilities.add_argument(
        "--include-additional",
        action="store_true",
        help="Also probe newly advertised, untrusted commands (may have side effects).",
    )

    raw = subparsers.add_parser("raw", help="Run any FluidAudio CLI command.")
    raw.add_argument("--live", action="store_true", help="Inherit terminal input and output.")
    raw.add_argument("args", nargs=argparse.REMAINDER)

    transcribe = subparsers.add_parser("transcribe", help="Run FluidAudio batch transcription.")
    transcribe.add_argument("audio", type=Path)
    transcribe.add_argument("--model-version")
    transcribe.add_argument("--streaming", action="store_true")
    transcribe.add_argument("--language")
    transcribe.add_argument("--output-json", type=Path)

    diarize = subparsers.add_parser("diarize", help="Run FluidAudio diarization via process.")
    diarize.add_argument("audio", type=Path)
    diarize.add_argument("--mode", choices=["streaming", "offline"])
    diarize.add_argument("--threshold", type=float)
    diarize.add_argument("--output", type=Path)
    diarize.add_argument("--export-embeddings", type=Path)

    vad = subparsers.add_parser("vad", help="Run FluidAudio VAD analysis.")
    vad.add_argument("audio", type=Path)
    vad.add_argument("--streaming", action="store_true")
    vad.add_argument("--threshold", type=float)
    vad.add_argument("--export-wav", type=Path)

    tts = subparsers.add_parser("tts", help="Run FluidAudio text-to-speech.")
    tts.add_argument("text")
    tts.add_argument("--output", required=True, type=Path)
    tts.add_argument("--backend")
    tts.add_argument("--language")
    tts.add_argument("--clone-voice", type=Path)

    return parser


def _split_upstream_args(argv: list[str]) -> tuple[list[str], list[str]]:
    if not argv or argv[0] not in _FRIENDLY_COMMANDS or "--" not in argv:
        return argv, []
    separator = argv.index("--")
    return argv[:separator], argv[separator + 1 :]


if __name__ == "__main__":
    raise SystemExit(main())
