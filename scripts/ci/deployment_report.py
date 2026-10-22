from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.deploy_fleet import FleetDeploymentError, HostConfig, Runner, parse_targets

STORE_PATH = re.compile(r"/nix/store/[0-9a-df-np-sv-z]{32}-[^/\s]+")
GENERATION = re.compile(r"system-([0-9]+)-link")
SNAPSHOT = """set -eu
hostname
readlink -e /run/current-system
readlink /nix/var/nix/profiles/system
for link in /nix/var/nix/profiles/system-[0-9]*-link; do
  [ -L "$link" ] || continue
  printf '%s\\t%s\\n' "${link##*/}" "$(readlink "$link")"
done
"""
PRESENCE = """set -efu
paths=$(nix-store --store daemon --query --requisites "$1")
for path in $paths; do
  if [ ! -e "$path" ]; then
    printf 'Missing closure path: %s\\n' "$path" >&2
    exit 1
  fi
done
printf '%s\\n' "$paths"
"""


def json_object(output: str) -> dict[str, Any]:
    data = json.loads(output)
    if not isinstance(data, dict):
        raise ValueError("Expected a JSON object")
    return data


def measurement(confidence: str, reason: str = "", **values: Any) -> dict[str, Any]:
    return {"confidence": confidence, "reason": reason, **values}


def store_path(value: Any, *, derivation: bool = False) -> str:
    if not isinstance(value, str) or not STORE_PATH.fullmatch(value):
        raise ValueError("Expected a canonical /nix/store path")
    if derivation and not value.endswith(".drv"):
        raise ValueError("Expected a derivation path")
    return value


def snapshot(output: str, host: HostConfig) -> dict[str, Any]:
    lines = output.splitlines()
    if len(lines) < 3 or lines[0] != host.name:
        raise ValueError(f"Target identity does not match {host.name}")
    current = store_path(lines[1])
    profile = GENERATION.fullmatch(Path(lines[2]).name)
    if profile is None:
        raise ValueError("Cannot identify the system profile generation")
    generations: dict[int, str] = {}
    for line in lines[3:]:
        name, path = line.split("\t")
        match = GENERATION.fullmatch(name)
        if match is None:
            raise ValueError("Invalid system generation link")
        generations[int(match[1])] = store_path(path)
    profile_id = int(profile[1])
    matching = [n for n, path in generations.items() if path == current]
    current_id = None
    if profile_id in matching:
        current_id = profile_id
    elif len(matching) == 1:
        current_id = matching[0]
    previous = max(
        (n for n in generations if current_id is not None and n < current_id),
        default=None,
    )
    return {
        "store_path": current,
        "generation": current_id,
        "generation_confidence": "exact" if current_id is not None else "unavailable",
        "matching_generations": sorted(matching),
        "profile_generation": profile_id,
        "profile_store_path": generations.get(profile_id),
        "rollback_generation": previous,
        "rollback_store_path": generations.get(previous) if previous else None,
        "generation_links": generations,
    }


def closure(output: str, root: str) -> dict[str, dict[str, Any]]:
    data = json.loads(output)
    if not isinstance(data, dict) or root not in data:
        raise ValueError("Closure query did not include its root")
    for path, info in data.items():
        store_path(path)
        if not isinstance(info, dict) or type(info.get("narSize")) is not int:
            raise ValueError("Closure query contains unavailable path metadata")
        if info["narSize"] < 0 or not isinstance(info.get("references"), list):
            raise ValueError("Invalid closure size or references")
        if any(
            not isinstance(ref, str) or ref not in data for ref in info["references"]
        ):
            raise ValueError("Closure query omitted referenced store paths")
    return data


