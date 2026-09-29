"""Post a registry drift report to ntfy.

The verify report is the artifact of record; this only summarises it, using the
same ntfy transport as `scripts.deploy_fleet` so failures reach one channel.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from collections.abc import Sequence
from urllib.error import URLError
from urllib.request import Request, urlopen

MAX_LISTED = 10


def summarise(report: dict) -> tuple[str, str]:
    images = report.get("images", [])
    failed = [item for item in images if item.get("status") != "verified"]
    lines = [
        f"{len(failed)} of {len(images)} locked images failed verification.",
        "",
    ]
    for item in failed[:MAX_LISTED]:
        reasons = "; ".join(item.get("errors", [])) or "unknown"
        lines.append(f"{item['id']}: {reasons}")
    if len(failed) > MAX_LISTED:
        lines.append(f"... and {len(failed) - MAX_LISTED} more")
    lines.append("")
    lines.append(
        "Untagged manifests are collectable by default on this registry, so a "
        "missing digest is a pull-time outage, not just a stale lock."
    )
    title = (
        f"Registry drift: {len(failed)} image(s) unverified"
        if failed
        else "Registry drift: no failures"
    )
    return title, "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.ci.notify")
    parser.add_argument("label")
    parser.add_argument("report", type=pathlib.Path)
    args = parser.parse_args(argv)

    topic = os.getenv("NTFY_TOPIC")
    if not topic:
        print("NTFY_TOPIC is unset, skipping notification", file=sys.stderr)
        return 0

    title, message = summarise(json.loads(args.report.read_text(encoding="utf-8")))
    base = os.getenv("NTFY_URL", "https://ntfy.rupan.dev").rstrip("/")
    headers = {"Title": title, "Priority": "high", "Tags": "skull,warning"}
    token = os.getenv("NTFY_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urlopen(
            Request(
                f"{base}/{topic.lstrip('/')}",
                data=message.encode(),
                headers=headers,
                method="POST",
            ),
            timeout=5.0,
        ) as response:
            print(f"notified ntfy: {response.status}", file=sys.stderr)
    except (URLError, TimeoutError, OSError) as error:
        print(f"ntfy notification failed: {error}", file=sys.stderr)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
