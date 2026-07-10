# FluidAudio macOS Capability Matrix

`fluid-bridge` provides complete transport access to the FluidAudio CLI audited at upstream commit
`372eb32a3b23342d11dca41ed75cd4d11d3f8955`. That snapshot registers 33 commands. Every command and
option is callable through `fluid-bridge raw -- ...` and `FluidAudioBridge.run(...)`.

## Runtime Prerequisite

The bridge is a Python adapter, not FluidAudio itself. macOS provides Core ML and compatible Apple
Silicon hardware, but it does not include FluidAudio, `fluidaudiocli`, or the third-party model assets
that FluidAudio runs. A caller must provide a built CLI on `PATH`, an explicit `FLUID_BRIDGE_CLI`
command, or a FluidAudio checkout through `FLUID_AUDIO_PACKAGE`. The bridge does not install the CLI
or download model assets; first inference is managed by the configured FluidAudio installation.

The bridge deliberately does not recreate all 33 upstream parsers. Four common workflows have a
curated stable interface; every uncommon, experimental, benchmark, dataset, and newly added option
continues through the lossless compatibility interface. Friendly commands also accept an unparsed
tail after `--`, and Python methods accept `extra_args`.

## Interface Levels

- **Stable**: curated Python method and `fluid-bridge` command, plus raw access.
- **Raw**: full argv passthrough with stdout, stderr, and exit status preserved.
- **Raw live**: passthrough with inherited terminal streams and signal mirroring.
- **Python stream**: incremental stdout/stderr events, cancellation, and one timeout deadline.

This is full FluidAudio **CLI** capability, not full Swift SDK binding coverage. SDK-only APIs,
in-process audio buffers, Swift protocols, and app lifecycle integrations are outside the current
product boundary. They should be added only for a demonstrated use case that the CLI cannot serve.

## ASR

| Upstream command | Interface | Capability and boundary |
| --- | --- | --- |
| `asr-benchmark` | Raw | LibriSpeech ASR benchmarks; dataset/model work is manual. |
| `unified-benchmark` | Raw | Unified batch/streaming benchmark; upstream `--help` starts work, so deep probe skips it. |
| `fleurs-benchmark` | Raw | Multilingual FLEURS evaluation. |
| `transcribe` | Stable | Batch/streaming transcription, model version, language, JSON artifact, and arbitrary extra options. |
| `multi-stream` | Raw live | Parallel source transcription; upstream consumes bare `--help` as audio, so deep probe skips it. |
| `parakeet-eou` | Raw / Python stream | Parakeet EOU streaming, model/chunk/benchmark controls, and incremental output. |
| `ctc-earnings-benchmark` | Raw | Earnings22 CTC keyword/timestamp benchmark. |
| `emission-delay-benchmark` | Raw | TDT-to-CTC emission-delay analysis. |
| `nemotron-benchmark` | Raw | Nemotron streaming benchmark. |
| `nemotron-transcribe` | Raw | Nemotron custom-file transcription. |
| `nemotron-multilingual-transcribe` | Raw | Multilingual Nemotron transcription, language/prompt/chunk/model controls. |
| `nemotron-multilingual-benchmark` | Raw | Multilingual benchmark across supported datasets. |
| `nemotron-multilingual-multi-stream-bench` | Raw | Concurrent multilingual manager benchmark. |
| `sensevoice-transcribe` | Raw | SenseVoice transcription and precision controls. |
| `sensevoice-benchmark` | Raw | SenseVoice multilingual benchmark. |
| `paraformer-transcribe` | Raw | Paraformer transcription and precision controls. |
| `ja-benchmark` | Raw | Japanese JSUT/Common Voice benchmark. |
| `cohere-transcribe` | Raw | Cohere model-directory and decoding controls; positional help handling is unsafe, so deep probe skips it. |
| `cohere-benchmark` | Raw | Cohere FLEURS/LibriSpeech benchmark and checkpoint controls. |

