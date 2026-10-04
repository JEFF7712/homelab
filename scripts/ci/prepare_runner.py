from __future__ import annotations

import argparse
import json
from pathlib import Path

from scripts.ci.policy import IMAGE


def configuration(validation_token: str, operations_token: str) -> str:
    validation_token = validation_token.strip()
    operations_token = operations_token.strip()
    if not validation_token.strip() or not operations_token.strip():
        raise ValueError("Both newly registered runner tokens are required")
    if validation_token == operations_token:
        raise ValueError("Validation and operation runners require distinct tokens")
    runners = []
    for name, token in [
        ("homelab-dr-validation", validation_token),
        ("homelab-dr-operations", operations_token),
    ]:
        runners.append(
            f"""
[[runners]]
  name = {json.dumps(name)}
  url = "https://gitlab.com/"
  token = {json.dumps(token.strip())}
  executor = "docker"
  limit = 1
  environment = ["GIT_TERMINAL_PROMPT=0"]
  [runners.docker]
    image = {json.dumps(IMAGE)}
    privileged = false
    disable_cache = true
    volumes = []
    dns = ["10.0.30.10"]
    memory = "8g"
    cpus = "2"
"""
        )
    return "concurrent = 2\ncheck_interval = 3\n" + "".join(runners)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validation-token-file", type=Path, required=True)
    parser.add_argument("--operations-token-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    body = configuration(
        args.validation_token_file.read_text(), args.operations_token_file.read_text()
    )
    with args.output.open("x") as stream:
        args.output.chmod(0o600)
        stream.write(body)


if __name__ == "__main__":
    main()
