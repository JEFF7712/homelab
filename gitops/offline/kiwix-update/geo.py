"""Keep US place search (Photon) and directions (Valhalla) current and offline.

Everything lives under the archive's maps/ tree on nas-01, so a server can be
rebuilt from it without the internet:

    search/photon-<YYYYMMDD>/photon_data   unpacked Photon US index
    search/photon-<version>.jar            Photon server, pinned by SHA-256
    search/current -> photon-<YYYYMMDD>
    routing/us-<YYMMDD>/valhalla_tiles.tar Valhalla tiles plus valhalla.json
    routing/current -> us-<YYMMDD>

fetch: once the served data is older than geo.json allows, download the
newest Photon US index (MD5 from the dump server) and unpack it beside the
served one, and download the newest Geofabrik US extract (MD5 from
Geofabrik) and ask the build step to turn it into Valhalla tiles. Downloads
resume and stay under the rate cap.

build-routing.sh (Valhalla image) builds the tiles on node-local disk and
copies them into routing/.

finish: point current at the new data, restart maps-search and
maps-routing, then search for a place and plan a route through
maps.homelab. Only then delete older data; on failure, point current back
and restart again. Fails when a fetch, build or check failed.
"""

from __future__ import annotations

import argparse
import datetime
import email.utils
import hashlib
import json
import os
import re
import shutil
import ssl
import sys
import tarfile
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update

PHOTON_DIR = re.compile(r"photon-(?P<date>\d{8})")
ROUTING_DIR = re.compile(r"us-(?P<date>\d{6})")
EXTRACT = re.compile(r"/(?P<name>us-(?P<date>\d{6}))\.osm\.pbf$")
BUILD_REQUEST = "geo-build.json"
STATE = "geo-state.json"
FAILED = "geo-failed.json"
DEPLOYMENTS = {"search": "maps-search", "routing": "maps-routing"}
SERVICE_ACCOUNT = Path("/var/run/secrets/kubernetes.io/serviceaccount")


@dataclass(frozen=True)
class Remote:
    """A file to download: where, under which local name, how big, its MD5."""

    url: str
    name: str
    size: int
    md5: str


def head(url: str) -> tuple[str, int, str]:
    """Final URL (after redirects), size and Last-Modified of a download."""
    request = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": update.USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return (
            response.url,
            int(response.headers["Content-Length"]),
            response.headers.get("Last-Modified", ""),
        )


def md5_of(listing: bytes) -> str:
    """The checksum from an `md5sum`-style file."""
    digest = listing.decode().split()[0].lower() if listing.strip() else ""
    if not re.fullmatch(r"[0-9a-f]{32}", digest):
        raise update.UpdateError("checksum file holds no MD5")
    return digest


def served(directory: Path, pattern: re.Pattern[str]) -> tuple[str, str] | None:
    """(directory name, date) that `current` points at, if any."""
    link = directory / "current"
    if not link.is_symlink():
        return None
    match = pattern.fullmatch(os.readlink(link))
    return (match.group(0), match["date"]) if match else None


def age_days(date: str, today: datetime.date) -> int:
    format_ = "%Y%m%d" if len(date) == 8 else "%y%m%d"
    return (today - datetime.datetime.strptime(date, format_).date()).days


def point(directory: Path, target: str) -> None:
    """Point `current` at `target` in one rename."""
    temporary = directory / ".current.tmp"
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target)
    os.replace(temporary, directory / "current")


def verify_md5(path: Path, remote: Remote) -> None:
    if path.stat().st_size != remote.size:
        raise update.UpdateError(
            f"{path.name}: size {path.stat().st_size} != {remote.size}"
        )
    digest = hashlib.md5(usedforsecurity=False)
    read = 0
    with path.open("rb") as handle:
        while chunk := handle.read(update.CHUNK):
            digest.update(chunk)
            read += len(chunk)
            if read % update.FLUSH_EVERY < len(chunk):
                update.drop_cache(handle)
    if digest.hexdigest() != remote.md5:
        raise update.UpdateError(f"{path.name}: MD5 mismatch")


