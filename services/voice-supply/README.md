# Retained voice supply

Chatterbox uses the existing digest-pinned UV/Python runtime and Forgejo
`JEFF7712/chatterbox-runtime-supply` version `20261013`. The manifest pins
all eight archive parts and the reconstructed archive. The wheel manifest
and requirements bind all 123 installed package versions by SHA-256.

Run `python -m scripts.voice.fetch_supply services/voice-supply/chatterbox-manifest.json /absolute/new/supply`
with a read-package token in `FORGEJO_PACKAGE_TOKEN`. Downloading is an
operator preparation step. The destination must be new. Copy the tracked
`chatterbox-requirements.txt` to `requirements.txt` in that directory and
make it readable by UID 1000. The deployed supply path is
`/persist/voice-models/chatterbox/supply-20261013` on homelab-05.

The runtime init container uses only these retained wheels, requires hashes,
disables Python downloads and network package resolution, and fails if the
pinned model snapshot or revision marker is absent. Model weights and the
voice reference are recovery state, not boot-time downloads. Restore them
before starting the service. The active model revision is
`749d1c1a46eb10492095d68fbcf55691ccf137cd`.

The 2026-10-13 drill installed a new virtual environment in a fresh container
with `--network=none`, an empty cache, the pinned UV image and retained wheels.
Torch 2.6.0, Chatterbox and Transformers imported successfully. All package
assets were downloaded from Forgejo and their hashes verified before preparing
the homelab-05 supply. The short-lived upload token was revoked and deleted.

Independent model archives are retained under
`/home/rupan/sovereign-recovery/voice/2026-10-13`. Their manifest binds archive
hashes, sizes and originating hosts. The native ASR build environment is
pinned by `flake#voice-asr`; its complete 210-path closure is retained as a
Nix export at the same recovery location. Native compilation and restored
model inference are separate acceptance tests and require their own receipts.
