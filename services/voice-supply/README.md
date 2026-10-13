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

## Native ASR image

`native-inputs-manifest.json` retains the full SDK export and the exact NeMo
and ggml source archives in Forgejo. `native-wheels-package.json` retains the
four Python wheels, independently bound by `native-wheels.json`. Use the same
fetch helper to download each package into a new directory. Keep the SDK NAR
compressed until importing it into the disposable builder's own Nix store.

Prepare an input directory containing `nemotron-build-sdk.nar.gz`,
`nemotron-source.tar.gz` and `native-wheels/`. With the two digest-pinned base
images already present, run `python -m scripts.voice.build_image INPUTS TAG`.
The helper requires committed, unchanged image source, verifies every input
hash, and builds with no network or Docker layer cache. The recipe imports
the retained SDK, compiles for the homelab-04 GPU architecture and installs
Python packages without an index. It labels the result with the source SHA.
Runtime packaging removes the SDK's NVIDIA driver link stubs; real GPU checks
use the host driver. `--reuse-build-cache` is an explicit debugging option for
resuming packaging after a recorded cold compilation. It does not establish
a new cold build proof. The default remains an uncached build.

The 2026-10-13 recovery drill compiled the native library in a fresh Nix image,
verified its ABI, and recognized "Local recovery verification." from the
independently restored model with networking disabled. This evidence covers
the compiler and model recovery. Final image runtime and GPU acceptance remain
separate gates before replacing the production image.
