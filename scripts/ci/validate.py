import argparse
import json
import os
import subprocess
from pathlib import Path

from scripts.ci.contract import build_report
from scripts.ci.scope import Scope, determine


def matrix_checks() -> int:
    for command in (
        [
            "env",
            "-u",
            "PYTHONPATH",
            "uv",
            "--directory",
            "services/led-matrix",
            "sync",
            "--frozen",
            "--no-dev",
            "--no-install-project",
        ],
        ["bash", "scripts/checks/led-matrix.sh"],
    ):
        code = subprocess.call(command)
        if code:
            return code
    return 0


def _scoped_commands(scope: Scope) -> list[tuple[list[str], dict[str, str] | None]]:
    commands: list[tuple[list[str], dict[str, str] | None]] = []
    provisioned = False
    for check in scope.checks:
        kind, _, arg = check.partition(":")
        if kind == "agent":
            commands.append((["bash", "scripts/checks/agent-workflows.sh"], None))
        elif kind == "python":
            env = None
            if scope.test_modules:
                env = {"CI_SCOPED_TEST_MODULES": " ".join(scope.test_modules)}
            commands.append((["bash", "scripts/checks/python.sh"], env))
        elif kind == "registry":
            commands.append((["bash", "scripts/checks/registry.sh"], None))
        elif kind == "nix":
            commands.append((["bash", "scripts/checks/nix.sh", arg], None))
        elif kind == "gitops":
            commands.append((["bash", "scripts/checks/gitops.sh"], None))
        elif kind == "tofu":
            if not provisioned:
                commands.append((["just", "provision-check-deps"], None))
                provisioned = True
            commands.append((["bash", "scripts/checks/tofu.sh"], None))
        elif kind == "home-assistant":
            commands.append((["bash", "scripts/checks/home-assistant.sh"], None))
        elif kind == "docs":
            commands.append((["python", "-m", "scripts.checks.docs"], None))
            commands.append((["python", "scripts/checks/whitespace.py"], None))
    if any(
        path.startswith(("flake/hosts/", "opnsense_reconciler/", "tofu/opnsense/"))
        for path in scope.paths
    ):
        commands.append((["just", "topology"], None))
    return commands


def _repository_scoped(scope: Scope) -> int:
    for command, extra_env in _scoped_commands(scope):
        env = dict(os.environ)
        # Scoped lanes run their own tests; the full-gate partition flag
        # would otherwise shrink python.sh to the Nix-integration subset.
        env.pop("CI_UNIT_TESTS_EXTERNAL", None)
        if extra_env:
            env.update(extra_env)
        print(f"CI scoped: {' '.join(command)}", flush=True)
        code = subprocess.call(command, env=env)
        if code:
            return code
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("gate", choices=("repository", "flake", "cache", "fleet"))
    parser.add_argument("--flake", default="./flake")
    parser.add_argument("--full", action="store_true", help="force full gate")
    args = parser.parse_args(argv)
    if args.gate == "fleet":
        code = subprocess.call(
            ["nix", "flake", "check", args.flake, "--no-write-lock-file"]
        )
        if code:
            return code
        return subprocess.call(["bash", "scripts/ci/cache.sh", args.flake])
    if args.full:
        scope = Scope("full", "forced by --full flag")
    else:
        scope = determine(Path.cwd())
    directory = Path(f"artifacts/ci/{os.environ.get('CI_JOB_ID', 'local')}")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{args.gate}-scope.json").write_text(
        json.dumps(scope.report()) + "\n"
    )
    print(f"CI {args.gate}: {scope.mode} ({scope.reason})", flush=True)
    if args.gate == "cache":
        if scope.mode != "full":
            print(
                "No fleet inputs changed; the deployer prepares only changed host closures."
            )
            return 0
        return subprocess.call(["bash", "scripts/ci/cache.sh", args.flake])
    if scope.mode == "documentation":
        for command in (
            ["python", "-m", "scripts.checks.docs"],
            ["python", "scripts/checks/whitespace.py"],
        ):
            code = subprocess.call(command)
            if code:
                return code
        return 0
    if scope.mode == "matrix":
        if args.gate != "repository":
            return 0
        code = matrix_checks()
        if code:
            return code
        for command in (
            ["python", "-m", "scripts.checks.docs"],
            ["python", "scripts/checks/whitespace.py"],
        ):
            code = subprocess.call(command)
            if code:
                return code
        return 0
    if scope.mode == "scoped":
        if args.gate != "repository":
            print(
                "No fleet inputs changed; the deployer prepares only changed host closures."
            )
            return 0
        return _repository_scoped(scope)
    if args.gate == "repository":
        if Path("services/led-matrix").is_dir():
            code = matrix_checks()
            if code:
                return code
        code = subprocess.call(["just", "provision-check-deps"])
        if code:
            return code
        env = dict(os.environ, CI_UNIT_TESTS_EXTERNAL="1")
        if scope.mode == "application":
            env["SKIP_FLAKE_CHECK"] = "1"
        return subprocess.call(["just", "check"], env=env)
    code = build_report(args.flake, Path("artifacts/ci/tests.xml"))
    if code or scope.mode == "application":
        return code
    env = dict(os.environ, NIX_SHOW_STATS="1")
    return subprocess.call(
        ["nix", "flake", "check", args.flake, "--no-write-lock-file"], env=env
    )


if __name__ == "__main__":
    raise SystemExit(main())
