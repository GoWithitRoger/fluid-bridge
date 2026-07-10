# fluid-bridge

Unofficial Python bindings for [FluidAudio](https://github.com/FluidInference/FluidAudio)'s
macOS command-line interface.

FluidAudio is a Swift SDK for local audio AI on Apple devices. Upstream FluidAudio includes
ASR/transcription, text-to-speech, voice activity detection, speaker diarization, speaker
embeddings/identification, offline and streaming modes, and ANE/CoreML execution.

`fluid-bridge` is intentionally thin: it lets Python projects call FluidAudio's official CLI without
embedding Swift or Rust in the Python runtime. The first public release focuses on CLI execution and
conservative result handling; future releases may add an optional Rust/PyO3 backend over
[`fluidaudio-rs`](https://github.com/FluidInference/fluidaudio-rs).

## Install

```bash
git clone https://github.com/GoWithitRoger/fluid-bridge.git
cd fluid-bridge
uv sync --all-extras
```

FluidAudio itself is not bundled. Use one of these setup paths:

```bash
# Option 1: put a built FluidAudio CLI on PATH
fluidaudiocli --help

# Option 2: point fluid-bridge at a FluidAudio Swift package checkout
export FLUID_AUDIO_PACKAGE=/path/to/FluidAudio
swift run --package-path "$FLUID_AUDIO_PACKAGE" fluidaudiocli --help

# Option 3: provide an exact command
export FLUID_BRIDGE_CLI='swift run --package-path /path/to/FluidAudio fluidaudiocli'
```

## Python Usage

```python
from fluid_bridge import FluidAudioBridge

bridge = FluidAudioBridge()

doctor = bridge.doctor()
print(doctor.to_dict())

transcript = bridge.transcribe("meeting.wav", model_version="v2")
print(transcript.stdout)

diarization = bridge.diarize("meeting.wav", mode="offline", threshold=0.6)
print(diarization.parsed_json)

vad = bridge.vad_analyze("meeting.wav", streaming=True, threshold=0.65)
print(vad.stdout)

tts = bridge.tts("Hello from FluidAudio.", "out.wav", backend="kokoro-ane")
tts.raise_for_error()
```

## CLI Usage

```bash
fluid-bridge doctor
fluid-bridge transcribe meeting.wav --model-version v2
fluid-bridge diarize meeting.wav --mode offline --threshold 0.6
fluid-bridge vad meeting.wav --streaming --threshold 0.65
fluid-bridge tts "Hello from FluidAudio." --backend kokoro-ane --output out.wav
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

## Relationship To FluidAudio

This project is an unofficial Python adapter. FluidAudio is upstream-owned by Fluid Inference and
has its own source, model, and third-party licensing. FluidAudio may download model assets on first
use; see upstream documentation for registry, proxy, and offline-mode controls.

## Prior Art

Many projects use FluidAudio directly in apps and pipelines, including Senko and several macOS/iOS
dictation or meeting tools. `fluid-bridge` is narrower: a small reusable Python adapter around the
official FluidAudio CLI, not an application pipeline.

## Development

```bash
uv sync --all-extras
uv run pytest
uv run ruff check .
```

Default tests do not download FluidAudio models or run live inference.
