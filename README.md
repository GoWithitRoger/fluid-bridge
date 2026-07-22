<p align="center">
  <img src="assets/fluid-bridge-mark.svg" width="176" alt="fluid-bridge mark">
</p>

# fluid-bridge

A small, unofficial Python adapter for the
[FluidAudio](https://github.com/FluidInference/FluidAudio) macOS command-line interface.

It is a hobby project built to make FluidAudio easier to call from Python without embedding Swift
or Rust in the Python process. It is maintained on a best-effort basis and is not affiliated with
Fluid Inference.

The bridge stays deliberately thin: it runs the separate FluidAudio CLI, preserves its output and
exit status, and adds conservative Python and command-line helpers. See the
[capability matrix](docs/CAPABILITIES.md) and [validation guide](docs/VALIDATION.md) for the detailed
behavior.

## Important Prerequisite

`fluid-bridge` is not a built-in macOS speech feature and it does not contain FluidAudio itself.
macOS supplies the Core ML runtime and Apple Silicon hardware that can execute compatible models;
it does **not** ship the FluidAudio SDK, `fluidaudiocli`, or FluidAudio's model assets.

This package is a Python adapter around a separate FluidAudio CLI installation. Before it can run
transcription, diarization, VAD, text-to-speech, or voice cloning, provide FluidAudio by either
installing its CLI or pointing the bridge at a local FluidAudio checkout. The bridge never installs
FluidAudio. The external FluidAudio process, not this Python package, controls any model download
and cache behavior triggered by an inference command.

## Install

```bash
git clone https://github.com/GoWithitRoger/fluid-bridge.git
cd fluid-bridge
uv sync --all-extras --locked
```

### Provide FluidAudio

Use one of these setup paths for the separate FluidAudio dependency:

```bash
# Option 1: put a built FluidAudio CLI on PATH
fluidaudiocli --help

# Option 2: point fluid-bridge at a FluidAudio Swift package checkout
export FLUID_AUDIO_PACKAGE=/path/to/FluidAudio
swift run --package-path "$FLUID_AUDIO_PACKAGE" fluidaudiocli --help

# Option 3: provide an exact command
export FLUID_BRIDGE_CLI='swift run --package-path /path/to/FluidAudio fluidaudiocli'
```

`fluid-bridge doctor --probe` verifies this connection without loading a model. The first actual
inference command may cause FluidAudio to download and cache the selected third-party model assets;
consult FluidAudio's upstream documentation for model availability, storage, proxy, and offline
controls.

## Python Usage

```python
from fluid_bridge import FluidAudioBridge

bridge = FluidAudioBridge()

doctor = bridge.doctor(probe_cli=True)
print(doctor.to_dict())

transcript = bridge.transcribe(
    "meeting.wav",
    model_version="v3",
    streaming=True,
    language="en",
    output_json="transcript.json",
)
print(transcript.parsed_json)
print(transcript.artifacts["transcript"])

diarization = bridge.diarize(
    "meeting.wav",
    mode="offline",
    threshold=0.6,
    output_path="diarization.json",
    export_embeddings="embeddings.json",
)
print(diarization.parsed_json)

vad = bridge.vad_analyze(
    "meeting.wav",
    streaming=True,
    threshold=0.65,
    export_wav="speech.wav",
)
print(vad.stdout)

tts = bridge.tts("Hello from FluidAudio.", "out.wav", backend="kokoro-ane")
tts.raise_for_error()
```

`CommandResult.artifacts` contains only files produced by a successful command. When requested JSON
exists but cannot be decoded, `parsed_json` is `None`, `parse_error` describes the problem, and raw
stdout/stderr remain available.

For incremental Python output, start a streaming command and consume its stdout/stderr line events:

```python
running = bridge.stream(["parakeet-eou", "--input", "meeting.wav"])

for event in running:
    print(event.stream, event.text, end="")

result = running.wait()
result.raise_for_error()
```

Call `running.cancel()` from application control flow to stop early. `FluidAudioCLIConfig(timeout_s=...)`
applies one deadline across event iteration and `wait()`.

## CLI Usage

```bash
fluid-bridge doctor
fluid-bridge doctor --probe
fluid-bridge capabilities
fluid-bridge capabilities --deep
fluid-bridge capabilities --deep --include-additional
fluid-bridge transcribe meeting.wav --model-version v2
fluid-bridge diarize meeting.wav --mode offline --threshold 0.6
fluid-bridge vad meeting.wav --streaming --threshold 0.65
fluid-bridge tts "Hello from FluidAudio." --backend kokoro-ane --output out.wav
```

Friendly commands accept an upstream option tail after `--`. This keeps common options concise
while leaving every FluidAudio option available:

```bash
fluid-bridge transcribe meeting.wav --streaming --output-json result.json -- --custom-vocab terms.txt
fluid-bridge diarize meeting.wav --export-embeddings embeddings.json -- --num-speakers 3
fluid-bridge vad meeting.wav --export-wav speech.wav -- --min-silence-ms 400 --pad-ms 100
fluid-bridge tts "Hello" --backend pocket --clone-voice speaker.wav --output out.wav -- --temperature 0.7
```

Use `raw --` to run any upstream FluidAudio command with its arguments unchanged. This is the full
CLI compatibility path, including commands and options that do not yet have a friendly
`fluid-bridge` subcommand:

```bash
fluid-bridge raw -- parakeet-eou --input meeting.wav
fluid-bridge raw -- nemotron-transcribe --input meeting.wav --chunk-ms 160
fluid-bridge raw -- sortformer meeting.wav --offline --output speakers.json
fluid-bridge raw -- download --dataset ami-sdm
```

Raw mode preserves FluidAudio's stdout, stderr, and exit status. The Python equivalent is
`FluidAudioBridge.run(["command", "--option", "value"])`.

Add `--live` before the separator for interactive or long-running commands. Live mode inherits
stdin, stdout, and stderr so progress appears immediately, and it mirrors FluidAudio's signal exit:

```bash
fluid-bridge raw --live -- multi-stream microphone.wav system-audio.wav
```

The Python equivalent is `FluidAudioBridge.run_live(...)`.

`fluid-bridge capabilities` compares the installed CLI's root help with the command baseline audited
from FluidAudio commit `372eb32a`. FluidAudio's root help does not list every registered command, so
`baseline_not_advertised` means only that a command was absent from root help; it is not treated as
proof that the installed CLI cannot run it. `additional_commands` highlights newly advertised
upstream commands while raw mode keeps them immediately usable. If help cannot be parsed reliably,
`probe_ok` is false and both delta lists remain empty.

Add `--deep` to probe every known-safe command help surface and collect its installed long options,
raw help output, diagnostics, and exit status. Five pinned commands do not implement a safe
`command --help` path: `download`, `unified-benchmark`, `multi-stream`, `lseend`, and
`cohere-transcribe`. The report marks them as skipped instead of risking model, corpus, dataset, or
audio work. Their full argument surfaces remain available through raw mode.
Newly advertised commands are reported but not executed unless `--include-additional` is explicit,
because the bridge cannot yet know whether their help paths are free of side effects.

`doctor` reports platform, CLI discovery, Swift, and `xcode-select` state without running FluidAudio.
Add `--probe` (or `probe_cli=True` in Python) to execute root help and receive a readiness result plus
the exact command, exit status, stdout, stderr, and actionable findings. The probe recognizes common
Swift compiler/SDK incompatibility messages and keeps the original toolchain diagnostics intact.

## Relationship To FluidAudio

This project is an unofficial Python adapter. FluidAudio is upstream-owned by Fluid Inference and
has its own source, model, and third-party licensing. Core ML is Apple technology for packaging and
running compatible models; the models FluidAudio uses are not Apple-provided macOS speech models.
FluidAudio converts or integrates third-party open models for Core ML/ANE execution and may download
their assets on first use. See the upstream documentation for model provenance, registry, proxy, and
offline-mode controls. The source-backed [dependency research note](docs/RESEARCH-FLUIDAUDIO-DEPENDENCIES.md)
explains the relationship among macOS, FluidAudio, and `fluidaudio-rs`.

## Prior Art

Many projects use FluidAudio directly in apps and pipelines, including Senko and several macOS/iOS
dictation or meeting tools. `fluid-bridge` is narrower: a small reusable Python adapter around the
official FluidAudio CLI, not an application pipeline.

## Development

```bash
uv sync --all-extras --locked
uv run pytest
uv run ruff check .
uv run ty check
```

Default tests do not download FluidAudio models or run live inference.

### Live macOS Validation

Point the bridge at a real CLI or checkout, then explicitly enable the no-download live tier:

```bash
export FLUID_AUDIO_PACKAGE=/path/to/FluidAudio
FLUID_BRIDGE_LIVE=1 uv run pytest -m live -v
```

This runs root help and every source-audited safe command help path. Unsafe upstream help paths are
asserted as skipped. Model-backed smoke tests require both download consent and a capability-specific
input, so setting `FLUID_BRIDGE_LIVE=1` alone cannot start inference or download models:

```bash
export FLUID_BRIDGE_LIVE=1
export FLUID_BRIDGE_LIVE_ALLOW_DOWNLOADS=1
export FLUID_BRIDGE_LIVE_AUDIO=/absolute/path/to/short.wav
export FLUID_BRIDGE_LIVE_TTS=1
uv run pytest -m live_inference -v
```

Set `FLUID_BRIDGE_LIVE_VOICE=/absolute/path/to/reference.wav` to include PocketTTS voice cloning.
Use `FLUID_BRIDGE_LIVE_TIMEOUT` to change the per-command timeout from its 600-second default.

Dataset downloads and full benchmarks have no universal dry-run contract upstream. They are never
started by the live suite. Run them manually through raw mode only after choosing the dataset,
storage cost, model-download policy, and benchmark limits, for example:

```bash
fluid-bridge raw -- download --dataset ami-sdm
fluid-bridge raw -- asr-benchmark --subset test-clean --max-files 10
fluid-bridge raw -- diarization-benchmark --dataset ami-sdm --single-file ES2004a
fluid-bridge raw -- tts-benchmark --backend kokoro-ane --skip-asr
```