def fetch_verified(
    root: Path,
    remote: Remote,
    transfer: Callable[[str, Path, int], None],
    reserve: int,
    stale: str,
) -> Path:
    """Download `remote` into staging/, verified; drop stale partials matching `stale`."""
    staging = root / "staging"
    staging.mkdir(exist_ok=True)
    done = staging / remote.name
    if done.exists():
        return done
    part = staging / f"{remote.name}.part"
    for old in staging.glob(stale):
        if old != part:
            old.unlink()
    have = part.stat().st_size if part.exists() else 0
    update.check_space(root, max(remote.size - have, 0), reserve)
    transfer(remote.url, part, remote.size)
    try:
        verify_md5(part, remote)
    except update.UpdateError:
        part.unlink()
        raise
    os.replace(part, done)
    return done


def unpack_photon(archive: Path, target: Path) -> None:
    """Unpack only photon_data/ from the dump into `target`, then rename it in."""
    building = target.with_name(f"{target.name}.next")
    shutil.rmtree(building, ignore_errors=True)
    building.mkdir()
    with tarfile.open(archive, mode="r|bz2") as dump:
        for member in dump:
            if member.name != "photon_data" and not member.name.startswith(
                "photon_data/"
            ):
                raise update.UpdateError(f"{archive.name}: unexpected {member.name}")
            dump.extract(member, building, filter="data")
    if not (building / "photon_data").is_dir():
        raise update.UpdateError(f"{archive.name}: no photon_data/")
    os.replace(building, target)


def update_search(
    root: Path,
    config: dict[str, Any],
    *,
    head: Callable[[str], tuple[str, int, str]],
    fetch: Callable[[str], bytes],
    transfer: Callable[[str, Path, int], None],
    unpack: Callable[[Path, Path], None],
    reserve: int,
    max_age_days: int,
    today: datetime.date,
) -> tuple[str, str | None]:
    """Unpack a newer Photon index; (message, directory to switch to)."""
    directory = root / "search"
    directory.mkdir(exist_ok=True)
    current = served(directory, PHOTON_DIR)
    if current and age_days(current[1], today) < max_age_days:
        return f"search: current ({current[0]})", None
    url, size, modified = head(config["url"])
    built = email.utils.parsedate_to_datetime(modified).strftime("%Y%m%d")
    name = f"photon-{built}"
    if current and current[0] >= name:
        return f"search: current ({current[0]}, newest dump)", None
    if not (directory / name).is_dir():
        remote = Remote(
            url, f"photon-db-usa-{built}.tar.bz2", size, md5_of(fetch(f"{url}.md5"))
        )
        # The index unpacks to about three times the download.
        update.check_space(root, 3 * size, reserve)
        archive = fetch_verified(
            root, remote, transfer, reserve, "photon-db-usa-*.tar.bz2*"
        )
        unpack(archive, directory / name)
        archive.unlink()
    return f"search: unpacked {name}", name


def install_jar(root: Path, jar: dict[str, str], fetch: Callable[[str], bytes]) -> str:
    target = root / "search" / jar["path"]
    if target.exists():
        return f"search server: current ({jar['path']})"
    data = fetch(jar["url"])
    if hashlib.sha256(data).hexdigest() != jar["sha256"]:
        raise update.UpdateError(f"{jar['url']}: SHA-256 mismatch")
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, target)
    return f"search server: installed {jar['path']}"


def prepare_routing(
    root: Path,
    config: dict[str, Any],
    *,
    head: Callable[[str], tuple[str, int, str]],
    fetch: Callable[[str], bytes],
    transfer: Callable[[str, Path, int], None],
    reserve: int,
    max_age_days: int,
    today: datetime.date,
) -> tuple[str, str | None]:
    """Download a newer US extract and request a build; (message, built directory)."""
    directory = root / "routing"
    directory.mkdir(exist_ok=True)
    current = served(directory, ROUTING_DIR)
    if current and age_days(current[1], today) < max_age_days:
        return f"routing: current ({current[0]})", None
    url, size, _ = head(config["url"])
    match = EXTRACT.search(url)
    if match is None:
        raise update.UpdateError(f"{url}: not a dated US extract")
    name = match["name"]
    if current and current[0] >= name:
        return f"routing: current ({current[0]}, newest extract)", None
    if (directory / name / "valhalla_tiles.tar").exists():
        return f"routing: built {name}", name
    remote = Remote(url, f"{name}.osm.pbf", size, md5_of(fetch(f"{url}.md5")))
    pbf = fetch_verified(root, remote, transfer, reserve, "us-*.osm.pbf*")
    update.write_atomic(
        root / BUILD_REQUEST,
        json.dumps({"pbf": f"staging/{pbf.name}", "out": f"routing/{name}"}) + "\n",
    )
    return f"routing: build requested for {name}", name


