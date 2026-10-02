# Phonon-2 release findings, 2026-09-30

Phonon-2 is a feasible English STT candidate, with Linux CPU and NVIDIA container runtimes. Integration and improvement remain separate questions. No runtime installation or infrastructure change was performed.

## Accuracy evidence

The official model card identifies a quantized derivative of NVIDIA Parakeet TDT 0.6B v3, English only, CC-BY-4.0 weights and Apache-2.0 code. It reports these average word error rates across seven English Open ASR datasets:

| Model | Average WER |
|---|---:|
| Phonon-2 | 5.21% |
| Parakeet TDT v3 teacher | 4.96% |
| Whisper large-v3-turbo | 6.58% |
| Nemotron 3.5 ASR Streaming 0.6B | 7.96% |

Those seven datasets are LibriSpeech clean/other, AMI, Earnings-22, GigaSpeech, SPGISpeech and VoxPopuli. Phonon-2 loses to its teacher on five sets and improves on meetings and parliamentary speech. The post's 99.8% retained word accuracy refers to accuracy near 95%, and does not mean equal WER: 5.21 versus 4.96 is about 5% relatively more errors. Its Whisper comparison explicitly names large-v3-turbo in the card. The published Nemotron row is not proof about our native Q8 deployment or household command audio. [Official model card](https://huggingface.co/FermionResearch/Phonon-2)

## Runtime and resource requirements

164 MB is the compressed download, not runtime memory. Release documentation states GPU engines expand the encoder to 16-bit dense weights at load; CPU requantizes to 8-bit rows and uses a C thread pool with AVX2, AVX-512 VNNI or Arm NEON. Its claimed speed excludes loading. Eight Zen 5 cores reach 142.8 times realtime; M5 GPU reaches 174; H100 465 for one stream and 6,680 at batch 128. These are different hardware and throughput conditions from our i5/T1000 and do not establish final command latency. [Official release](https://www.fermionresearch.com/research/phonon-2/)

Linux x86-64 AVX2 and Arm NEON CPUs are supported, so the i5-13600 is a practical CPU test target. CPU container is amd64/arm64 and exposes transcription, streaming and health endpoints. Current README pins CPU 2.0.3 and CUDA 1.0.4. [CPU docs](https://github.com/fermionresearch/phonon/blob/main/docs/cpu.md), [repository](https://github.com/fermionresearch/phonon)

CUDA source describes a 1,208 MB dense encoder versus optional 302 MB packed encoder, excluding activations, decoder, CUDA context and graph/workspace overhead. The current Dockerfile uses Torch 2.9.1/CUDA 12.8 and defaults `PHONON2_CUDA_DTYPE=bfloat16`; engine honors an environment override and shows no automatic GPU capability fallback. [Dockerfile](https://github.com/fermionresearch/phonon/blob/main/docker-phonon2/Dockerfile), [engine](https://github.com/fermionresearch/phonon/blob/main/docker-phonon2/phonon2_cuda_engine.py)

NVIDIA lists T1000 at compute capability 7.5. Inference: stock BF16 is a compatibility concern on that older GPU; testing dense `float16` would be necessary before claiming compatibility. No T1000 benchmark or tested support was found. CPU is the cleaner first evaluation path. [NVIDIA capability table](https://developer.nvidia.com/cuda/gpus)

## Interface and streaming limits

HTTP is OpenAI-style multipart `POST /v1/audio/transcriptions`, with JSON `text` response. Audio model input is 16 kHz English. Generic server docs accept WAV/FLAC/OGG/AIFF with resampling, while CUDA container docs impose 16 kHz, so send our existing 16 kHz PCM as WAV. `prompt` is accepted but unimplemented, so it cannot provide entity-name word boosting. Timestamps and language selection are also unimplemented. Body cap is 32 MB; chunked uploads unsupported. [Server docs](https://github.com/fermionresearch/phonon/blob/main/docs/server.md), [CUDA docs](https://github.com/fermionresearch/phonon/blob/main/docs/cuda.md)

WebSocket `/v1/audio/stream` consumes raw mono PCM float32/int16 at 16 kHz. First partial after roughly 350 ms of speech, then every 500 ms of audio; final after 700 ms silence or 30 s cap. One active stream per worker. Its implementation repeatedly decodes the accumulating segment rather than retaining a native incremental encoder state, so streaming capability is not equivalent to our Nemotron streaming architecture. [Streaming implementation](https://github.com/fermionresearch/phonon/blob/main/docker-phonon2/_live.py)
