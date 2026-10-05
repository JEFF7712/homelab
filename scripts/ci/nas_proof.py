from __future__ import annotations

import socket
from collections.abc import Sequence


def prove(targets: Sequence[tuple[str, int]]) -> None:
    print("Executor:", socket.gethostname())
    for host, port in targets:
        with socket.create_connection((host, port), timeout=5):
            print(f"Connected to {host}:{port}")


def main() -> None:
    prove([("10.0.30.20", 8080), ("10.0.30.11", 6443)])


if __name__ == "__main__":
    main()