def fetch_geo(args: argparse.Namespace) -> int:
    root: Path = args.maps_root
    config = json.loads(args.geo.read_text())
    (root / BUILD_REQUEST).unlink(missing_ok=True)
    common: dict[str, Any] = {
        "head": head,
        "fetch": update.http_get,
        "transfer": lambda url, part, size: update.download(url, part, size, args.rate),
        "reserve": args.reserve,
        "max_age_days": config["max_age_days"],
        "today": datetime.date.today(),
    }
    failed: list[str] = []
    pending: dict[str, str] = {}
    steps: list[tuple[str, Callable[[], tuple[str, str | None]]]] = [
        (
            "search",
            lambda: update_search(
                root, config["search"], unpack=unpack_photon, **common
            ),
        ),
        ("routing", lambda: prepare_routing(root, config["routing"], **common)),
    ]
    try:
        (root / "search").mkdir(exist_ok=True)
        print(install_jar(root, config["search"]["jar"], update.http_get), flush=True)
    except (update.UpdateError, OSError) as error:
        failed.append("search server")
        print(f"search server: FAILED: {error}", file=sys.stderr, flush=True)
    for part, step in steps:
        try:
            message, target = step()
            print(message, flush=True)
            if target:
                pending[part] = target
        except (update.UpdateError, OSError, ValueError, tarfile.TarError) as error:
            failed.append(part)
            print(f"{part}: FAILED: {error}", file=sys.stderr, flush=True)
    update.write_atomic(root / STATE, json.dumps(pending) + "\n")
    update.write_atomic(root / FAILED, json.dumps(failed) + "\n")
    return 0


def switch(root: Path, pending: dict[str, str]) -> dict[str, str | None]:
    """Point each part's current at its new directory; the previous targets."""
    previous: dict[str, str | None] = {}
    for part, target in pending.items():
        directory = root / part
        if not (directory / target).is_dir():
            raise update.UpdateError(f"{part}: {target} was not built")
        link = directory / "current"
        previous[part] = os.readlink(link) if link.is_symlink() else None
        point(directory, target)
    return previous


def restore(root: Path, previous: dict[str, str | None]) -> None:
    for part, target in previous.items():
        if target:
            point(root / part, target)
        else:
            (root / part / "current").unlink(missing_ok=True)


def prune(root: Path) -> list[str]:
    """Delete data current no longer points at, and leftover sources."""
    removed = []
    for part, pattern in (("search", PHOTON_DIR), ("routing", ROUTING_DIR)):
        current = served(root / part, pattern)
        for path in sorted((root / part).iterdir()):
            if pattern.fullmatch(path.name) and current and path.name != current[0]:
                shutil.rmtree(path)
                removed.append(f"{part}/{path.name} deleted")
    for source in sorted((root / "staging").glob("us-*.osm.pbf")):
        source.unlink()
        removed.append(f"staging/{source.name} deleted")
    return removed


class Cluster:
    """Restart a Deployment and wait for it, through the pod's service account."""

    def __init__(self, namespace: str = "offline") -> None:
        self.base = "https://kubernetes.default.svc"
        self.namespace = namespace
        self.token = (SERVICE_ACCOUNT / "token").read_text()
        self.context = ssl.create_default_context(cafile=SERVICE_ACCOUNT / "ca.crt")

    def call(self, method: str, name: str, body: bytes | None = None) -> Any:
        request = urllib.request.Request(
            f"{self.base}/apis/apps/v1/namespaces/{self.namespace}/deployments/{name}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/strategic-merge-patch+json",
            },
        )
        with urllib.request.urlopen(request, timeout=30, context=self.context) as reply:
            return json.load(reply)

    def restart(self, name: str) -> None:
        stamp = datetime.datetime.now(datetime.UTC).isoformat()
        patch = {
            "spec": {
                "template": {
                    "metadata": {"annotations": {"offline/restarted-at": stamp}}
                }
            }
        }
        self.call("PATCH", name, json.dumps(patch).encode())

    def ready(self, name: str) -> bool:
        deployment = self.call("GET", name)
        status, spec = deployment["status"], deployment["spec"]
        return (
            status.get("observedGeneration", 0) >= deployment["metadata"]["generation"]
            and status.get("updatedReplicas", 0) == spec["replicas"]
            and status.get("availableReplicas", 0) == spec["replicas"]
        )