def closure_size(paths: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return measurement(
        "exact",
        "Unique registered NAR sizes; every closure root exists on this target. Not filesystem usage or a byte-integrity check.",
        nar_bytes=sum(info["narSize"] for info in paths.values()),
        path_count=len(paths),
    )


def compare_packages(
    current: dict[str, Any], candidate: dict[str, Any]
) -> dict[str, Any]:
    def versions(paths: dict[str, Any]) -> dict[str, set[str]]:
        result: dict[str, set[str]] = defaultdict(set)
        for path in paths:
            name = path.split("-", 1)[1]
            match = re.fullmatch(r"(.+?)-([0-9].*)", name)
            if match:
                result[match[1]].add(match[2])
        return result

    old, new = versions(current), versions(candidate)
    return measurement(
        "estimated",
        "Package names and versions inferred from store names; version changes may be upgrades, downgrades or changed version sets. Store-path differences are exact.",
        store_paths_confidence="exact",
        added=[
            {"name": n, "versions": sorted(new[n])}
            for n in sorted(new.keys() - old.keys())
        ],
        removed=[
            {"name": n, "versions": sorted(old[n])}
            for n in sorted(old.keys() - new.keys())
        ],
        version_changed=[
            {"name": n, "before": sorted(old[n]), "after": sorted(new[n])}
            for n in sorted(old.keys() & new.keys())
            if old[n] != new[n]
        ],
        added_store_paths=sorted(candidate.keys() - current.keys()),
        removed_store_paths=sorted(current.keys() - candidate.keys()),
    )


def build_plan(stdout: str, stderr: str, drv: str, output: str) -> dict[str, Any]:
    result = json.loads(stdout)
    if result != [{"drvPath": drv, "outputs": {"out": output}}]:
        raise ValueError("Native plan does not match the candidate identity")
    builds: list[str] = []
    downloads: list[str] = []
    destination: list[str] | None = None
    download_bytes = 0
    expected_builds = 0
    expected_downloads = 0
    units = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}
    for line in stderr.splitlines():
        build_header = re.fullmatch(
            r"(?:this derivation|these ([0-9]+) derivations) will be built:", line
        )
        download_header = re.fullmatch(
            r"(?:this path|these ([0-9]+) paths) will be fetched \(([0-9.]+) (B|KiB|MiB|GiB|TiB) download, [0-9.]+ (?:B|KiB|MiB|GiB|TiB) unpacked\):",
            line,
        )
        if build_header:
            expected_builds += int(build_header[1] or 1)
            destination = builds
        elif download_header:
            expected_downloads += int(download_header[1] or 1)
            download_bytes += round(
                float(download_header[2]) * units[download_header[3]]
            )
            destination = downloads
        elif line.startswith("  /nix/store/"):
            if destination is None:
                raise ValueError("Store path outside a recognized plan section")
            destination.append(store_path(line.strip()))
        elif "will be" in line or "unknown" in line.lower() or "error:" in line:
            raise ValueError("Unrecognized or incomplete Nix plan")
        elif line and not line.startswith("warning:"):
            raise ValueError("Unexpected Nix planner output")
    if len(builds) != expected_builds or len(downloads) != expected_downloads:
        raise ValueError("Nix plan path counts do not match its summary")
    if any(not path.endswith(".drv") for path in builds):
        raise ValueError("Build requirements must be derivation paths")
    return measurement(
        "estimated",
        "Native dry-run on the target with its user settings and substituters. Path lists are observed; download bytes are rounded Nix estimates. Required derivations may use configured builders, so local execution is not guaranteed.",
        store="daemon",
        path_lists_confidence="exact",
        download_size_confidence="estimated",
        build_derivations=builds,
        substitute_paths=downloads,
        estimated_download_bytes=download_bytes,
    )


