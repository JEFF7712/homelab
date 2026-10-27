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
