import argparse
import json
import os
import subprocess
from pathlib import Path

from scripts.ci.contract import build_report
from scripts.ci.scope import determine


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("gate", choices=("repository", "flake", "cache", "fleet"))
    parser.add_argument("--flake", default="./flake")
    args = parser.parse_args(argv)
    if args.gate == "fleet":
        code = subprocess.call(
            ["nix", "flake", "check", args.flake, "--no-write-lock-file"]
        )
        if code:
            return code
        return subprocess.call(["bash", "scripts/ci/cache.sh", args.flake])
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
                "No fleet inputs changed; deployment preflight will require a full build."
            )
            return 0
        return subprocess.call(["bash", "scripts/ci/cache.sh", args.flake])
    if scope.mode == "documentation":
        for command in (
            ["python", "scripts/checks/docs.py"],
            ["python", "scripts/checks/whitespace.py"],
        ):
            code = subprocess.call(command)
            if code:
                return code
        return 0
    if args.gate == "repository":
        code = subprocess.call(["just", "provision-check-deps"])
        if code:
            return code
        env = dict(os.environ, CI_UNIT_TESTS_EXTERNAL="1")
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