## Diarization

| Upstream command | Interface | Capability and boundary |
| --- | --- | --- |
| `diarization-benchmark` | Raw | Streaming/offline benchmark with clustering and post-processing options. |
| `process` | Stable | Streaming/offline diarization, JSON result, embeddings export, thresholds, and arbitrary extra options. |
| `sortformer` | Raw | Sortformer streaming/offline diarization and tuning controls. |
| `sortformer-benchmark` | Raw | AMI benchmark and model/tuning sweeps. |
| `lseend` | Raw | LS-EEND file diarization; positional help handling is unsafe, so deep probe skips it. |
| `lseend-benchmark` | Raw | LS-EEND AMI benchmark and post-processing controls. |

## Voice Activity Detection

| Upstream command | Interface | Capability and boundary |
| --- | --- | --- |
| `vad-analyze` | Stable | Batch/streaming segmentation, thresholds, compute units, tuning tail, and speech WAV export. |
| `vad-benchmark` | Raw | VAD datasets, thresholds, compute units, and result output. |

## Speech Generation And Corpora

| Upstream command | Interface | Capability and boundary |
| --- | --- | --- |
| `tts` | Stable | KokoroAne, PocketTTS, StyleTTS2, Supertonic-3, language, output, voice cloning, and arbitrary backend options. |
| `tts-asr-verify` | Raw | Batch TTS-to-ASR round-trip WER verification. |
| `tts-benchmark` | Raw | Latency, quality, compute-unit, and optional ASR verification benchmark. |
| `minimax-corpus` | Raw | Fetch and normalize the MiniMax multilingual TTS corpus. |
| `g2p-benchmark` | Raw | Multilingual grapheme-to-phoneme benchmark. |

## Datasets

| Upstream command | Interface | Capability and boundary |
| --- | --- | --- |
| `download` | Raw | Explicit FluidAudio dataset download. Upstream bare `--help` starts the default download, so deep probe never invokes it. |

## Option Coverage

Raw mode does no option parsing or allowlisting:

```bash
fluid-bridge raw -- COMMAND [every upstream positional, option, and flag]
```

The Python equivalent is:

```python
result = bridge.run(["COMMAND", "--upstream-option", "value"])
```

For a friendly command, append options the curated interface does not name:

```bash
fluid-bridge transcribe audio.wav -- --custom-vocab terms.txt --encoder-precision int8
```

```python
result = bridge.transcribe(
    "audio.wav",
    extra_args=["--custom-vocab", "terms.txt", "--encoder-precision", "int8"],
)
```

`fluid-bridge capabilities --deep` asks each source-audited safe installed command for help and
returns its discovered long options and raw diagnostics. This dynamic report is the option-drift
surface; a static Python copy of every option would go stale and create a second parser to maintain.

## Runtime Behavior

- `run()` captures complete stdout/stderr and preserves the upstream exit code.
- `run_live()` inherits terminal streams and mirrors signal termination.
- `stream()` yields tagged line events, supports cancellation, and terminates process groups.
- Stable output artifacts are reported only when a successful command creates or changes the file.
- JSON decode failures preserve process output and are exposed through `parse_error`.
- `doctor --probe` checks executable readiness without loading models.
- Deep capability and default automated tests never run source-audited unsafe help paths.

## Drift Policy

The pinned command baseline proves what was audited, while raw passthrough keeps future commands and
options immediately callable. After an upstream update:

1. Run `fluid-bridge capabilities` to inspect newly advertised commands.
2. Audit any new command's `--help` path for side effects before adding it to safe deep probing.
3. Run `fluid-bridge capabilities --deep` against the installed checkout.
4. Update the pinned baseline and this matrix in one reviewed change.

No absent root-help entry is treated as proof that a command is unsupported; upstream root help is
known to omit registered commands and may log through channels that are not captured as stdout.
