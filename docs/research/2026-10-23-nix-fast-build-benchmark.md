# Fleet cache builder comparison, 2026-10-23

## Decision

Restore the native builder as the default in `scripts/ci/cache.sh`. Keep
`HOMELAB_CACHE_BUILDER=fast` as an explicit option in the operations shell.
The earlier local evaluation improvement did not establish a consistent
complete-job benefit on the actual CI runner. Keep the independent bounded
`nix-eval-jobs` evaluator used by the full validation gate.

## Workload and controls

- Source: `b56b321433f77697afacad0d077bdcfeb9c73678`, checked out cleanly once
  and mounted read-only at `/source` for every run.
- Runner: homelab-04, the actual Woodpecker operations host.
- Image: `registry.rupan.dev/upstream/docker.io/nixos/nix@sha256:7a007c766426c1877758ddc5cb87a965ac131fc78c582ce0083d922d51ae945c`,
  matching the deployed CI image observed on that host.
- Limits: two CPUs and 8589934592 bytes of memory per fresh Docker container,
  using the CI Docker daemon and its `ci.slice` parent.
- Every run started from the same image with a fresh writable layer, Nix
  store and cache directories. No host Nix store or Docker socket was mounted
  in the measurement container, and no production credentials were supplied.
- Both builders used the same 17 targets from `scripts/ci/cache.sh` and the
  same Nix configuration: two build jobs, two cores, the local Attic cache
  and its existing trusted key. Fast used two evaluator workers at 2048 MiB
  each, as configured by the pilot.
- All 17 target outputs were available from Attic at each completed-run check.
  Their NarInfo content fingerprints matched across all six runs and were
  rechecked before the following trial in each controller. Initial pre-run
  availability was not separately recorded. Both builders produced the same
  target derivations and output store paths.
- Uploads were disabled for every run. Measurements did not populate the
  shared cache with new outputs between trials.
- Run order: native, fast, fast, native, then an additional native/fast pair.

Build time covers the complete `cache.sh` build invocation, including its
evaluation, substitution/build and fast-result validation. Job time includes
container startup and `nix develop ./flake#ops` through completion of that
build invocation. Both exclude the later measurement-only identity checks,
cache publication and scheduler queue time. This is a fresh-store, warm
binary-cache workload, not a full compilation from an empty binary cache.

## Results

| Trial | Builder | Build seconds | Job seconds | Peak container GiB | Other container identities observed |
| --- | --- | ---: | ---: | ---: | ---: |
| 1 | native | 151.0 | 172.2 | 7.66 | 7 |
| 2 | fast | 159.4 | 186.9 | 7.65 | 2 |
| 3 | fast | 153.5 | 181.3 | 7.05 | 6 |
| 4 | native | 169.6 | 193.0 | 8.00 | 6 |
| 5 | native | 148.4 | 171.5 | 8.00 | 5 |
| 6 | fast | 185.5 | 208.1 | 8.00 | 14 |
| Mean | native | 156.3 | 178.9 | | |
| Mean | fast | 166.1 | 192.1 | | |

All six runs exited successfully, realized the same 17 targets and reported
zero container OOM events or OOM kills. Peak memory comes from the container's
cgroup `memory.peak`, including file cache, not the largest child process's
RSS. The last column counts distinct other container names seen during a
run; it is not maximum simultaneous concurrency or a CPU-load measurement.

Two fast runs exceeded the evaluator's combined 4096 MiB budget. The
evaluator killed and retried a worker at 4113 MiB in trial 2 and 4146 MiB in
trial 3. These were evaluator-managed retries, not container OOM failures.
Trial 6 recorded no such retry.

The first four trials averaged 182.6 seconds native versus 184.1 seconds
fast for the complete job, effectively a tie. Across all six, the observed
job means were 178.9 versus 192.1 seconds. Neither comparison demonstrates
a consistent complete-job improvement from enabling fast by default.

## Interpretation limits

Other CI jobs overlapped every run. The first four started when the runner
was idle, but new jobs arrived during execution. A later attempt to require
60 continuous idle seconds before each confirmation run also failed to
provide isolation: jobs arrived during the native confirmation. The final
fast confirmation therefore ran under recorded normal CI load without that
startup wait. No results were discarded.

Background CPU, storage and memory contention differed, so the observed
13.2-second difference between the six-run job means is not a causal slowdown
estimate. The shared cache was not globally snapshotted; its configuration
and all 17 target NarInfo fingerprints were checked, rather than every
possible intermediate dependency. Upload performance and uncached compilation
were not measured. These limits support the conservative decision to retain
the established native default, without claiming that fast is universally
slower or unsuitable for larger evaluation matrices.

## Evidence

The local task artifacts are under
`artifacts/nix-fast-build-benchmark/` in the benchmark worktree. They contain
the frozen checkout, controller and measurement programs, source archive,
six build logs, container limit/state receipts, timing and memory reports,
target derivations and output paths, and cache fingerprints. `results.json`
contains the six complete measurement records. These artifacts are ignored
by Git; this report records the durable protocol and results.
