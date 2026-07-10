# Validation Guide

The validation strategy has three tiers. The first is always safe and local; later tiers require an
actual macOS FluidAudio installation and, for inference, explicit consent to model downloads.

## Tier 1: Automated Adapter Tests

```bash
uv run pytest
uv run ruff check .
```

These tests use fake subprocesses. They cover discovery precedence, raw and live passthrough,
signals, streaming, cancellation, timeouts, all 33 pinned command records, deep option parsing,
doctor diagnostics, stable workflow argv, JSON results, and artifact lifecycle. They do not invoke
FluidAudio or download models.

## Tier 2: Real CLI, No Downloads

Configure one real execution path:

```bash
export FLUID_AUDIO_PACKAGE=/path/to/FluidAudio
# or: export FLUID_BRIDGE_CLI='swift run --package-path /path/to/FluidAudio fluidaudiocli'
```

Then run only the `live` marker:

```bash
FLUID_BRIDGE_LIVE=1 uv run pytest -m live -v
```

This verifies macOS readiness, root help, every pinned safe command help path, the complete baseline,
and the audited skip set. It does not select `live_inference` tests. At the pinned upstream commit,
the skip set is `download`, `unified-benchmark`, `multi-stream`, `lseend`, and `cohere-transcribe`.

## Tier 3: Model-Backed Inference

Inference is selected separately and requires download consent:

```bash
export FLUID_BRIDGE_LIVE=1
export FLUID_BRIDGE_LIVE_ALLOW_DOWNLOADS=1
export FLUID_BRIDGE_LIVE_AUDIO=/absolute/path/to/short.wav
export FLUID_BRIDGE_LIVE_TTS=1
uv run pytest -m live_inference -v
```

The audio file enables ASR, diarization, and VAD smoke tests. `FLUID_BRIDGE_LIVE_TTS=1` enables TTS.
Add `FLUID_BRIDGE_LIVE_VOICE=/absolute/path/to/reference.wav` for PocketTTS voice cloning. The suite
uses temporary output paths and verifies parsed results and produced artifacts.

`FLUID_BRIDGE_LIVE_TIMEOUT` sets a positive per-command timeout in seconds; the default is 600.

## Datasets And Benchmarks

FluidAudio dataset and benchmark commands can consume substantial bandwidth, storage, memory, and
time. They do not share a universal dry-run interface, so they are manual validation gates:

```bash
fluid-bridge raw -- download --dataset ami-sdm
fluid-bridge raw -- asr-benchmark --subset test-clean --max-files 10
fluid-bridge raw -- diarization-benchmark --dataset ami-sdm --single-file ES2004a
fluid-bridge raw -- vad-benchmark --num-files 10
fluid-bridge raw -- tts-benchmark --backend kokoro-ane --skip-asr
```

Choose explicit datasets and file limits. Review upstream storage/model requirements before running
them. Raw mode preserves their progress output, diagnostics, and exit status.

## Release Checks

Before publishing:

```bash
uv run pytest
uv run ruff check .
uv build
```

Run Tier 2 on macOS. Run only the Tier 3 capabilities for which explicit test inputs and download
consent are available. Record skipped environmental validation honestly; do not convert it into a
mocked success claim.
