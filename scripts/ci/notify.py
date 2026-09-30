"""Post a registry drift report to ntfy.

The verify report is the artifact of record; this only summarises it, using the
same ntfy transport as `scripts.deploy_fleet` so failures reach one channel.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
from collections.abc import Sequence
from urllib.error import URLError
from urllib.request import Request, urlopen

MAX_LISTED = 10
# ntfy rejects a message over a few KiB with a 400, and skopeo's own text is
# long and repetitive, so reasons are condensed before they are sent.
MAX_REASON = 220
MAX_MESSAGE = 3500


def reason_for(item: dict) -> str:
    """One failure reason, stripped of the tool's own noise and bounded."""
    reasons = item.get("errors") or []
    text = "; ".join(str(value) for value in reasons) or "unknown"
    # `time="..." level=fatal msg="..."` prefixes are repeated per image and
    # carry nothing the reason does not already say.
    text = re.sub(r'time="[^"]*"\s*', "", text)
    text = re.sub(r"level=\w+\s*", "", text)
    text = re.sub(r'\bmsg="?', "", text)
    text = text.replace('\\"', "").rstrip('"')
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_REASON:
        # Keep both ends: skopeo puts the cause last ("... authentication
        # required"), so a plain head truncation would cut off the only part
        # worth reading.
        head = int(MAX_REASON * 0.45)
        tail = MAX_REASON - head - len(" ... ")
        text = f"{text[:head].rstrip()} ... {text[-tail:].lstrip()}"
    return text


def summarise(report: dict) -> tuple[str, str]:
    images = report.get("images", [])
    failed = [
        item
        for item in images
        if item.get("status") not in {"verified", "indeterminate"}
    ]
    # A record the check could not reach is not evidence of drift, so it is
    # called out separately rather than folded into a phantom failure count.
    indeterminate = [item for item in images if item.get("status") == "indeterminate"]
    lines = [
        f"{len(failed)} of {len(images)} locked images failed verification.",
        "",
    ]
    for item in failed[:MAX_LISTED]:
        lines.append(f"{item['id']}: {reason_for(item)}")
    if len(failed) > MAX_LISTED:
        lines.append(f"... and {len(failed) - MAX_LISTED} more")
    if indeterminate:
        lines.append("")
        lines.append(
            f"{len(indeterminate)} image(s) were inconclusive because the registry "
            "was unreachable or intercepted, not because they drifted:"
        )
        for item in indeterminate[:MAX_LISTED]:
            lines.append(f"{item['id']}: {reason_for(item)}")
        if len(indeterminate) > MAX_LISTED:
            lines.append(f"... and {len(indeterminate) - MAX_LISTED} more")
    if failed:
        lines.append("")
        lines.append(
            "Untagged manifests are collectable by default on this registry, so a "
            "missing digest is a pull-time outage, not just a stale lock."
        )
    title = (
        f"Registry drift: {len(failed)} image(s) unverified"
        if failed
        else f"Registry check inconclusive: {len(indeterminate)} unreachable"
        if indeterminate
        else "Registry drift: no failures"
    )
    message = "\n".join(lines)
    if len(message) > MAX_MESSAGE:
        message = message[: MAX_MESSAGE - 4].rstrip() + "\n…"
    return title, message


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
