from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def store_path(value: object) -> str:
    if (
        not isinstance(value, str)
        or Path(value).parent != Path("/nix/store")
        or any(character.isspace() for character in value)
    ):
        raise ValueError(f"Invalid build result store path: {value!r}")
    return value


def build_paths(report: object, count: int) -> tuple[list[str], list[str], list[str]]:
    """(output paths, derivations, failed targets) of a nix-fast-build report.

    Every target must evaluate. A target whose build failed keeps its
    derivation, so the dependencies it did build are still published, but
    not its missing outputs: one flaky VM check would otherwise discard the
    whole fleet's cache (2026-11-21, niks3-cache-service under load).
    """
    if not isinstance(report, dict) or not isinstance(report.get("results"), list):
        raise TypeError("Build report must contain a results list")
    evaluations: dict[str, dict[str, object]] = {}
    builds: list[dict[str, object]] = []
    for result in report["results"]:
        if not isinstance(result, dict) or result.get("skipped"):
            raise ValueError(f"Unsuccessful build result: {result!r}")
        if result.get("type") == "BUILD":
            builds.append(result)
            continue
        if result.get("success") is not True:
            raise ValueError(f"Unsuccessful build result: {result!r}")
        if result.get("type") == "EVAL":
            attr = result.get("attr")
            if not isinstance(attr, str) or attr in evaluations:
                raise ValueError("Invalid or duplicate evaluation attribute")
            evaluations[attr] = result
    expected = {f"target{index}" for index in range(count)}
    if count <= 0 or set(evaluations) != expected:
        raise ValueError("Build report does not cover the requested target set")
    failed: set[str] = set()
    for result in builds:
        if result.get("attr") not in evaluations:
            raise ValueError(f"Build result for an unknown target: {result!r}")
        if result.get("success") is not True:
            failed.add(str(result["attr"]))
    paths: set[str] = set()
    derivations: set[str] = set()
    for attr, result in evaluations.items():
        outputs = result.get("outputs")
        if not isinstance(outputs, dict) or not outputs:
            raise ValueError("Evaluation has no output paths")
        if attr not in failed:
            paths.update(store_path(value) for value in outputs.values())
        derivation = store_path(result.get("drvPath"))
        if not derivation.endswith(".drv"):
            raise ValueError("Evaluation has no derivation path")
        derivations.add(derivation)
    return sorted(paths), sorted(derivations), sorted(failed)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate nix-fast-build cache results"
    )
    parser.add_argument(
        "--report",
        nargs=2,
        action="append",
        required=True,
        metavar=("STATUS", "PATH"),
        help="one nix-fast-build pass: its exit status and result file",
    )
    parser.add_argument("count", type=int)
    parser.add_argument("paths", type=Path)
    parser.add_argument("derivations", type=Path)
    args = parser.parse_args()
    results: list[object] = []
    for status, path in args.report:
        report = json.loads(Path(path).read_text())
        if not isinstance(report, dict) or not isinstance(report.get("results"), list):
            raise SystemExit(f"{path}: build report must contain a results list")
        failed_here = [
            result
            for result in report["results"]
            if isinstance(result, dict)
            and result.get("type") == "BUILD"
            and result.get("success") is not True
        ]
        # An exit status its report does not explain publishes nothing.
        if bool(failed_here) != (status != "0"):
            raise SystemExit(
                f"nix-fast-build exited {status} but {path} lists"
                f" {len(failed_here)} failed target(s); publishing nothing"
            )
        results.extend(report["results"])
    paths, derivations, failed = build_paths({"results": results}, args.count)
    if failed:
        print(
            "Publishing the targets that built; failed: " + ", ".join(failed),
            file=sys.stderr,
        )
    args.paths.write_text("".join(f"{path}\n" for path in paths))
    args.derivations.write_text("".join(f"{path}\n" for path in derivations))


if __name__ == "__main__":
    main()