class Reporter:
    def __init__(
        self,
        *,
        runner: Runner = subprocess.run,
        timeout: float = 120,
        ssh_user: str = "rupan",
        ssh_opts: Sequence[str] = (),
    ) -> None:
        self.runner = runner
        self.timeout = timeout
        self.ssh_user = ssh_user
        self.ssh_opts = list(ssh_opts)

    def run(self, command: list[str]) -> str:
        result = self.command(command)
        if result.returncode:
            raise ValueError(
                f"{command[0]} query failed (exit {result.returncode}): {result.stderr.strip()[:600]}"
            )
        return result.stdout

    def command(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return self.runner(
            command, text=True, capture_output=True, check=False, timeout=self.timeout
        )

    def ssh(self, host: HostConfig, command: list[str]) -> list[str]:
        return [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
            "-o",
            "StrictHostKeyChecking=yes",
            *self.ssh_opts,
            f"{self.ssh_user}@{host.ip}",
            shlex.join(command),
        ]

    def observe(self, host: HostConfig) -> dict[str, Any]:
        return snapshot(self.run(self.ssh(host, ["sh", "-c", SNAPSHOT])), host)

    def paths(self, host: HostConfig, root: str) -> dict[str, dict[str, Any]]:
        paths = closure(
            self.run(
                self.ssh(
                    host,
                    [
                        "nix",
                        "path-info",
                        "--store",
                        "daemon",
                        "--json",
                        "--json-format",
                        "1",
                        "--recursive",
                        "--option",
                        "substitute",
                        "false",
                        store_path(root),
                    ],
                )
            ),
            root,
        )
        present = self.run(self.ssh(host, ["sh", "-c", PRESENCE, "sh", root]))
        if set(present.splitlines()) != paths.keys():
            raise ValueError("Closure changed between metadata and presence checks")
        return paths

    def candidate_source(self, flake: str) -> tuple[str, dict[str, Any]]:
        data = json_object(
            self.run(
                [
                    "nix",
                    "flake",
                    "metadata",
                    "--json",
                    "--no-write-lock-file",
                    "--no-update-lock-file",
                    flake,
                ]
            )
        )
        source = store_path(data["path"])
        locked = data["locked"]
        if not isinstance(locked, dict):
            raise ValueError("Invalid locked flake metadata")
        revision = data.get("revision")
        if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError(
                "Deployment reports require a clean committed Git flake so source revision and flake self metadata remain reproducible."
            )
        locked_ref = data["url"]
        if not isinstance(locked_ref, str) or f"rev={revision}" not in locked_ref:
            raise ValueError(
                "Flake metadata did not provide a revision-locked Git reference"
            )
        return locked_ref, {
            "flake": flake,
            "source_store_path": source,
            "locked_ref": locked_ref,
            "revision": revision,
            "nar_hash": locked.get("narHash"),
            "fingerprint": data.get("fingerprint"),
            "revision_confidence": "exact",
            "note": "Evaluation uses this locked Git reference, preserving flake self metadata including its revision.",
        }

    def candidate(self, flake: str, host: HostConfig) -> dict[str, Any]:
        data = json_object(
            self.run(
                [
                    "nix",
                    "eval",
                    "--json",
                    "--no-write-lock-file",
                    "--no-update-lock-file",
                    "--option",
                    "allow-import-from-derivation",
                    "false",
                    f"{flake}#nixosConfigurations.{host.flake_attr}.config.system.build.toplevel",
                    "--apply",
                    "s: { drv_path = s.drvPath; store_path = s.outPath; }",
                ]
            )
        )
        return measurement(
            "exact",
            "Evaluated identity; this does not realize the candidate.",
            flake_attr=host.flake_attr,
            drv_path=store_path(data["drv_path"], derivation=True),
            store_path=store_path(data["store_path"]),
        )

    def report_host(self, host: HostConfig, flake: str) -> dict[str, Any]:
        report: dict[str, Any] = {
            "host": host.name,
            "address": host.ip,
            "observed_at": datetime.now(UTC).isoformat(),
            "status": "partial",
            "errors": [],
        }
        unavailable = measurement("unavailable", "Observation did not complete.")
        for key in (
            "deployed",
            "candidate",
            "current_closure",
            "candidate_closure",
            "closure_delta",
            "packages",
            "plan",
            "rollback",
            "stable_observation",
        ):
            report[key] = dict(unavailable)
        try:
            before = self.observe(host)
            report["deployed"] = measurement(
                "exact",
                "Observed /run/current-system; profile generation is reported separately.",
                **before,
            )
            report["candidate"] = self.candidate(flake, host)
        except (ValueError, KeyError, OSError, subprocess.TimeoutExpired) as error:
            report["errors"].append(str(error))
            report["status"] = "error"
            return report

        def query(label: str, operation: Callable[[], Any]) -> Any:
            try:
                return operation()
            except (ValueError, KeyError, OSError, subprocess.TimeoutExpired) as error:
                report[label] = measurement("unavailable", str(error))
                return None

        version = query(
            "deployed_source",
            lambda: json_object(self.run(self.ssh(host, ["nixos-version", "--json"]))),
        )
        if version is not None:
            report["deployed_source"] = measurement(
                "exact" if version.get("configurationRevision") else "unavailable",
                "Only configurationRevision identifies homelab source; nixpkgsRevision does not.",
                revision=version.get("configurationRevision"),
            )
        old = query("current_closure", lambda: self.paths(host, before["store_path"]))
        new = query(
            "candidate_closure",
            lambda: self.paths(host, report["candidate"]["store_path"]),
        )
        if old is not None:
            report["current_closure"] = closure_size(old)
        if new is not None:
            report["candidate_closure"] = closure_size(new)
        if old is not None and new is not None:
            report["packages"] = compare_packages(old, new)
            current_size = report["current_closure"]["nar_bytes"]
            delta = report["candidate_closure"]["nar_bytes"] - current_size
            report["closure_delta"] = measurement(
                "exact",
                nar_bytes=delta,
                percent=delta * 100 / current_size if current_size else None,
            )
        else:
            for field in ("packages", "closure_delta"):
                report[field] = measurement(
                    "unavailable",
                    "Both complete closures must already be registered on the target; reporting never builds or downloads them.",
                )

        rollback_path = before["rollback_store_path"]
        report["rollback"] = measurement(
            "unavailable",
            "The running generation label is unavailable, so its predecessor cannot be selected reliably."
            if before["generation"] is None
            else "No retained generation preceding the running generation was found.",
            generation=before["rollback_generation"],
            store_path=rollback_path,
        )
        if rollback_path:
            rollback = query("rollback", lambda: self.paths(host, rollback_path))
            availability = (
                closure_size(rollback) if rollback is not None else report["rollback"]
            )
            report["rollback"] = {
                **availability,
                "generation": before["rollback_generation"],
                "store_path": rollback_path,
                "closure_available": True if rollback is not None else None,
                "retention": "Observed generation link only; reporting creates no GC root. Future retention, byte integrity and successful boot are not guaranteed.",
                "selection": "Highest retained generation below the running generation; an explicit recovery target, not necessarily the profile's implicit rollback.",
            }

        def plan() -> dict[str, Any]:
            candidate = report["candidate"]
            self.run(
                self.ssh(
                    host,
                    [
                        "nix-store",
                        "--check-validity",
                        "--store",
                        "daemon",
                        candidate["drv_path"],
                    ],
                )
            )
            command = self.ssh(
                host,
                [
                    "env",
                    "LC_ALL=C",
                    "NO_COLOR=1",
                    "nix",
                    "build",
                    "--store",
                    "daemon",
                    "--dry-run",
                    "--no-link",
                    "--json",
                    f"{candidate['drv_path']}^out",
                ],
            )
            result = self.command(command)
            if result.returncode:
                raise ValueError(
                    "Target native plan unavailable; candidate derivations must already exist there. "
                    + result.stderr.strip()[:600]
                )
            return build_plan(
                result.stdout,
                result.stderr,
                candidate["drv_path"],
                candidate["store_path"],
            )

        plan_result = query("plan", plan)
        if plan_result is not None:
            report["plan"] = plan_result
        after = query("stable_observation", lambda: self.observe(host))
        if after is not None and before == after:
            report["stable_observation"] = measurement(
                "exact",
                "Running system and generation links unchanged between probes; store/GC activity is not locked.",
            )
        else:
            report["stable_observation"] = measurement(
                "unavailable",
                "Target identity or generation links changed, or could not be rechecked. Discard this report and collect it again.",
            )
            report["status"] = "error"
            return report
        if all(
            report[key]["confidence"] != "unavailable"
            for key in (
                "deployed_source",
                "current_closure",
                "candidate_closure",
                "packages",
                "plan",
                "rollback",
            )
        ):
            report["status"] = "complete"
        return report


def markdown(report: dict[str, Any]) -> str:
    def cell(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    def evidence(field: str, item: dict[str, Any]) -> str:
        if item["confidence"] == "unavailable":
            return "unavailable: " + item["reason"]
        if field == "deployed":
            return f"{item['store_path']} (profile generation {item['profile_generation']})"
        if field == "deployed_source":
            return item["revision"]
        if field == "candidate":
            return (
                f"{item['flake_attr']}: {item['drv_path']}; output {item['store_path']}"
            )
        if field in ("current_closure", "candidate_closure"):
            return f"{item['nar_bytes']:,} NAR bytes, {item['path_count']} paths"
        if field == "closure_delta":
            percent = (
                f"{item['percent']:+.2f}%"
                if item["percent"] is not None
                else "percentage unavailable"
            )
            return f"{item['nar_bytes']:+,} NAR bytes ({percent})"
        if field == "packages":
            return f"{len(item['added'])} added, {len(item['removed'])} removed, {len(item['version_changed'])} version changes; {len(item['added_store_paths'])} store paths added, {len(item['removed_store_paths'])} removed"
        if field == "plan":
            return f"{len(item['build_derivations'])} derivations require building; {len(item['substitute_paths'])} substitutions; approximately {item['estimated_download_bytes']:,} download bytes"
        if field == "rollback":
            return f"generation {item['generation']}, {item['store_path']}; complete closure registered and root paths present ({item['nar_bytes']:,} NAR bytes)"
        return item["reason"]

    source = report.get("candidate_source", {})
    lines = [
        "# Deployment report",
        "",
        f"Observed: {report['generated_at']}",
        "",
        f"Candidate source: {source.get('revision') or 'unavailable'}",
        f"Source NAR hash: {source.get('nar_hash') or 'unavailable'}",
        "",
        "This is diagnostic evidence, not deployment authorization. A partial report contains unavailable measurements; an error report must be discarded.",
        "",
        "Sizes are NAR serialization sizes, not filesystem usage. Package versions are inferred from store names. Rollback checks establish current availability, not future retention, content integrity or bootability. JSON includes full evidence and limitations.",
        "",
    ]
    for host in report["hosts"]:
        lines += [
            f"## {host['host']} ({host['status']})",
            "",
            "| Measurement | Evidence | Confidence |",
            "| --- | --- | --- |",
        ]
        for field in (
            "deployed",
            "deployed_source",
            "candidate",
            "current_closure",
            "candidate_closure",
            "closure_delta",
            "packages",
            "plan",
            "rollback",
            "stable_observation",
        ):
            item = host.get(
                field, measurement("unavailable", "Observation did not complete.")
            )
            lines.append(
                f"| {field.replace('_', ' ')} | {cell(evidence(field, item))} | {item['confidence']} |"
            )
            if field == "deployed" and "generation_confidence" in item:
                label = (
                    item["generation"]
                    if item["generation"] is not None
                    else f"unavailable; matching labels {item['matching_generations']}"
                )
                lines.append(
                    f"| running generation | {label} | {item['generation_confidence']} |"
                )
        packages = host["packages"]
        if packages["confidence"] != "unavailable":
            changes = [
                f"Added {item['name']}: {', '.join(item['versions'])}"
                for item in packages["added"]
            ]
            changes += [
                f"Removed {item['name']}: {', '.join(item['versions'])}"
                for item in packages["removed"]
            ]
            changes += [
                f"{item['name']}: {', '.join(item['before'])} -> {', '.join(item['after'])}"
                for item in packages["version_changed"]
            ]
            if changes:
                lines += [
                    "",
                    "Package changes:",
                    "",
                    *[f"- {cell(change)}" for change in changes],
                ]
        plan = host["plan"]
        if plan["confidence"] != "unavailable":
            for label, key in (
                ("Derivations requiring builds", "build_derivations"),
                ("Paths requiring substitution", "substitute_paths"),
            ):
                if plan[key]:
                    lines += [
                        "",
                        label + ":",
                        "",
                        *[f"- `{path}`" for path in plan[key]],
                    ]
        lines += ["", *[f"Error: {cell(error)}" for error in host["errors"]], ""]
    lines += [f"Error: {cell(error)}" for error in report.get("errors", [])]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None, runner: Runner = subprocess.run) -> int:
    parser = argparse.ArgumentParser(
        description="Observe fleet deployment evidence without changing target stores or activating systems."
    )
    parser.add_argument("--target", action="append", dest="targets")
    parser.add_argument("--flake", default="./flake")
    parser.add_argument("--ssh-user", default="rupan")
    parser.add_argument(
        "--ssh-opts", default=os.getenv("NIX_SSHOPTS") or os.getenv("SSH_OPTS") or ""
    )
    parser.add_argument(
        "--timeout", type=float, default=120, help="Timeout per query in seconds."
    )
    parser.add_argument(
        "--json", action="store_true", help="Print JSON instead of Markdown."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Write report.json and report.md locally and print the chosen format.",
    )
    args = parser.parse_args(argv)
    if (
        args.timeout <= 0
        or args.timeout == float("inf")
        or args.timeout != args.timeout
    ):
        parser.error("--timeout must be positive")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*", args.ssh_user):
        parser.error("Invalid SSH user")
    report: dict[str, Any] = {
        "schema_version": 1,
        "command": "deployment-report",
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "error",
        "hosts": [],
        "errors": [],
    }
    try:
        targets = parse_targets(args.targets)
        if not targets:
            raise ValueError("Select at least one target host")
        reporter = Reporter(
            runner=runner,
            timeout=args.timeout,
            ssh_user=args.ssh_user,
            ssh_opts=shlex.split(args.ssh_opts),
        )
        flake, source = reporter.candidate_source(args.flake)
        report["candidate_source"] = source
        report["hosts"] = [reporter.report_host(host, flake) for host in targets]
        statuses = {host["status"] for host in report["hosts"]}
        if "error" in statuses:
            report["status"] = "error"
        elif "partial" in statuses:
            report["status"] = "partial"
        else:
            report["status"] = "complete"
    except (
        FleetDeploymentError,
        ValueError,
        KeyError,
        OSError,
        subprocess.TimeoutExpired,
    ) as error:
        report["errors"].append(str(error))
    json_output = json.dumps(report, indent=2, sort_keys=True) + "\n"
    summary = markdown(report)
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "report.json").write_text(json_output)
        (args.output_dir / "report.md").write_text(summary)
    sys.stdout.write(json_output if args.json else summary)
    return int(report["status"] == "error")


if __name__ == "__main__":
    raise SystemExit(main())
