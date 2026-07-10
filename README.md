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
raw help output, diagnostics, and exit status. The pinned upstream `unified-benchmark` and `download`
commands do not implement non-invasive `--help`; the report marks them as skipped instead of risking
model, corpus, or dataset downloads. Their full argument surfaces remain available through raw mode.
Newly advertised commands are reported but not executed unless `--include-additional` is explicit,
because the bridge cannot yet know whether their help paths are free of side effects.

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
