# Agent Task: woodpecker-policy-durability

Status: `active`

Base commit: `6cb771ab6785247cd1ba529c47364784a7169a49`

Checkpoint HEAD: `b425adf222197f9bc994b9d6ba07b10f9a95d91e`

Owner: `codex`

Session: `woodpecker-policy-durability-20261011`

Exported at: `2026-10-12T17:55:35.571370+00:00`

Current HEAD at export: `b425adf222197f9bc994b9d6ba07b10f9a95d91e`

## Objective

Make policy deployment recoverable and prove the NixOS artifact publishing operation

## Acceptance criteria

- [x] Immutable policy package generates workflows for all enrolled applications (nix-agent build h6r6ygisykwbd4fz73dfnmrf731cjcbm includes package smoke validation)
- [x] Protected change deployed without temporary policy override (/home/rupan/homelab/.worktrees/woodpecker-policy-durability/.agent-state/policy-live.json)
- [ ] Laptop and ISO artifacts built and published through authenticated Woodpecker operation

## Owned source

- `flake/modules/woodpecker.nix`
- `docs/runbooks/application-ci.md`
- `scripts/ci/nixos_publish.sh`
- `tests/test_nixos_publish.py`
- `scripts/ci/cache_sources.py`
- `tests/test_ci_cache_sources.py`

## Remaining work

- PR173 protected merge after1302 starts
- Verify laptop and ISO publication through1302 and final operation receipt

## Verification

Current source fingerprint at export: `cd5edf6f75a99f738db32449183576e5434706c559f9b3e88f5df9765eac40d0`

Freshness describes source identity at export time; it does not establish live infrastructure health.

- `nix develop ./flake -c just check-changed`: exit 0, freshness `stale`, superseded `true`, verified at `2026-10-12T02:52:45.395766+00:00`, source fingerprint `dca6763b0f70e98a7177fe5fbfc258b27f4aa7e2d4a62ae673d44f5570a86a1b`, evidence `/home/rupan/homelab/.worktrees/woodpecker-policy-durability/.agent-state/evidence/checks/verify-x50bpwb6.log`
- `nix develop ./flake -c just check-changed`: exit 0, freshness `stale`, superseded `true`, verified at `2026-10-12T03:37:53.784850+00:00`, source fingerprint `8c8aa6e531900b89215b01e8dea318cbc77c57b010003489933a6b2bdb29201e`, evidence `/home/rupan/homelab/.worktrees/woodpecker-policy-durability/.agent-state/evidence/checks/verify-qyj2vwrv.log`
- `nix develop ./flake -c just check-changed`: exit 0, freshness `stale`, superseded `false`, verified at `2026-10-12T06:58:51.159124+00:00`, source fingerprint `6f836da1bedab03aee2aecb5148f66df2dd702e4363fc2dd920ce3d98b3587e4`, evidence `/home/rupan/homelab/.worktrees/woodpecker-policy-durability/.agent-state/evidence/checks/verify-cx25ii0y.log`
- `nix develop ./flake -c just check`: exit 0, freshness `stale`, superseded `false`, verified at `2026-10-12T07:09:53.518046+00:00`, source fingerprint `f5d2e207014f33cf445c919a5d1f9d351204402e086ae818466c715180f397c0`, evidence `/home/rupan/homelab/.worktrees/woodpecker-policy-durability/.agent-state/evidence/checks/verify-6qggzv4p.log`
- `python3 -c 'import json,pathlib,subprocess,urllib.request,yaml; s=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(); c=yaml.safe_load(pathlib.Path("/home/rupan/.config/tea/config.yml").read_text())["logins"][0]; q=urllib.request.Request(c["url"]+"/api/v1/repos/JEFF7712/homelab/commits/"+s+"/status",headers={"Authorization":"token "+c["token"]}); r=json.load(urllib.request.urlopen(q,timeout=20)); assert r["state"]=="success"; print(json.dumps({"source_sha":s,"state":r["state"],"contexts":[{"context":v["context"],"status":v["status"]}for v in r["statuses"]]}))'`: exit 0, freshness `stale`, superseded `true`, verified at `2026-10-12T07:34:49.048611+00:00`, source fingerprint `68ea40d1ecee0e38088a9cdcb8e282ce46aae893952a8f65619d43ade062cebf`, evidence `/home/rupan/homelab/.worktrees/woodpecker-policy-durability/.agent-state/evidence/checks/verify-5uexoas8.log`
- `python3 -c 'import json,pathlib,subprocess,urllib.request,yaml; s=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(); c=yaml.safe_load(pathlib.Path("/home/rupan/.config/tea/config.yml").read_text())["logins"][0]; q=urllib.request.Request(c["url"]+"/api/v1/repos/JEFF7712/homelab/commits/"+s+"/status",headers={"Authorization":"token "+c["token"]}); r=json.load(urllib.request.urlopen(q,timeout=20)); assert r["state"]=="success"; print(json.dumps({"source_sha":s,"state":r["state"],"contexts":[{"context":v["context"],"status":v["status"]}for v in r["statuses"]]}))'`: exit 0, freshness `stale`, superseded `true`, verified at `2026-10-12T15:11:04.771368+00:00`, source fingerprint `4a56f24da9cd481b5d386d8fe95a4e45ee9c1f92729583d386458cf7969de8e2`, evidence `/home/rupan/homelab/.worktrees/woodpecker-policy-durability/.agent-state/evidence/checks/verify-guedryn2.log`
- `python3 -c 'import json,pathlib,subprocess,urllib.request,yaml; s=subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip(); c=yaml.safe_load(pathlib.Path("/home/rupan/.config/tea/config.yml").read_text())["logins"][0]; q=urllib.request.Request(c["url"]+"/api/v1/repos/JEFF7712/homelab/commits/"+s+"/status",headers={"Authorization":"token "+c["token"]}); r=json.load(urllib.request.urlopen(q,timeout=20)); assert r["state"]=="success"; print(json.dumps({"source_sha":s,"state":r["state"],"contexts":[{"context":v["context"],"status":v["status"]}for v in r["statuses"]]}))'`: exit 0, freshness `current`, superseded `false`, verified at `2026-10-12T17:55:35.176953+00:00`, source fingerprint `cd5edf6f75a99f738db32449183576e5434706c559f9b3e88f5df9765eac40d0`, evidence `/home/rupan/homelab/.worktrees/woodpecker-policy-durability/.agent-state/evidence/checks/verify-vadeckt5.log`

## Next action

Verify1302 starts with valid current-main guard, then merge reviewed PR172/173 after exact-head CI success; verify both uploads and operation receipt

## Uncommitted work

This handoff does not contain uncommitted file content. Recover it from the original checkout, or create and transfer a reviewed patch before moving to another machine.
