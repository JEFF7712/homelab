# CI revalidation cache investigation

Observed on 2026-10-27 through the Woodpecker API. Durations below come
from workflow/step timestamps and `[EXECUTION BREAKDOWN]` in the validation
logs. These runs overlap other fleet activity, so they are a baseline,
not a controlled performance comparison.

| Pipeline | Creation to finish | Validation step | Full gate | Flake lane | Contract |
| --- | ---: | ---: | ---: | ---: | --- |
| [6031](https://ci.rupan.dev/repos/1/pipeline/6031) (main) | 578 s | 488 s | 461.3 s | 434.1 s | built |
| [6028](https://ci.rupan.dev/repos/1/pipeline/6028) (branch) | 525 s | 500 s | 470.4 s | 444.2 s | built |
| [6032](https://ci.rupan.dev/repos/1/pipeline/6032) (branch) | 398 s | 388 s | 362.6 s | 340.5 s | built |

In 6031, creation to workflow start was 29 s. Clone took 2 s. The first
timed command began 54.8 s after pipeline creation. Dependent plan/import
workflows finished about 59 s after validation. This separates queue and
bootstrap costs from the dominant flake lane.

The flake lane accounted for 89% of the validation step in 6031 and 6028.
The existing logs prove it rebuilt `repository-contract`, but do not time
that build separately from evaluation and dependency retrieval. No OOM
kills were reported in these runs. Concurrent checks share cgroup counters;
their CPU throttling totals cannot be added together.

## Source identity mismatch

Before the fix, `scripts/checks/flake.sh` passed `path:.?dir=flake` to
`nix-eval-jobs`. Cache publication uses `./flake`. On a clean checkout of
`4096d9179413f3ed355cf5862208bec0ba2a5e8b`, evaluating the same check with
these two references returned:

```text
./flake:
  /nix/store/s3brvmx0iamj1diin8lzp99y0zwb7awg-repository-contract.drv
path:.?dir=flake:
  /nix/store/d578h7dbiw8hnsqbn3hp45vgsr29129s-repository-contract.drv
```

Inspecting the derivations' build commands and source directories showed
`.git` only in the path-based source. The check interpolates the entire
repository root with `${../.}`, so that difference changes its derivation.
CI-generated ignored files also enter the path-based snapshot. Changing
Git metadata or job artifacts therefore defeats reuse even when tracked
source is identical, and publishing the Git-based result does not populate
the path-based request.

Reproduce without building the unit suite:

```sh
nix path-info --derivation ./flake#checks.x86_64-linux.repository-contract
nix path-info --derivation 'path:.?dir=flake#checks.x86_64-linux.repository-contract'
```

Validation now uses `./flake`. The regression fixture evaluates a tiny
dependency-free flake through the actual validation shell script: it must
match the canonical reference before and after an ignored timing report
and an empty Git commit, then change after a tracked source edit. It runs
where Nix is available and does not build any derivation.

## Scope and follow-up measurement

Every individual flake check now writes a named timing record through
`scripts.ci.run`, preserving failed exit codes. This exposes the contract
invocation separately from VM checks and topology in future validation
logs. It measures substitution, dependency work and check execution
together; host evaluation still overlaps builds.

The first instrumented run, [6046](https://ci.rupan.dev/repos/1/pipeline/6046),
passed. The gate took 589.0 s and its flake lane took 562.8 s. The
`repository-contract` invocation took 134.4 s; the libvirt normalization VM
check took 169.1 s and the Zot service VM check took 154.2 s. Contract
execution was therefore not most of this run's flake time. These are cold
check invocations with overlapping evaluation and other activity, not a
before/after speedup measurement.

Inspecting the cache targets against the evaluated flake checks found five
missing targets: `zot-registry-service`, `opnsense-types`, `opnsense-config`,
`fleet-inventory` and `kiosk-tty1`. Both `cache.sh` and `just cache-populate`
now include them, bringing publication to all twelve checks. Behavioral tests
compare the two publication entrypoints, and a real-Nix integration test
compares their check set to the evaluated flake outputs. The historical
17-target benchmark remains a measurement of its original workload; current
publication has 22 targets.

The change fixes cache identity; it does not guarantee a warm cache or
avoid rerunning tests after source changes. The latest maintenance run
inspected, [6021](https://ci.rupan.dev/repos/1/pipeline/6021), failed its
`cache-publish` workflow while building a fleet kernel-modules derivation,
before reaching publication. Failed cache publication is a separate limit
on output reuse. Sandbox jobs retain their existing credentials and store
isolation.

For a measured warm-run claim, first confirm successful cache publication
for the exact source, then compare validation on that source and inspect
the Nix log for substitution instead of `building ...repository-contract`.
Compare per-check timings as well as the complete workflow, retaining
queue/bootstrap measurements. No end-to-end speedup is claimed from the
baseline alone.

## Matched measurements, 2026-10-27

Two matched-source pairs observed **31.3% and 57.1% less full-validation
elapsed time**, respectively **1.45x and 2.33x speedup**:

| Pair | Old reference | Fixed reference | Time saved | Reduction |
| --- | ---: | ---: | ---: | ---: |
| Initial, old then fixed | 211.0 s | 145.1 s | 66.0 s | 31.3% |
| After recovery, fixed then old | 234.3 s | 100.6 s | 133.7 s | 57.1% |

The contract invocation fell from **140.8–153.3 s rebuilding to 0.17–0.37 s
reusing its result**. All twelve canonical check results were available in
these pairs, so they isolate the reference change rather than the additional
publication targets. The phases had different host load and cache-server
memory caps; each pair held its own server cap constant, and the pairs are
reported separately rather than pooled into an average.

The combined old-reference/old-target trial failed on the kernel-modules
error before the Zot VM check could execute. It cannot supply a valid
combined speedup percentage. These are shared-runner observations from two
successful pairs, not a statistically established production average.

### Method

- Source: `0aa3f79b6241b159ee313d433c373a382f24b9ed`, frozen in a clean,
  full Git clone before any benchmark documentation edits.
- Runner: homelab-04, using its CI Docker daemon and the same pinned image,
  `registry.rupan.dev/upstream/docker.io/nixos/nix@sha256:7a007c766426c1877758ddc5cb87a965ac131fc78c582ce0083d922d51ae945c`.
- Every validation used a fresh container, writable layer and Nix store.
  Limits matched an inspected Woodpecker job: two CPUs, 8 GiB RAM and
  16 GiB combined RAM/swap. No host Nix store, Docker socket, extra
  capabilities or production credentials were supplied to the containers.
- Each container copied the same read-only source into its workspace,
  entered `devShells.ci`, and ran the full validation command's three
  lanes: formatting, Jev and `scripts.ci.validate repository`. The full
  repository gate and every flake check had to pass. Bytecode writes were
  disabled, matching policy. The full gate was forced, and the secret scan
  used the same incremental parent range in every trial.
- An external wrapper changed only the evaluator's flake reference:
  `path:.?dir=flake#` for the old behavior or `./flake#` for the fix.
  Tracked source was never patched between trials.
- Ordinary dependencies retained the real Attic cache at priority 30 and
  the public cache at priority 40. A disposable HTTP binary cache at
  priority 50 supplied the exact-source check results. Its twelve outputs
  were built separately, signed with an ephemeral key, and verified before
  measurement. NarInfo fingerprints were checked before and after trials.
  TLS and NAR signature verification were not disabled.
- For the old-target trial only, a separate read-only cache view withheld
  the five newly added check-result NarInfos. Dependencies stayed warm.
  Production Attic returned 404 for the Zot service result, so it could not
  bypass that omission. Three cheap omitted checks were already present
  upstream; their existing hits remained available. No shared cache paths
  were deleted or hidden.

Job time includes container creation/start, source copy, shell resolution
and the complete validation command. It excludes webhook handling,
scheduler queue time, the clone plugin and downstream plan workflows.
The source clone and incremental scan range differ from a production
shallow clone, so absolute job times are not an end-to-end Woodpecker SLO.
The paired trials use identical clone contents and command inputs.
The controller polled completion every two seconds, adding a few seconds
of observation overhead to job time. Individual command timings use the
monotonic clock inside the job.

### Results and load event

| Trial | Reference | Available check results | Job seconds | Outcome |
| --- | --- | --- | ---: | --- |
| 1 | old path reference | all twelve | 211.0 | passed |
| 2 | fixed Git reference | all twelve | 145.1 | passed |
| 3 | fixed Git reference | all twelve | 243.6 | passed during host overload |
| 4 | old path reference | all twelve | 37.8 | deliberately stopped before completion |
| 5 | old path reference | five formerly omitted results withheld | 234.5 | failed on kernel-modules build |
| 6 | fixed Git reference | all twelve | 100.6 | passed after load recovery |
| 7 | old path reference | all twelve | 234.3 | passed after load recovery |

Trial 1's sampled host load ranged from 2.88 to 6.94; trial 2's ranged
from 6.58 to 11.08. During trial 3 it rose as high as 129.87, and SSH and
Docker calls stalled. The cause of this host-wide event was not established.
Trial 3 is retained as a completed but unmatched observation; it is not
pooled into the paired speedup. Trial 4 was stopped to reduce pressure;
its exit 137 was the explicit stop, with Docker reporting no OOM kill.
No trial reported a container OOM kill. Trial 5 failed with exit 1 on the
same `modules.cer` error as production publication. Its failed duration is
retained but is not counted as a successful baseline or as time saved.

The private cache server's memory cap was reduced from 8 GiB to 512 MiB
after the load event; its signing key, cache contents and output fingerprints
stayed unchanged. The old-target and subsequent fixed-reference trials
both use the reduced server cap. New trials resumed after short-term
memory pressure returned to zero. They are reported separately from the
initial pair, not averaged across that server change.
Trial 6's sampled host load ranged from 1.86 to 3.71 with no other Docker
CI jobs observed; trial 7's ranged from 1.64 to 5.40 with at most one other
CI container observed at a sample. These counts describe sampled containers,
not total host CPU or memory use.

### Production status and evidence

The live main validation for this same source,
[pipeline 6093](https://ci.rupan.dev/repos/1/pipeline/6093), passed with a
475 s validation workflow, a 449.8 s gate and a 424.7 s flake lane.
Its contract took 126.4 s, libvirt VM check 131.9 s and Zot VM check 125.7 s.
These check results were rebuilt, not reused.

The protected exact-source publication attempt,
[pipeline 6100](https://ci.rupan.dev/repos/1/pipeline/6100), failed before
uploading: the adguard-netbird-01 kernel-modules build tried to copy
`modules.cer` onto the same file. The earlier successful maintenance
publication, [6089](https://ci.rupan.dev/repos/1/pipeline/6089), built the
previous 17-target workload; it does not establish warm results for this
source and the new target set.

Consequently these are measured results with a seeded, signed output
cache, not a claim that production revalidation already realizes that
speedup. A production warm rerun remains contingent on successful
publication. Main advanced during this investigation, so its superseded
pipeline was not restarted.

Raw artifacts are under `artifacts/ci-revalidation-benchmark/` in the
measurement task's archived evidence. They include the frozen source,
controllers, seed program, public signing key, cache fingerprints, logs,
per-lane timing records, Docker limit receipts, load samples and every
trial outcome, including the interrupted trial. The private signing key
exists only in the disposable seed container and is destroyed with it.
