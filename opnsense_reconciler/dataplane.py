import argparse
import json
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class Probe(Protocol):
    def __call__(self, url: str, timeout: float) -> tuple[bool, int | None]: ...


@dataclass(frozen=True)
class DataPlaneProof:
    unreachable: set[str]
    ready: bool


def _desired_targets(targets: object) -> list[dict[str, object]]:
    if not isinstance(targets, list) or not all(
        isinstance(item, dict) for item in targets
    ):
        raise ValueError("data-plane targets must contain a list of objects")
    desired: list[dict[str, object]] = []
    for item in targets:
        target = {"url": item.get("url"), "expect_status": item.get("expect_status")}
        url, expect_status = target["url"], target["expect_status"]
        if not isinstance(url, str) or not url.startswith("http://"):
            raise ValueError("data-plane target requires an http:// url")
        if not isinstance(expect_status, int):
            raise ValueError(f"data-plane target {url} requires integer expect_status")
        desired.append(target)
    if not desired:
        raise ValueError("data-plane targets must not be empty")
    return desired


def verify_lb_data_plane(
    targets: list[dict[str, object]],
    results: Mapping[str, tuple[bool, int | None]],
) -> DataPlaneProof:
    desired = _desired_targets(targets)
    unreachable = {
        str(target["url"])
        for target in desired
        if results.get(str(target["url"])) != (True, target["expect_status"])
    }
    return DataPlaneProof(unreachable=unreachable, ready=not unreachable)


def probe_target(
    url: str,
    timeout: float,
    opener: Callable[..., object] | None = None,
) -> tuple[bool, int | None]:
    open_url = opener if opener is not None else urllib.request.urlopen
    try:
        response = open_url(url, timeout=timeout)
    except urllib.error.HTTPError as error:
        # The VIP answered, so the route works; the status decides readiness.
        print(f"{url}: HTTP {error.code}", file=sys.stderr)
        return True, error.code
    except (urllib.error.URLError, OSError, ValueError) as error:
        reason = getattr(error, "reason", error)
        print(f"{url}: {type(reason).__name__}: {reason}", file=sys.stderr)
        return False, None
    status = getattr(response, "status", None)
    close = getattr(response, "close", None)
    if callable(close):
        close()
    return True, status if isinstance(status, int) else None


def probe_targets(
    targets: list[dict[str, object]],
    timeout: float,
    probe: Probe = probe_target,
) -> dict[str, tuple[bool, int | None]]:
    desired = _desired_targets(targets)
    return {
        str(target["url"]): probe(str(target["url"]), timeout) for target in desired
    }


def main(
    argv: list[str] | None = None,
    environ: Mapping[str, str] | None = None,
    probe: Probe = probe_target,
) -> None:
    del environ
    parser = argparse.ArgumentParser(
        description="Probe LoadBalancer VIPs through OPNsense to prove the data plane"
    )
    parser.add_argument("--targets", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=10.0)
    arguments = parser.parse_args(argv)
    targets = json.loads(arguments.targets.read_text())
    results = probe_targets(targets, arguments.timeout, probe)
    proof = verify_lb_data_plane(targets, results)
    print(
        json.dumps(
            {
                "checked": len(results),
                "unreachable": sorted(proof.unreachable),
                "ready": proof.ready,
            },
            sort_keys=True,
        )
    )
    if not proof.ready:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
