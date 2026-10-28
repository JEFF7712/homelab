"""Change the reviewed GitOps preference order for the stable STT gateway.

The command edits one manifest and never stages, commits, pushes, or reconciles.
It refuses to overwrite an existing edit so the resulting diff stays reviewable.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GATEWAY_FILE = REPO_ROOT / "gitops" / "voice" / "gateway.yaml"
BACKENDS = ("nemotron", "cloud")
ADDRESSES = {
    "nemotron": "wyoming-stt-nemotron:10300",
    "cloud": "wyoming-stt-groq:10300",
}


def preference_value(primary: str) -> str:
    ordered = [primary, *(backend for backend in BACKENDS if backend != primary)]
    return ",".join(f"{name}={ADDRESSES[name]}" for name in ordered)


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in BACKENDS:
        raise SystemExit(f"usage: voice_stt_switch.py [{'|'.join(BACKENDS)}]")
    backend = sys.argv[1]

    import subprocess

    dirty = subprocess.run(
        [
            "git",
            "status",
            "--porcelain",
            "--",
            str(GATEWAY_FILE.relative_to(REPO_ROOT)),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    if dirty:
        raise SystemExit(f"refusing to overwrite existing changes in {GATEWAY_FILE}")

    text = GATEWAY_FILE.read_text()
    marker = '              value: "'
    lines = text.splitlines(keepends=True)
    value_index = next(
        (
            index
            for index, line in enumerate(lines)
            if "WYOMING_BACKENDS" in lines[index - 1] and marker in line
        ),
        None,
    )
    if value_index is None:
        raise SystemExit("STT WYOMING_BACKENDS setting not found")
    current = lines[value_index].split(marker, 1)[1].rsplit('"', 1)[0]
    replacement = preference_value(backend)
    if current == replacement:
        print(f"STT backend already {backend}, nothing to do")
        return
    lines[value_index] = f'{marker}{replacement}"\n'
    GATEWAY_FILE.write_text("".join(lines))
    print(f"STT preference changed to {backend}; review the Git diff before publishing")


if __name__ == "__main__":
    main()