def roll(cluster: Any, parts: list[str], timeout: float) -> list[str]:
    """Restart the servers of `parts` and wait until they are available."""
    names = [DEPLOYMENTS[part] for part in parts]
    for name in names:
        cluster.restart(name)
    deadline = time.monotonic() + timeout
    waiting = list(names)
    while waiting and time.monotonic() < deadline:
        time.sleep(30)
        waiting = [name for name in waiting if not cluster.ready(name)]
    return [f"{name}: not available after {timeout:.0f}s" for name in waiting]


def http_post(url: str, body: Any, headers: dict[str, str]) -> bytes:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "User-Agent": update.USER_AGENT,
            "Content-Type": "application/json",
            **headers,
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def verify_geo(url: str, host: str, check: dict[str, Any]) -> list[str]:
    """Search for a place and plan a route through maps.homelab."""
    headers = {"Host": host}
    problems = []
    query = urllib.parse.urlencode({"q": check["search"], "limit": 1})
    try:
        found = json.loads(
            update.http_get(f"{url}/api/search?{query}", headers=headers)
        )
        if not found.get("features"):
            problems.append(f"search for {check['search']!r} found nothing")
    except (OSError, ValueError) as error:
        problems.append(f"search: {error}")
    route = {
        "locations": check["route"],
        "costing": "auto",
        "directions_options": {"units": "miles"},
    }
    try:
        trip = json.loads(http_post(f"{url}/api/route", route, headers))["trip"]
        if not trip.get("legs") or trip["summary"]["length"] <= 0:
            problems.append("route has no legs")
    except (OSError, ValueError, KeyError) as error:
        problems.append(f"route: {error}")
    print(f"verified search and directions, {len(problems)} problems", flush=True)
    return problems


def finish_geo(
    args: argparse.Namespace,
    cluster: Any = None,
    verify: Callable[..., list[str]] = verify_geo,
) -> int:
    root: Path = args.maps_root
    config = json.loads(args.geo.read_text())
    pending: dict[str, str] = json.loads((root / STATE).read_text())
    failed: list[str] = json.loads((root / FAILED).read_text())
    if (root / BUILD_REQUEST).exists():
        # The build step consumes its request; one left over means it failed.
        failed.append("routing build")
        pending.pop("routing", None)
    problems: list[str] = []
    previous: dict[str, str | None] = {}
    try:
        previous = switch(root, pending)
    except update.UpdateError as error:
        problems.append(str(error))
    if previous:
        cluster = cluster or Cluster()
        for part, target in previous.items():
            print(f"{part}: serving {pending[part]} (was {target})", flush=True)
        problems += roll(cluster, sorted(previous), args.timeout)
    if not problems:
        problems += verify(args.maps_url, args.maps_host, config["check"])
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems and previous:
        restore(root, previous)
        print("restored the previous search and routing data", file=sys.stderr)
        roll(cluster, sorted(previous), args.timeout)
    elif not problems:
        for line in prune(root):
            print(line, flush=True)
    if failed:
        print(f"failed: {', '.join(failed)}", file=sys.stderr)
    return 1 if problems or failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch")
    fetch.add_argument("--maps-root", type=Path, required=True)
    fetch.add_argument("--geo", type=Path, required=True, help="geo.json")
    fetch.add_argument("--rate", type=int, default=20 << 20, help="bytes per second")
    fetch.add_argument("--reserve", type=int, default=100 << 30, help="bytes kept free")
    done = commands.add_parser("finish")
    done.add_argument("--maps-root", type=Path, required=True)
    done.add_argument("--geo", type=Path, required=True)
    done.add_argument("--maps-url", required=True)
    done.add_argument("--maps-host", default="maps.homelab")
    done.add_argument("--timeout", type=float, default=3600)
    args = parser.parse_args(argv)
    return fetch_geo(args) if args.command == "fetch" else finish_geo(args)


if __name__ == "__main__":
    raise SystemExit(main())
