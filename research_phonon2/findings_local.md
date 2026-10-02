Current source, inspected 2026-09-30:

- gitops/voice/gateway.yaml selects Nemotron then local Whisper.
- gitops/voice/stt-nemotron.yaml selects nemotron-speech-streaming-en-0.6b.q8_0.gguf on homelab-04.
- gitops/voice/whisper.yaml selects CPU Whisper base.en with an initial vocabulary prompt.
- HARDWARE.md records homelab-04 as i5-13600 with NVIDIA T1000 4 GB.
- Native bridge pushes audio chunks during speech and finalizes at audio-stop, concurrently with speaker identification. Its speaker prefix supports personalized music routing.
- Phonon's HTTP API can be adapted to Wyoming. Existing stt-groq.yaml demonstrates a buffered Wyoming-to-HTTP pattern, but is not directly configurable for Phonon and does not replace the speaker-ID contract.
- Official server documentation says prompt is accepted but unimplemented. Do not assume Whisper vocabulary hints carry over.
- No runtime benchmark or deployment change performed. kubectl unavailable in the current shell, so current live deployment was not reverified.

Sources: local files above; https://github.com/fermionresearch/phonon/blob/main/docs/server.md
