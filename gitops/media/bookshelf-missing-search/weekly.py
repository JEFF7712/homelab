"""Weekly Bookshelf maintenance: rescan, clear imported downloads, search.

Bookshelf's own import is off (Calibre imports books; docs/runbooks/books.md),
so finished downloads stay in its queue. This job:
  1. rescans the library so books Calibre imported get their files,
  2. removes queue entries whose book now has a file (Bookshelf only: the
     torrent keeps seeding and nothing is blocklisted),
  3. searches every monitored book that is still missing.
Any API failure exits nonzero so the CronJob surfaces it.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

BASE = os.environ.get("BOOKSHELF_URL", "http://bookshelf.media") + "/api/v1"
TIMEOUT = 60
POLL_SECONDS = 10
RESCAN_TIMEOUT = 1800


class ApiError(Exception):
    pass


def api(method: str, path: str, body: dict | None = None) -> Any:
    key = os.environ.get("BOOKSHELF_API_KEY", "")
    if not key:
        raise ApiError("missing BOOKSHELF_API_KEY")
    headers = {"X-Api-Key": key, "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ApiError(f"{method} {path}: {e}") from e


def run_command(
    call: Callable[..., Any],
    name: str,
    sleep: Callable[[float], None] = time.sleep,
    timeout: float = RESCAN_TIMEOUT,
) -> None:
    cmd = call("POST", "/command", {"name": name})
    waited = 0.0
    while True:
        status = call("GET", f"/command/{cmd['id']}")["status"]
        if status == "completed":
            return
        if status in ("failed", "aborted", "cancelled", "orphaned"):
            raise ApiError(f"{name} ended {status}")
        if waited >= timeout:
            raise ApiError(f"{name} still {status} after {timeout:.0f}s")
        sleep(POLL_SECONDS)
        waited += POLL_SECONDS


def books_with_files(call: Callable[..., Any], book_ids: set[int]) -> set[int]:
    owned: set[int] = set()
    for book_id in sorted(book_ids):
        book = call("GET", f"/book/{book_id}")
        if (book.get("statistics") or {}).get("bookFileCount"):
            owned.add(book_id)
    return owned


def clear_imported(call: Callable[..., Any]) -> list[str]:
    records = call("GET", "/queue?pageSize=500")["records"]
    finished = [
        r for r in records if r.get("status") == "completed" and r.get("bookId")
    ]
    owned = books_with_files(call, {r["bookId"] for r in finished})
    removed = []
    for r in finished:
        if r["bookId"] in owned:
            call(
                "DELETE",
                f"/queue/{r['id']}?removeFromClient=false&blocklist=false",
            )
            removed.append(r.get("title", str(r["id"])))
    return removed


def main(call: Callable[..., Any] = api) -> int:
    try:
        run_command(call, "RescanFolders")
        for title in clear_imported(call):
            print(f"cleared from queue: {title}")
        call("POST", "/command", {"name": "MissingBookSearch", "monitored": True})
        print("missing-book search queued")
    except ApiError as e:
        print(f"FAIL: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
