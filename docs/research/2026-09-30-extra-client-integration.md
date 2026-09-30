# Antigravity CLI and Meta Muse Code integration

Observation date: 2026-09-30. These results describe the installed versions and
this source snapshot, not permanent compatibility with future releases.

## Before

The shared launcher supported Codex, Claude Code, Cursor, and OpenCode.
Antigravity CLI 1.2.5 and Meta Muse Code 1.0.2-R2040.1 were installed, but had no
homelab lifecycle registration. Antigravity's CLI executable is `agy`; the
`antigravity` executable is its desktop application. Muse is Meta Muse Code.

## Changes

- Added both clients to the explicit task launcher, context choices, doctor,
  changed-path routing, and installed-client smoke probes.
- Added an Antigravity plugin with PreInvocation, run_command PostToolUse, and
  Stop adapters. It translates camelCase input and injects shared context as an
  ephemeral message. Stop stays advisory and never restarts the model.
- Added a Muse native plugin with distinct SessionStart, PostToolUse,
  PostToolUseFailure, and Stop scripts. They find this repository from the
  runtime payload and call the existing shared hooks.
- Added a temporary helper on each launched client's PATH. Muse filters custom
  environment variables from hook subprocesses; the helper restores task scope
  and diagnostic flags without a shared active-task pointer. Antigravity also
  uses it to locate the repository when workspacePaths is empty.
- Installed the Antigravity bundle locally. Installed and approved Muse's
  project plugin and marked only this workspace trusted, preserving existing
  trust entries. No authentication, provider, model, Nix package, deployment,
  Git publication, or shared infrastructure settings were changed.
- Added regression tests for payload translation, advisory Stop, malformed
  input, filtered environments, concurrent launch isolation, helper cleanup,
  and Antigravity's empty workspace list. Native Muse scripts join ShellCheck.

## Installed-client observations

| Stage | Antigravity 1.2.5 | Muse Code 1.0.2 |
| --- | --- | --- |
| Plugin registration | `/hooks` lists all three actions | native validation succeeds, four capabilities approved |
| Task-scoped startup context | observed in a fresh provider session | observed in a fresh provider session |
| Advisory Stop | shared reminder receipt observed | shared reminder receipt observed |
| Shell exit 7 | wrapper reports the actual exit | wrapper reports the actual exit |
| Native shell failure receipt | PostToolUse had no error | no shell failure hook observed |
| Full integration smoke | failed at failure receipt gate | failed at failure receipt gate |

Antigravity's hook handler ran, but workspacePaths was empty despite the CLI
backend logging this repository as its workspace. A process trace established
the failed repository lookup; the scoped helper fixed it. Muse's cleared hook
environment explained why initial startup receipts lacked task selection.
Native plugin fixtures alone would have missed both problems.

Fresh provider sessions executed the verification command exactly once and
reported exit 7 in its structured output. Failure evidence remains available in
ignored `.agent-state/evidence/checks/`. The smoke gate intentionally remains
failed because wrapper evidence does not establish native failure delivery.
Use explicit `--record --task ID` when running verification through these clients
to save the observed result to the selected task. No numeric exit code is inferred
from a missing native result.

## Evidence and remaining gate

Local evidence is under `.agent-state/evidence/harness-followups/`,
`.agent-state/evidence/client-smoke/`, and
`.agent-state/evidence/hook-integration/`. It includes installed plugin validation,
approval, process traces, fresh smoke reports, and scoped check logs. These are
ignored local observations, not portable handoffs or proof of deployment.

Remaining acceptance is native shell failure delivery. Repeat the installed
smoke after a client upgrade or a supported runtime change; retain the explicit
verification wrapper until the actual receipt gate passes. Registration and
fixtures are insufficient to close this gate. The existing four clients retain
their adapters and selection behavior.

Primary contracts: [Antigravity hooks](https://www.antigravity.google/docs/hooks)
and [Muse hook events](https://meta-models.github.io/muse-code-sdk/next/guides/plugins/reference/hook-events/).
Muse's published next documentation targets a newer version than the installed
1.0.2 runtime; runtime observations above take precedence over assumed parity.
