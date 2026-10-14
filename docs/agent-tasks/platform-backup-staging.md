# Agent Task: platform-backup-staging

Status: `complete`

Base commit: `9bcac37425caa42135a01f4d57ea7e927ab6c291`

Checkpoint HEAD: `964aa30d71c4c891c3c39efeeb1e302847276bca`

Owner: `codex`

Session: `platform-backup-staging-20261012`

Exported at: `2026-10-12T19:16:43.341308+00:00`

Current HEAD at export: `964aa30d71c4c891c3c39efeeb1e302847276bca`

## Objective

Restore working platform backups by staging on persistent storage

## Acceptance criteria

- [x] Backup stages real SQLite snapshots on persistent storage and cleans up on success and failure (9 focused backup tests passed;1510 full-suite unit tests passed)
- [x] Deployed CI backup completes and independent restore verifies its database (Deployedg9sviswc; production unit and14:01 hourly timer succeeded; snapshot75771a58 restored and verified20 repositories)

## Owned source

- `scripts/ci/platform_backup.py`
- `tests/test_ci_platform_backup.py`
- `flake/modules/platform-backup.nix`
- `docs/runbooks/forgejo-disaster-recovery.md`

## Remaining work

- None

## Verification

Current source fingerprint at export: `a737e4a98cb3114d5d4f3152f093e6412b2c8313413a782a2417947b70ce4aec`

Freshness describes source identity at export time; it does not establish live infrastructure health.

- `python3 -c 'import json,pathlib,subprocess,urllib.request,yaml; s=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(); c=yaml.safe_load(pathlib.Path("/home/rupan/.config/tea/config.yml").read_text())["logins"][0]; q=urllib.request.Request(c["url"]+"/api/v1/repos/JEFF7712/homelab/commits/"+s+"/status",headers={"Authorization":"token "+c["token"]}); r=json.load(urllib.request.urlopen(q,timeout=20)); assert r["state"]=="success"; print(json.dumps({"source_sha":s,"state":r["state"],"contexts":[{"context":v["context"],"status":v["status"]}for v in r["statuses"]]}))'`: exit 0, freshness `stale`, superseded `false`, verified at `2026-10-12T17:54:14.350791+00:00`, source fingerprint `251c6192f64152f56d9a1bb5a2ea96befcdab29bc0e50593284fc2fd6fc37b59`, evidence `/home/rupan/homelab/.worktrees/platform-backup-staging/.agent-state/evidence/checks/verify-edn5fk2t.log`

## Next action

Complete

## Uncommitted work

This handoff does not contain uncommitted file content. Recover it from the original checkout, or create and transfer a reviewed patch before moving to another machine.
