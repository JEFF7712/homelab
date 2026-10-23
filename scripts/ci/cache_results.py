from __future__ import annotations

import argparse
import json
from pathlib import Path


def store_path(value: object) -> str:
    if (
        not isinstance(value, str)
        or Path(value).parent != Path("/nix/store")
        or any(character.isspace() for character in value)
    ):
        raise ValueError(f"Invalid build result store path: {value!r}")
    return value


def build_paths(report: object, count: int) -> tuple[list[str], list[str]]:
    if not isinstance(report, dict) or not isinstance(report.get("results"), list):
        raise TypeError("Build report must contain a results list")
    evaluations: dict[str, dict[str, object]] = {}
    for result in report["results"]:
        if (
            not isinstance(result, dict)
            or result.get("success") is not True
            or result.get("skipped")
        ):
            raise ValueError(f"Unsuccessful build result: {result!r}")
        if result.get("type") == "EVAL":
            attr = result.get("attr")
            if not isinstance(attr, str) or attr in evaluations:
                raise ValueError("Invalid or duplicate evaluation attribute")
            evaluations[attr] = result
    expected = {f"target{index}" for index in range(count)}
    if count <= 0 or set(evaluations) != expected:
        raise ValueError("Build report does not cover the requested target set")
    paths: set[str] = set()
    derivations: set[str] = set()
    for result in evaluations.values():
        outputs = result.get("outputs")
        if not isinstance(outputs, dict) or not outputs:
            raise ValueError("Evaluation has no output paths")
        paths.update(store_path(value) for value in outputs.values())
        derivation = store_path(result.get("drvPath"))
        if not derivation.endswith(".drv"):
            raise ValueError("Evaluation has no derivation path")
        derivations.add(derivation)
    return sorted(paths), sorted(derivations)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate nix-fast-build cache results"
    )
    parser.add_argument("report", type=Path)
    parser.add_argument("count", type=int)
    parser.add_argument("paths", type=Path)
    parser.add_argument("derivations", type=Path)
    args = parser.parse_args()
    paths, derivations = build_paths(json.loads(args.report.read_text()), args.count)
    args.paths.write_text("".join(f"{path}\n" for path in paths))
    args.derivations.write_text("".join(f"{path}\n" for path in derivations))


if __name__ == "__main__":
    main()
