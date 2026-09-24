# Nemotron ASR Model and Runtime Contract

## Purpose
This document defines the interface, packaging, and validation contract for the Nemotron speech recognition bridge and its underlying native inference runtime.

## Audio Interface Specification
- **Encoding**: 16-bit signed integer Linear PCM (`width=2`), little-endian
- **Sample Rate**: 16,000 Hz (`rate=16000`)
- **Channels**: 1 (mono, `channels=1`)
- **Streaming Chunks**: Arbitrary byte length, pushed sequentially between `audio-start` and `audio-stop`.

## Model Specification
- **Model Architecture**: GGUF weights consumed by the pinned NVIDIA NeMo-Speech.cpp runtime.
- **Artifact Name**: `nemotron-speech-streaming-en-0.6b.q8_0.gguf`
- **Expected Path**: Mounted at `/models/nemotron-speech-streaming-en-0.6b.q8_0.gguf`
- **Model Revision**: Q8_0 quantization; model hash observed on homelab-04 on 2026-09-23.
- **Expected SHA256**: `d9a01898d2a611c8764e23a1c2f45e70bbd5a425dc4de93692ac951dd603812d` (validated on disk if present).

## Native Shared Library ABI Contract
The C ABI is built from NVIDIA/NeMo-Speech.cpp revision `07003daa7eefea542076310722ccaa89709ee3c3` (`src/asr/c_api.cpp`), including its pinned ggml submodule and upstream patches, and installed into `/opt/nemo/lib64/libnemo_speech_asr_c.so`. It must export the following symbols:
- `int nemo_speech_asr_create(const void* config_ptr, void** recognizer_out)`
- `void nemo_speech_asr_destroy(void* recognizer)`
- `nemo_speech_asr_recognition_options nemo_speech_asr_recognition_options_default(void)`
- `int nemo_speech_asr_streaming_recognize(void* recognizer, const void* options_ptr, void** stream_out)`
- `int nemo_speech_asr_stream_push_f32(void* stream_ptr, const float* samples, size_t count, int32_t sample_rate)`
- `int nemo_speech_asr_stream_finish(void* stream_ptr)`
- `int nemo_speech_asr_stream_next(void* stream_ptr, void** result_out)`
- `void nemo_speech_asr_stream_close(void* stream_ptr)`
- `const char* nemo_speech_asr_result_transcript(const void* result_ptr, size_t index)`
- `bool nemo_speech_asr_result_is_final(const void* result_ptr)`
- `float nemo_speech_asr_result_audio_processed(const void* result_ptr)`
- `void nemo_speech_asr_result_destroy(void* result_ptr)`
- `const char* nemo_speech_asr_last_error(void)`

Return code contract:
- Status code `0` indicates success (`OK`).
- Non-zero status indicates an error; `nemo_speech_asr_last_error` returns a descriptive string.

## Speaker Identification Contract
- **Python Module**: `proxy.py` packaged directly in `/app/proxy.py`.
- **Model**: `3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx`
- **Profiles**: `/etc/voice-id/profiles.json`
- **Output**: Cosine distance similarity match. If top score >= threshold and margin >= min_margin, formats `speaker <Name> <transcript>`. Otherwise outputs bare `<transcript>`.

## Packaging and Build Verification

The Dockerfile compiles the actual upstream runtime with CUDA for SM75 (T1000),
installs its dependent libraries, and packages the bridge and speaker-ID module.
Python versions are direct dependency pins, not a complete transitive lock.
The build runs `verify_contract.py --abi-only`: library loading with NVIDIA's driver link stub, exported symbols,
and Python imports. The driver stub is mounted only for that build step and is
absent from the runtime image; runtime checks require the real NVIDIA driver. This does not claim speech accuracy or model inference.

Recognition verification is separate and requires a mounted model and labeled
WAV manifest. Run `verify_contract.py --manifest /corpus/manifest.json` with
`NEMO_LIB` and `NEMO_MODEL` set. Missing libraries, model, or corpus fail closed.
The manifest uses `cases` with `id`, `recording`, and `expected`; it must include
silence and two distinct utterances. Audio is PCM16 mono at 16 kHz. Verification
uses the production bridge and checks normalized transcripts against the labels.

The previous local C implementation was removed: it returned hint phrases or
hardcoded text without inference. Its passing tests were not recognition evidence.
Do not replace the deployed runtime until the new image passes the model/corpus
verification. Physical QuadCast acceptance remains a separate gate.
