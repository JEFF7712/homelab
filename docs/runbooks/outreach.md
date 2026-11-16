# Outreach delivery

The authoritative sender and queue live in `gitops/outreach`. Agents still work
in `/home/rupan/projects/job/outreach` on the laptop and call `bin/outreach`.
That file is the installed copy of `gitops/outreach/app/client.py`. Update the
client copy when changing its protocol. There is no local sending fallback.

The control Deployment accepts commands through authenticated Kubernetes exec,
with no Service, public endpoint, or mounted Kubernetes token. The laptop's
`~/.config/outreach/remote.json` selects the existing Kubernetes context and
kubectl command. The sender CronJob runs every minute independently of the
laptop. Both serialize workspace access with NFS-supported file locks.

Mail egress adds public TCP 465 and 993 for the outreach pods to the existing
automation namespace policy. DNS uses the namespace's existing DNS rules.
Do not add an application DNS proxy rule without verifying DNS resolution from
the actual pods. Gmail connections retain TLS verification and bounded timeouts.

## State and workflow

`outreach-state` in namespace `automation` is an NFS PVC retained when Flux
prunes the application. `/state/outreach` holds the tracker, queue, resumes,
and successful-run timestamp. Bootstrap imports the existing tracker and queue
once and refuses to overwrite an initialized store. Gmail-native scheduled
messages remain owned by Gmail and are not imported into the custom queue.

Before a batch, run `bin/outreach sync --pull`, then inspect the local tracker
and Gmail. The local workbook is a mirror. Edits made during mailbox
reconciliation are uploaded with the next command using a SHA-256 comparison
against the last downloaded version. Concurrent remote changes reject the
upload instead of overwriting newer send receipts. `sync --pull` refreshes the
mirror and preserves replaced local workbooks in
`queue/local-tracker-backups/<sha256>.xlsx`. Reconcile preserved edits before
retrying a conflict. Close LibreOffice before syncing.

Queue, lint, render and doctor upload the selected local PDF attachments.
The client rejects a PDF that differs from its local validated build. Queued
Gmail drafts already contain their PDF, so sending does not need laptop files.
Render writes returned previews to the requested laptop directory. If the
connection is lost during queueing, check status and Gmail before retrying.

The engine persists the attempt Message-ID before SMTP and persists success
and pending tracker updates before draft cleanup. An interrupted attempt is
searched by its exact Message-ID in Gmail Sent. Missing evidence leaves it
`uncertain`, with no automatic resend. `cancel` and `reschedule` cannot release
these items. Recovered sends leave leftover drafts for manual review.

## Credentials and dependencies

`gitops/secrets/outreach.sops.yaml` owns `outreach-credentials` in `automation`.
It uses the existing Flux age recipient under the existing Kubernetes SOPS
policy. It contains the Gmail app-password environment file and notifier
settings, imported from the laptop without writing plaintext to Git or the
Nix store. Rotate at this source, verify decryption with output discarded,
and restart the control Deployment after Flux installs the replacement.
Gmail credentials are never sent by the laptop client.

The workload uses the existing digest-pinned registry Python image. Pure Python
openpyxl and et_xmlfile wheels are mounted read-only as a ConfigMap, with their
upstream URLs and SHA-256 digests in `vendor/manifest.json`; the runner verifies
them before importing. Keep the upstream wheel license files inside the wheels.
No runtime dependency download, pip installation, or new image publisher is
required.

Requests: control 25m CPU/64 MiB, sender 50m/128 MiB, daily backup 100m/128 MiB.
Memory limits total at most 1 GiB if all three overlap; ordinary idle usage is
the control process. The PVC requests 1 GiB on existing NFS. Existing R2 backup
credentials and bucket own the separate `restic-outreach` prefix.

## Backup and recovery

Each successful RPC and sender run atomically exports a consistent tar archive
under `/state/snapshots/latest.tar` while holding the workspace lock. It contains
the tracker, queue and resumes, without Gmail credentials. The daily
`outreach-backup` CronJob copies that archive into restic in R2, checks repository
integrity, and restores the latest snapshot into its temporary directory. Its
log prints the restored archive digest. Snapshots currently accumulate without
automatic deletion.

The backup ExternalSecret uses the existing `homelab-automation` trust domain;
the three backup properties are projected from the existing encrypted
`homelab-values` source. Do not create a second plaintext credential source.

Before restore, suspend the sender CronJob and stop the control pod. Restore
the archive into a new PVC, inspect its queue and reconcile uncertain sends
against Gmail Sent. Source configuration alone cannot recover queue mutations
after the most recent offsite backup. Do not resume an old queue without checking
mail sent since that backup. Keep the original PVC until recovery is verified.

## Verify and operate

Run `python -m unittest tests.test_outreach_sender tests.test_outreach_remote -v`
for offline crash, concurrency, transport and tracker-conflict tests. Use the
pinned flake shell for Kubernetes commands.

Check the outreach Flux Kustomization, Deployment readiness, sender CronJob's
last successful run, and the `outreach-state` PVC. `bin/outreach doctor` verifies
IMAP/SMTP authentication from the homelab and all uploaded resumes. The existing
cluster CronJob failure alerts cover sender and backup failures; the engine
also pushes send summaries, uncertain attempts and failures to ntfy.

Cutover order: ship and converge the workload, import state once, run doctor
and a sender Job, verify backup/restore, install the client at the existing
laptop CLI path, then disable `outreach-sender.timer`. Keep the original laptop
state and sender code as recovery artifacts. Never enable both senders.
