#!/usr/bin/env python3
"""Boot a Soularr CronJob run: fetch pinned sources, install deps,
render config.ini from secret env, run soularr once."""
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

SOULARR_SHA = "0700090ed1c539455b05af9e16ab1a5bf8b9baff"
BASE = f"https://raw.githubusercontent.com/mrusse/Soularr/{SOULARR_SHA}"
WORK = Path("/tmp/soularr")
LIBS = Path("/tmp/pylibs")
CONFIG_DIR = Path("/config")


def fetch(name: str) -> None:
    dest = WORK / name
    if dest.is_file():
        return
    with urllib.request.urlopen(f"{BASE}/{name}", timeout=60) as resp:
        dest.write_bytes(resp.read())
    print(f"soularr-boot: fetched {name}")


def main() -> int:
    lidarr_key = os.environ.get("LIDARR_API_KEY", "").strip()
    slskd_key = os.environ.get("SLSKD_API_KEY", "").strip()
    missing = [
        name
        for name, value in (
            ("LIDARR_API_KEY", lidarr_key),
            ("SLSKD_API_KEY", slskd_key),
        )
        if not value
    ]
    if missing:
        print(
            "soularr-boot: missing required env: " + ", ".join(missing),
            file=sys.stderr,
        )
        return 1
    WORK.mkdir(parents=True, exist_ok=True)
    LIBS.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    fetch("requirements.txt")
    fetch("soularr.py")
    env = dict(os.environ)
    env["PIP_TARGET"] = str(LIBS)
    env["PYTHONPATH"] = str(LIBS)
    env["HOME"] = "/tmp"
    print("soularr-boot: installing dependencies")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            "--target",
            str(LIBS),
            "-r",
            str(WORK / "requirements.txt"),
        ],
        env=env,
    )
    if proc.returncode != 0:
        print("soularr-boot: pip install failed", file=sys.stderr)
        return proc.returncode
    template = Path("/app/config.ini.template").read_text(encoding="utf-8")
    rendered = template.replace("__LIDARR_API_KEY__", lidarr_key).replace(
        "__SLSKD_API_KEY__", slskd_key
    )
    if "__LIDARR_API_KEY__" in rendered or "__SLSKD_API_KEY__" in rendered:
        print("soularr-boot: placeholder left unreplaced", file=sys.stderr)
        return 1
    (CONFIG_DIR / "config.ini").write_text(rendered, encoding="utf-8")
    print("soularr-boot: running soularr")
    proc = subprocess.run(
        [sys.executable, str(WORK / "soularr.py"), "--config-dir", str(CONFIG_DIR)],
        env=env,
        cwd=str(WORK),
    )
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
