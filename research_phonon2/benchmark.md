# Phonon-2 CPU benchmark, 2026-09-30

Phonon on homelab-05 is the best CPU candidate from this test. It approaches
the current GPU Nemotron primary's post-audio latency and substantially beats
the CPU Whisper fallback. The test does not establish a household accuracy
upgrade or complete Jarvis pipeline latency.

## Measured results

Post-audio latency is elapsed time from the benchmark client's end-of-audio
event to receiving the complete transcript, with audio replayed at real time.
It excludes wake detection, Home Assistant VAD, intent routing, and TTS.
Values below are medians of three warm sequential replays per recording.

| Backend | 2.10 s saved command | 2.085 s labeled speech |
|---|---:|---:|
| Phonon, homelab-04 CPU | 93 ms | 83 ms |
| Phonon, homelab-05 CPU | **53 ms** | **56 ms** |
| Current Nemotron, homelab-04 T1000 | 50 ms | 49 ms |
| Current Whisper base.en, homelab-04 CPU | 527 ms | 528 ms |

On the short saved command, Phonon on 05 is about ten times faster than the
Whisper fallback and essentially matches Nemotron. The measured difference
between Phonon and Nemotron is too small to justify a primary replacement
on speed alone. The deployed Wyoming backends include their speaker-ID path;
the isolated Phonon server has no speaker identification or Wyoming adapter,
so its results do not include the complete prospective replacement's overhead.

Buffered throughput is total recording duration divided by total elapsed
transcription time, excluding loading. This uses five repeats of each of six
speech-containing clips, 2.085 to 16 seconds long; it excludes the low-level
unlabeled clip and digital silence. The clips contain pauses, and this is a
small local throughput sample, not a recreation of the release benchmark.

| Backend | Buffered throughput |
|---|---:|
| Phonon, homelab-04 CPU | 23.8x real time |
| Phonon, homelab-05 CPU | **44.8x real time** |
| Current Nemotron, unpaced Wyoming replay | 24.3x real time |
| Current Whisper base.en, unpaced Wyoming replay | 5.6x real time |

For the nine-second microphone excerpt, buffered Phonon medians were 346 ms
on 04 and 184 ms on 05. This demonstrates why whole-recording throughput and
streaming post-audio latency lead to different rankings.

## Runtime and hardware

- homelab-04: live-confirmed i5-13600, 6 performance and 8 efficiency cores,
  AVX2 and AVX-VNNI. Runtime selected kernel ID 3.
- homelab-05: live-confirmed i5-11600, 6 cores / 12 threads, AVX-512 VNNI.
  Runtime selected kernel ID 4. Its advantage is consistent with the different
  native kernel, but this test does not isolate kernel effects from all other
  differences between the two machines.
- Both Phonon containers used six inference threads, a six-CPU quota and
  a 6 GiB memory limit, with no GPU. Model was loaded before measurements.
- Container: `ghcr.io/fermionresearch/phonon-cpu:2.0.3`, pinned observation
  `sha256:2f6af0539b2637d657fb7a6835e45862efe4eb319db9ec0c7d0397db3628017f`.
  Both servers reported package version `0.2.4` and the native C int8 encoder
  plus C TDT decoder, without a fallback-runtime error.
- Model archive: `98125795b6dda72f5c6eee9ba33d19815df65dcb18b50a357bf9f73c9935309e`.
  The runtime verified its archive and extracted members against published hashes.
- Observed Phonon process RSS was about 1.5 GiB on either host, substantially
  larger than the 164 MB download. Initial model/runtime loading took about
  21 seconds; a subsequent cached load on 04 reported 4.63 seconds.
- A follow-up pinned every Phonon thread on 04 to one logical CPU per performance
  core (`0,2,4,6,8,10`). It did not improve throughput or latency: the command's
  streaming median was 149 ms. This additional test was sequential and subject
  to background load; it is not proof that all affinity configurations are worse.

## Recognition and evidence limits

The labeled public sample is LibriSpeech `1089-134691-0000`, expected
`he could wait no longer`. Every backend transcribed it correctly. One labeled
sentence cannot establish comparative accuracy.

Three pre-existing recordings from homelab-05 (`/tmp/livetest*.wav`) were
copied into the private local corpus. Their capture provenance and spoken
labels were not independently established. Five fixed excerpts were extracted
without denoising, and a shorter command excerpt was subsequently selected.
Sources and excerpts are separately retained with SHA-256 hashes. Excerpts
are benchmark inputs, not acceptance recordings under the acoustic runbook.

For the short command, Phonon and Whisper consistently returned
`Hey Jarvis, turn on the lights.` Nemotron returned
`Hey Jarvis, turn on the light.` This difference is recorded but unscored.
On a separate low-level recording, Phonon produced words while Nemotron
returned nothing. Without a listening-verified label this cannot be classified
as either hallucination or improved quiet-speech recognition.

All backends returned empty text for digital silence. No microphone-to-action
test, noisy-room accuracy comparison, concurrent satellite load test, or
speaker-identification integration was performed.

## Reproduction and cleanup

Private artifacts are in `.agent-state/phonon-benchmark/`: immutable corpus
manifest and recordings, replay scripts, `turns.jsonl`, `summary.json`,
`validation.json`, runtime health, host metadata and baseline image identities.
The run contains 238 recorded turns, including the affinity follow-up.
Validation checks complete five-repeat buffered groups and three-repeat
paced comparisons. Initial Wyoming paced results used the existing helper;
the final short-recording comparisons use a follow-up with absolute pacing
deadlines and an exact-duration final chunk, matching the Phonon client.

Containers ran in a separate containerd namespace with loopback-only HTTP
servers, reached through temporary SSH tunnels. No deployment, gateway,
Home Assistant pipeline, or source configuration was changed. Temporary
servers and SSH tunnels were stopped. SIGTERM did not terminate either
Phonon server within the observed grace period; SIGKILL was required for
the two benchmark containers. Model caches and the downloaded image remain
available for subsequent testing. Current STT deployments were healthy at readback.

Recommendation: use homelab-05 for the next Phonon evaluation. Treat it as
a promising Whisper fallback replacement and a possible CPU alternative to
Nemotron. Require labeled household audio, speaker-ID integration, and verified
container shutdown behavior before promoting it into the active voice pipeline.

Repository formatting passed. The full documentation gate found an unrelated
broken `README.md#deployment` link in
`docs/runbooks/postgres-disaster-recovery.md:65`; those files were not changed
for this benchmark. The benchmark report's scoped whitespace check passed.

Runtime references: [CPU documentation](https://github.com/fermionresearch/phonon/blob/main/docs/cpu.md),
[server protocol](https://github.com/fermionresearch/phonon/blob/main/docs/server.md),
[model card](https://huggingface.co/FermionResearch/Phonon-2).
