# CI flake evaluation investigation, 2026-09-27

The primary observed bottleneck is runner contention. Full flake evaluation
completed locally in 27.17 seconds with the evaluation cache disabled. No host
configuration or package-policy change was made during this investigation.

## Pipeline evidence

Pipeline [1376](https://gitlab.com/JEFF7712/homelab/-/pipelines/2887666217)
ran `flake_check` for 839.99 seconds, after 96.97 seconds in the queue.
Its trace separates that time into:

- Approximately 12 minutes evaluating the 12 NixOS configurations.
- Approximately 33 seconds evaluating the remaining flake outputs.
- Approximately 61 seconds building the repository-contract check.

The same pipeline's dedicated secret scan took 764.70 seconds. It scanned
152 commits and approximately 1.03 GB of history. Its repository tests failed
early on import ordering, so they did not reach their duplicate secret scan.

Pipeline [1379](https://gitlab.com/JEFF7712/homelab/-/pipelines/2887722787)
does reach that duplicate scan. Its repository-test trace completes offline
validation at 00:29:44 UTC, then starts Gitleaks at 00:30:06 UTC on September 28.
The dedicated secret scan is also running. These pipelines still use the
published configuration, rather than the uncommitted CI optimizations.

## Runner observations

Read-only inspection confirmed that `nas-ci` jobs run on `homelab-04`, an
i5-13600 with 20 hardware threads and approximately 31 GiB of RAM. Nix is
2.34.8, matching the local benchmark machine.

The runner service has an eight-core CPU quota and a 12 GiB memory limit.
Source configuration allows five quality jobs concurrently, plus one
privileged job. Three Gitleaks processes were observed, each reporting over
200% CPU utilization. The host load average reached 62.70.

Over a roughly five-second sample, the runner cgroup consumed 40.085 CPU
seconds. All 51 quota periods were throttled. Cgroup full CPU pressure was
approximately 59%, while memory pressure was zero at the initial observation.
The runner had previously reached its exact memory limit; `memory.events`
reported 8,798 limit events and no OOM kills. The cumulative memory events
cannot be attributed to this particular pipeline.

These are current observations, not measurements captured during pipeline
1376. They establish ongoing contention and support, but do not quantify,
its contribution to that older job's duration.

## Local evaluation measurements

The local machine is an i9-13900H with approximately 31 GiB of RAM. Input
sources were already available locally. This measures evaluation, excluding
downloads, checkout, development-shell startup, and check builds.

```sh
NIX_SHOW_STATS=1 nix flake check 'path:.?dir=flake' \
  --no-build --no-write-lock-file --option eval-cache false
```

The unprofiled run passed in 27.17 seconds, with 47.09 seconds of user CPU,
2.45 seconds of system CPU, and 4.49 GiB peak resident memory. Nix reported
1.834 seconds in garbage collection, approximately 3.9% of CPU time.

A preceding sampled-profile run passed in 26.32 seconds with 4.52 GiB peak
resident memory. Approximately 83% of sampled stacks included the NixOS module
merger. These inclusive percentages overlap and should not be summed.
The largest individual Nixpkgs top-level frame accounted for about 3.3% of
samples. Sharing package instances is therefore a secondary hypothesis,
rather than an established solution to the CI delay.

The separate host-derivation query successfully evaluated all 12 host outputs.
It overlapped the second benchmark for part of its execution, so its timing is
not a standalone performance comparison.

Temporary evidence files from this session are `/tmp/ci-flake-baseline.log`,
`/tmp/ci-flake-baseline.profile`, `/tmp/ci-flake-unprofiled.log`,
`/tmp/ci-flake-unprofiled-metrics.json`, and `/tmp/ci-flake-hosts-before.json`.
They are local, transient evidence and are not committed artifacts.

## Authorized publication and live verification

Commit `b796dbc89ddf4d62550597cfe43eeac1873a9605` publishes incremental secret
scanning, removes the duplicate repository scan, and makes reversible
validation jobs interruptible. The main and feature flake checks share the
`nix-flake-evaluation` resource group, preserving all 12 host evaluations.
Flake job logs now include elapsed time and evaluator statistics.

The clean checkout passed 895 tests, the non-flake repository gate, formatting,
ShellCheck, and the actual pushed commit-range secret scan. GitLab CI lint
reported no errors or warnings and confirmed that both flake jobs inherit the
same resource group. GitLab created that group with its default `unordered`
process mode. No runner configuration or host configuration was changed.

Pipeline [1380](https://gitlab.com/JEFF7712/homelab/-/pipelines/2887748511)
passed all 12 automated jobs in approximately four minutes (reported duration:
239 seconds). It remains in `manual` status because deployment jobs require
an operator trigger. A manual LedFx deployment also completed successfully;
it is excluded from the CI job comparisons below.

| Job | Previous measured duration | Pipeline 1380 |
| --- | --- | --- |
| Flake check | 839.99 seconds (pipeline 1376) | 93.57 seconds |
| Secret scan | 764.70 seconds (pipeline 1376) | 4.30 seconds |
| Repository tests | 1,584.91 seconds (pipeline 1378) | 118.30 seconds |
| Cache upload | Separate background job retained | 91.86 seconds |

The new flake trace begins evaluation at 00:45:15 UTC and begins running
checks at 00:45:44 UTC: approximately 29 seconds of evaluation. Check builds
then run until 00:46:42 UTC. The timed command reports 88.685 seconds elapsed,
50.766 seconds of user CPU, and 2.073 seconds of system CPU. The job duration
also includes checkout and development-shell startup. The new secret scan
checks one commit and approximately 7.29 KB in 27.7 milliseconds; job setup
accounts for most of its 4.30-second duration.

Two superseded validation jobs from pipeline 1379 were explicitly canceled
after publication because their old configuration did not mark them
interruptible. The older cache-upload job was retained. This is an observed
production before/after comparison, not a controlled repeated benchmark;
the individual contributions of incremental scanning, removed duplication,
cancellation, and serialization are not separately quantified.

Runner telemetry was sampled approximately every 21 seconds from 00:44:32
through 00:48:25 UTC. During that interval:

- Observed maximum cgroup memory use was 10.81 GiB, below the 12 GiB limit.
- The memory-limit event counter stayed at 8,798, with no new events or OOMs.
- Full CPU pressure's 10-second average fell from 58.67% to 0.00%.
- The final 60-second full CPU pressure average was 2.97%.
- The flake evaluator reached approximately 4.49 GiB resident memory in the
  sampled process listing. This is an observed RSS maximum, not a kernel
  per-process peak measurement.

Durable checkout-local evidence is under
`.agent-state/evidence/ci-performance/`: pipeline telemetry, flake and secret
job traces, the clean-checkout validation log, and the final pipeline summary.
That directory is ignored and is not published.

This run does not justify changing the runner concurrency or CPU quota.
Continue observing real workloads before further tuning. Do not split host
evaluation into many simultaneous jobs on the current constrained runner.
Any later package-sharing optimization should preserve every host toplevel
derivation path and retain the NVIDIA hosts' scoped `allowUnfree` policy.
