# FluidAudio Dependency Research (for fluid-bridge)

Date researched: 2026-07-10

## Sources used
- [FluidAudio README](https://github.com/FluidInference/FluidAudio/blob/main/README.md)
- [FluidAudio Models documentation](https://github.com/FluidInference/FluidAudio/blob/main/Documentation/Models.md)
- [fluidinference/fluidaudio-rs README](https://raw.githubusercontent.com/FluidInference/fluidaudio-rs/main/README.md)
- [fluidaudio-rs `Cargo.toml`](https://raw.githubusercontent.com/FluidInference/fluidaudio-rs/main/Cargo.toml)
- [fluidaudio-rs `src/lib.rs`](https://raw.githubusercontent.com/FluidInference/fluidaudio-rs/main/src/lib.rs)
- [fluidaudio-rs `src/ffi/bridge.rs`](https://raw.githubusercontent.com/FluidInference/fluidaudio-rs/main/src/ffi/bridge.rs)
- [fluidaudio-rs `Package.swift`](https://github.com/FluidInference/fluidaudio-rs/blob/main/Package.swift)
- [fluidaudio-rs `build.rs`](https://raw.githubusercontent.com/FluidInference/fluidaudio-rs/main/build.rs)

## (1) Is FluidAudio built into macOS or a separate project? What does macOS/Core ML provide?
- [Verified Fact] `FluidAudio` is its own repository and Swift SDK, described as "a Swift SDK for fully local, low-latency audio AI on Apple devices" and instructs installation via Swift Package Manager dependency syntax in its README.
- [Verified Fact] The same README states models are "optimized for background processing ... by running inference on the ANE" and that it supports inference on Apple devices, which indicates it uses platform frameworks/hardware acceleration rather than macOS shipping a dedicated FluidAudio product.
- [Inference] Because it is an installable SDK and includes explicit model lists and registries, it is separate runtime/software that your app depends on, not a built-in macOS component.
- [Inference] “macOS/Core ML provide” in this context is the Core ML runtime and Apple Neural Engine execution path for running compatible models on-device; the FluidAudio code and model catalog are supplied by FluidAudio, not by macOS itself.

## (2) What exactly is `FluidInference/fluidaudio-rs`? CLI invocation or Rust inference?
- [Verified Fact] `fluidaudio-rs` is documented as Rust bindings for FluidAudio: "Rust bindings for [FluidAudio]" in README and `lib.rs`.
- [Verified Fact] `src/ffi/bridge.rs` declares `extern "C"` symbols and wraps them in a `FluidAudioBridge` type used by public APIs in `src/lib.rs`, showing Rust-to-native calls, not direct model execution in Rust tensors.
- [Verified Fact] `Package.swift` declares a `FluidAudioBridge` static library that depends on the `FluidAudio` Swift package. `build.rs` runs `swift build -c release` and links that library plus Apple frameworks (Foundation, AVFoundation, CoreML, Accelerate, Metal, etc.).
- [Verified Fact] `README` and `src/lib.rs` APIs expose `transcribe_file`, streaming methods, etc. through this bridge.
- [Inference] There is no evidence of spawning `fluidaudiocli` from inference paths; the Rust crate appears to use the Swift bridge API rather than CLI calls.

## (3) How does FluidAudio obtain/create Core ML models, versus Apple-provided models?
- [Verified Fact] `Models.md` explicitly lists each supported model and a "Model Sources" table pointing to `huggingface.co/FluidInference/*-coreml` repositories.
- [Verified Fact] The FluidAudio README notes that supported models are open-source and are "converted and optimized by our team".
- [Verified Fact] `Models.md` includes conversion notes such as `Scripts/convert_supertonic3_to_coreml.py`, indicating conversion/build tooling is part of their workflow for some pipelines.
- [Inference] This implies model artifacts come from FluidInference-curated/converted Core ML assets (typically from open-source upstream checkpoints) that are downloaded/managed by FluidAudio, rather than using any Apple-supplied ASR/TTS/VAD diarization model pack.

## Short product implication for the adapter
[Inference] The adapter should treat `fluidaudio-rs` as a platform-bound native bridge (a Rust façade over Swift/Core ML), with dependencies on Apple Silicon/macOS/iOS Core ML execution and FluidAudio model bootstrap/download behavior. This is a strong fit for macOS/iOS desktop apps, but it is not a cross-platform inference runtime and depends on external model package availability, caching, and Swift build/link behavior at install.
