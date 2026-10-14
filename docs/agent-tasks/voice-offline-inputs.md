# Agent Task: voice-offline-inputs

Status: `active`

Base commit: `e84fd4b54d8e6b4aea343eb922e16d02bff2c784`

Checkpoint HEAD: `950c45139d49d1c7b6372973d8d85ffa48031c10`

Owner: `codex`

Session: `sovereignty-closeout-20261013`

Exported at: `2026-10-14T00:39:32.554439+00:00`

Current HEAD at export: `950c45139d49d1c7b6372973d8d85ffa48031c10`

## Objective

Retain voice build and model inputs and prove offline recovery without adding external runtime dependencies

## Acceptance criteria

- [x] Chatterbox starts with retained pinned packages and models without external downloads (PR199 merged, live deployment1/1 and HTTPhealth200. Exact123 wheels retained and hash verified on homelab05. Fresh networknone venv installed and imported all required runtime libraries.)
- [x] Voice model and reference inputs survive an independent restore and inference works offline (Off-host archives and manifest verified. Nemotron restored GGUF hash matches documented d9a01898. All ten Turbo input files and pinned HFref restored. Actual source engine generated99840 bytes24kHzPCM onCPU withnetworknone from restored model and freshly installedvenv; receipt at /home/rupan/sovereign-recovery/voice/2026-10-13/chatterbox-inference-receipt.json.)
- [x] Native ASR source and complete toolchain are retained and compile on a clean offline builder (Pinned flake#voice-asr profile4ac6v7g, full210-path Nix export retained off-host and in Forgejo nemotron-build-inputs/20261013. Allnine package assets downloaded and hash verified. Fresh Nixcontainer networknone, nohoststore, compiled native library9e132625 fromNeMo07003daa/ggmlc03b4e2. Runtime packaging and CIproof remain separately pending.)
- [ ] Final native image passes isolated model/corpus and GPU verification before protected promotion, then live runtime is accepted (DockerCDI accepted206f butk3sfailed; rolledback3e26. Correcteddriver-removal source and newcandidate-firstCIgate pendingliveexecution, blockedonhost04.)

## Owned source

- `flake/flake.nix`
- `gitops/voice/chatterbox.yaml`
- `scripts/voice/fetch_supply.py`
- `services/voice-supply/`
- `tests/test_voice_supply.py`
- `scripts/voice/build_image.py`

## Remaining work

- Merge/deploy PR206 after live protected validation
- Run native-asr-publish and verify actual coldCIbuild plus isolatedk3s GPUJob before finalrelease tag
- Protectedpromotion andlivegateway acceptance
- Replace diagnostic206f recovery archive with acceptedfinalartifact archive

## Verification

Current source fingerprint at export: `b97e5dc9576efc70a8ba3a10bdfff42137ec0156d23e1a837c20bf61f9484deb`

Freshness describes source identity at export time; it does not establish live infrastructure health.

- None recorded

## Next action

Host04 recovered and all five nodes Ready, voice backends/gateways healthy on rollback3e26. PR206 head527843ee fixes driver stubs and k3s address, focused46 tests and full offline subprocess passed, formatting passed. Await protected CI, merge, deploy policy only (dry-activate restarts only woodpecker-policy), run cold native-asr-publish and k3s gate. Bookshelf remains deferred.

## Uncommitted work

This handoff does not contain uncommitted file content. Recover it from the original checkout, or create and transfer a reviewed patch before moving to another machine.
