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

## Protected native CI operation

`native-asr-publish` is a manual Woodpecker operation in the protected homelab
repository, triggered from successful validation of current main. The trusted
deploy coordinator uses the existing fleet SSH identity. Application pipelines
do not receive its SSH or publisher credentials. The builder clones the exact
approved source, downloads retained packages with read-only credentials, checks
the model hash and compiles in a fresh container with networking disabled.
Build containers have no host Docker socket or credential mounts. Compilation
is limited to two CPUs and 8 GiB.

The coordinator reaches its own build host through the NAS SSH relay because
the host firewall excludes Docker bridge sources. Both SSH hops use the same
protected identity and pinned known-hosts file without agent forwarding.
Connection and keepalive timeouts bound unavailable-host failures.

Docker GPU checks run before publishing a `candidate-ci-*` tag. The coordinator
then runs the same corpus in an isolated k3s Job using the production NVIDIA
runtime. Only successful completion creates the final `0.0.N` release tag and
retention tag. Flux promotion requires a separate protected PR. The operation
receipt and k3s proof log are stored by the existing state gateway.

The short k3s probe shares the homelab-04 GPU with the primary recognizer, as the
operator's Docker acceptance tests do. It has no Kubernetes API token, reads
only the model and its own retained corpus, and never sends Home Assistant
requests. Failed probes remain for diagnosis and expire after one hour.

The first Docker-verified candidate from 2026-10-13 failed under k3s because
cudart also carried driver stubs outside the SDK's dedicated `-stubs` outputs.
The production pin was rolled back. Runtime packaging now removes every SDK
`libcuda.so*` entry, including merged symlinks, while retaining `libcudart.so`.
Docker CDI verification alone does not establish k3s runtime acceptance.

The 2026-10-13 recovery drill compiled the native library in a fresh Nix image,
verified its ABI, and recognized "Local recovery verification." from the
independently restored model with networking disabled. This evidence covers
the compiler and model recovery. Final image runtime and GPU acceptance remain
separate gates before replacing the production image.
