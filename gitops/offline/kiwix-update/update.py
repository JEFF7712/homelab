"""Keep the offline Kiwix library current without ever serving a bad file.

fetch: for each book in books.json, find the newest release in the Kiwix
OPDS catalog, download it into staging (resumable, rate limited), check its
size, SHA-256 and ZIM header, and rename it into zim/ beside the release it
replaces. Writes the files the library should list (newest release of each
book) and the books that failed. A failed book keeps its served release.

finish: wait until kiwix-serve lists exactly the library's books, load each
book's main page and, where it has a full-text index, run a search. Only
then move superseded releases to previous/ (one kept per book). Fails when
verification or any fetch failed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

CATALOG = "https://opds.library.kiwix.org/catalog/v2/entries"
ATOM = "{http://www.w3.org/2005/Atom}"
METALINK = "{urn:ietf:params:xml:ns:metalink}"
ZIM_MAGIC = 0x044D495A
RELEASE = re.compile(r"/(?P<stem>[^/]+)_(?P<date>\d{4}-\d{2})\.zim\.meta4$")
CHUNK = 1 << 20
USER_AGENT = "homelab-offline-library/1 (+https://kiwix.org)"
LIBRARY_FILES = "library-files.txt"
FAILED = "fetch-failed.json"


class UpdateError(Exception):
    pass


@dataclass(frozen=True)
class Book:
    name: str
    flavour: str = ""

    @property
    def stem(self) -> str:
        return f"{self.name}_{self.flavour}" if self.flavour else self.name


@dataclass(frozen=True)
class Release:
    stem: str
    date: str
    meta4: str

    @property
    def filename(self) -> str:
        return f"{self.stem}_{self.date}.zim"

    @property
    def url(self) -> str:
        return self.meta4.removesuffix(".meta4")


@dataclass(frozen=True)
class Metalink:
    size: int
    sha256: str


def load_books(path: Path) -> list[Book]:
    books = [Book(**entry) for entry in json.loads(path.read_text())["books"]]
    stems = [book.stem for book in books]
    if len(stems) != len(set(stems)):
        raise UpdateError("books.json lists a book twice")
    return books


def http_get(url: str, timeout: float = 60) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def latest_release(book: Book, feed: bytes) -> Release:
    """The newest release of exactly this name and flavour in an OPDS feed."""
    releases = []
    for link in ET.fromstring(feed).iter(f"{ATOM}link"):
        if "acquisition" not in link.get("rel", ""):
            continue
        match = RELEASE.search(link.get("href", ""))
        if match and match["stem"] == book.stem:
            releases.append(Release(book.stem, match["date"], link.attrib["href"]))
    if not releases:
        raise UpdateError(f"{book.stem}: not in the Kiwix catalog")
    return max(releases, key=lambda release: release.date)


def parse_metalink(document: bytes, filename: str) -> Metalink:
    for entry in ET.fromstring(document).iter(f"{METALINK}file"):
        if entry.get("name") != filename:
            continue
        size = entry.findtext(f"{METALINK}size")
        digest = next(
            (
                (hash_.text or "").strip().lower()
                for hash_ in entry.findall(f"{METALINK}hash")
                if hash_.get("type") == "sha-256"
            ),
            "",
        )
        if size is None or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise UpdateError(f"{filename}: metalink has no size or SHA-256")
        return Metalink(int(size), digest)
    raise UpdateError(f"{filename}: metalink does not describe this file")


def releases_of(directory: Path, stem: str, suffix: str = ".zim") -> list[Path]:
    """Files in `directory` holding releases of exactly this book, oldest first."""
    pattern = re.compile(rf"{re.escape(stem)}_\d{{4}}-\d{{2}}{re.escape(suffix)}")
    return sorted(path for path in directory.iterdir() if pattern.fullmatch(path.name))


def check_space(directory: Path, needed: int, reserve: int) -> None:
    free = shutil.disk_usage(directory).free
    if free - needed < reserve:
        raise UpdateError(
            f"needs {needed / 1e9:.1f} GB with {reserve / 1e9:.0f} GB spare, "
            f"{free / 1e9:.1f} GB free"
        )


def download(url: str, part: Path, size: int, rate: int) -> None:
    """Resume `part` up to `size` bytes, sleeping to stay under `rate` B/s."""
    offset = part.stat().st_size if part.exists() else 0
    if offset > size:
        part.unlink()
        offset = 0
    if offset == size:
        return
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Range": f"bytes={offset}-"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        if offset and response.status != 206:
            raise UpdateError(f"{url}: server ignored the resume range")
        started = time.monotonic()
        copied = 0
        with part.open("ab") as output:
            while chunk := response.read(CHUNK):
                output.write(chunk)
                copied += len(chunk)
                ahead = copied / rate - (time.monotonic() - started)
                if ahead > 0:
                    time.sleep(ahead)
    if part.stat().st_size != size:
        raise UpdateError(f"{url}: got {part.stat().st_size} of {size} bytes")


def verify_file(path: Path, expected: Metalink) -> None:
    if path.stat().st_size != expected.size:
        raise UpdateError(f"{path.name}: size {path.stat().st_size} != {expected.size}")
    with path.open("rb") as handle:
        if int.from_bytes(handle.read(4), "little") != ZIM_MAGIC:
            raise UpdateError(f"{path.name}: not a ZIM file")
        handle.seek(0)
        digest = hashlib.sha256()
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    if digest.hexdigest() != expected.sha256:
        raise UpdateError(f"{path.name}: SHA-256 mismatch")


def update_book(
    root: Path,
    book: Book,
    *,
    fetch: Callable[[str], bytes],
    transfer: Callable[[str, Path, int], None],
    reserve: int,
) -> str:
    query = urllib.parse.urlencode({"name": book.name, "count": 100})
    release = latest_release(book, fetch(f"{CATALOG}?{query}"))
    current = releases_of(root / "zim", book.stem)
    if current and current[-1].name >= release.filename:
        return f"{book.stem}: current ({current[-1].name})"
    expected = parse_metalink(fetch(release.meta4), release.filename)
    part = root / "staging" / f"{release.filename}.part"
    # A partial download of a release the catalog has since replaced only
    # takes space.
    for stale in releases_of(root / "staging", book.stem, ".zim.part"):
        if stale != part:
            stale.unlink()
    done = part.stat().st_size if part.exists() else 0
    check_space(root, max(expected.size - done, 0), reserve)
    transfer(release.url, part, expected.size)
    try:
        verify_file(part, expected)
    except UpdateError:
        part.unlink()
        raise
    os.replace(part, root / "zim" / release.filename)
    return f"{book.stem}: installed {release.filename}"


def library_files(root: Path, books: list[Book]) -> list[Path]:
    """The newest release of each book plus any ZIM no book claims."""
    claimed: set[Path] = set()
    newest = []
    for book in books:
        releases = releases_of(root / "zim", book.stem)
        claimed.update(releases)
        newest.extend(releases[-1:])
    unclaimed = sorted(set((root / "zim").glob("*.zim")) - claimed)
    return sorted(newest) + unclaimed


def fetch_all(args: argparse.Namespace) -> int:
    root: Path = args.root
    for directory in ("zim", "previous", "staging"):
        (root / directory).mkdir(exist_ok=True)
    books = load_books(args.books)
    failed = []
    for book in books:
        try:
            result = update_book(
                root,
                book,
                fetch=http_get,
                transfer=lambda url, part, size: download(url, part, size, args.rate),
                reserve=args.reserve,
            )
            print(result, flush=True)
        except (UpdateError, OSError, ET.ParseError) as error:
            failed.append(book.stem)
            print(f"{book.stem}: FAILED: {error}", file=sys.stderr, flush=True)
    files = library_files(root, books)
    claimed = {path for book in books for path in releases_of(root / "zim", book.stem)}
    for path in files:
        if path not in claimed:
            print(f"{path.name}: not in books.json; still served", flush=True)
    write_atomic(root / LIBRARY_FILES, "".join(f"zim/{p.name}\n" for p in files))
    write_atomic(root / FAILED, json.dumps(failed) + "\n")
    return 0


def write_atomic(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text)
    os.replace(temporary, path)


def catalog_books(feed: bytes) -> list[tuple[str, str, bool]]:
    """(book id, content path, has full-text index) for each served book."""
    books = []
    for entry in ET.fromstring(feed).iter(f"{ATOM}entry"):
        book_id = (entry.findtext(f"{ATOM}id") or "").removeprefix("urn:uuid:")
        content = next(
            (
                link.get("href", "")
                for link in entry.iter(f"{ATOM}link")
                if link.get("type") == "text/html"
            ),
            "",
        )
        indexed = "_ftindex:yes" in (entry.findtext(f"{ATOM}tags") or "").split(";")
        books.append((book_id, content, indexed))
    return books


def library_ids(library: Path) -> set[str]:
    return {book.attrib["id"] for book in ET.parse(library).getroot().iter("book")}


def verify_server(root: Path, url: str, timeout: float) -> list[str]:
    """Problems with the live server; empty when it serves the library."""
    wanted = library_ids(root / "library.xml")
    deadline = time.monotonic() + timeout
    while True:
        served = catalog_books(http_get(f"{url}/catalog/v2/entries?count=-1"))
        if {book_id for book_id, _, _ in served} == wanted:
            break
        if time.monotonic() > deadline:
            return [f"server lists {len(served)} books, library has {len(wanted)}"]
        time.sleep(10)
    problems = []
    for book_id, content, indexed in served:
        checks = [f"{url}{content}"]
        if indexed:
            checks.append(f"{url}/search?books.id={book_id}&pattern=the")
        for check in checks:
            try:
                http_get(check, timeout=120)
            except OSError as error:
                problems.append(f"{check}: {error}")
    print(f"verified {len(served)} books, {len(problems)} problems", flush=True)
    return problems


def rotate(root: Path, books: list[Book]) -> list[str]:
    """Move superseded releases to previous/, keeping one per book."""
    moved = []
    for book in books:
        for old in releases_of(root / "zim", book.stem)[:-1]:
            os.replace(old, root / "previous" / old.name)
            moved.append(f"{old.name} -> previous/")
        for old in releases_of(root / "previous", book.stem)[:-1]:
            old.unlink()
            moved.append(f"{old.name} deleted")
    return moved


def finish(args: argparse.Namespace) -> int:
    root: Path = args.root
    problems = verify_server(root, args.url, args.timeout)
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print("superseded releases left in zim/ for rollback", file=sys.stderr)
    else:
        for line in rotate(root, load_books(args.books)):
            print(line, flush=True)
    failed = json.loads((root / FAILED).read_text())
    if failed:
        print(f"fetch failed for: {', '.join(failed)}", file=sys.stderr)
    return 1 if problems or failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch")
    fetch.add_argument("--root", type=Path, required=True)
    fetch.add_argument("--books", type=Path, required=True)
    fetch.add_argument("--rate", type=int, default=20 << 20, help="bytes per second")
    fetch.add_argument("--reserve", type=int, default=100 << 30, help="bytes kept free")
    done = commands.add_parser("finish")
    done.add_argument("--root", type=Path, required=True)
    done.add_argument("--books", type=Path, required=True)
    done.add_argument("--url", required=True)
    done.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args(argv)
    return fetch_all(args) if args.command == "fetch" else finish(args)


if __name__ == "__main__":
    raise SystemExit(main())
