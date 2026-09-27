from __future__ import annotations

import json
import os
import sys


def render_cloud_bulbs(bulbs_json: str) -> str:
    try:
        bulbs = json.loads(bulbs_json)
    except json.JSONDecodeError as error:
        raise ValueError(f"ROKU_CLOUD_BULBS_JSON is not valid JSON: {error}") from error
    if not isinstance(bulbs, list) or not bulbs:
        raise ValueError("ROKU_CLOUD_BULBS_JSON must be a non-empty list")

    lines = ["bulbs:"]
    for entry in bulbs:
        name = str(entry.get("name", "")).strip()
        mac = str(entry.get("mac", "")).replace(":", "").upper()
        if not name:
            raise ValueError("each bulb needs a non-empty name")
        if len(mac) != 12 or any(c not in "0123456789ABCDEF" for c in mac):
            raise ValueError(f"bulb {name!r} has an invalid mac (want 12 hex chars)")
        lines += [
            f"  - name: {name}",
            f"    mac: {mac}",
        ]
    return "\n".join(lines) + "\n"


def render_cookies(cookies_json: str) -> str:
    try:
        cookies = json.loads(cookies_json)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"ROKU_CLOUD_COOKIES_JSON is not valid JSON: {error}"
        ) from error
    if not isinstance(cookies, dict) or not cookies:
        raise ValueError("ROKU_CLOUD_COOKIES_JSON must be a non-empty object")
    if "ks.session" not in cookies:
        raise ValueError("ROKU_CLOUD_COOKIES_JSON must contain ks.session")
    return json.dumps(cookies)


def main() -> int:
    try:
        mode = os.environ.get("ROKU_CLOUD_RENDER_MODE", "bulbs")
        if mode == "cookies":
            print(render_cookies(os.environ["ROKU_CLOUD_COOKIES_JSON"]), end="")
        else:
            print(render_cloud_bulbs(os.environ["ROKU_CLOUD_BULBS_JSON"]), end="")
    except (KeyError, ValueError) as error:
        print(error, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
