#!/usr/bin/env python3
"""Read-only track metadata lookup over the Navidrome library database.

Serves GET /lookup?isrc=&artist=&title=&duration= and returns the best local
match with its genres, BPM and ISRCs, so Home Assistant can choose music
lighting from local tags. The database is opened read-only per request.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger("navidrome_lookup")

DURATION_TOLERANCE_SECONDS = 5.0
ARTIST_SEPARATORS = re.compile(
    r"\s*(?:,|&|;|/|•|\bx\b|\bfeat\.?|\bft\.?|\bfeaturing\b|\bwith\b|\band\b)\s*",
    re.IGNORECASE,
)
TITLE_DECORATION = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]")
TITLE_SUFFIX = re.compile(r"\s+-\s+.*$")

ROW_COLUMNS = (
    "title",
    "artist",
    "album",
    "duration",
    "bpm",
    "json_extract(tags, '$.genre')",
    "json_extract(tags, '$.isrc')",
)
SELECT_ROWS = f"SELECT {', '.join(ROW_COLUMNS)} FROM media_file WHERE missing = 0"


def normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", ascii_only.lower())


def clean_title(title: str) -> str:
    """Drop "(feat. X)", "[Remastered]" and " - Radio Edit" decorations."""
    base = TITLE_DECORATION.sub("", title)
    base = TITLE_SUFFIX.sub("", base)
    return base.strip() or title.strip()


def artist_names(artist: str) -> set[str]:
    names = {normalize(part) for part in ARTIST_SEPARATORS.split(artist)}
    names.discard("")
    whole = normalize(artist)
    if whole:
        names.add(whole)
    return names


@dataclass(frozen=True)
class Track:
    title: str
    artist: str
    album: str
    duration: float | None
    bpm: int | None
    genres: tuple[str, ...]
    isrcs: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "duration": self.duration,
            "bpm": self.bpm,
            "genres": list(self.genres),
            "isrcs": list(self.isrcs),
        }


def _tag_values(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    try:
        entries = json.loads(raw)
    except ValueError:
        return ()
    values: list[str] = []
    for entry in entries if isinstance(entries, list) else []:
        value = entry.get("value") if isinstance(entry, dict) else entry
        if isinstance(value, str) and value.strip() and value not in values:
            values.append(value.strip())
    return tuple(values)


def track_from_row(row: tuple[Any, ...]) -> Track:
    title, artist, album, duration, bpm, genres, isrcs = row
    return Track(
        title=title or "",
        artist=artist or "",
        album=album or "",
        duration=float(duration) if duration else None,
        bpm=int(bpm) if bpm else None,
        genres=_tag_values(genres),
        isrcs=_tag_values(isrcs),
    )


def score(track: Track, artist: str, title: str, duration: float | None) -> float:
    """Return a match score, or 0 when the track is not the same recording."""
    if normalize(clean_title(track.title)) != normalize(clean_title(title)):
        return 0.0
    wanted = artist_names(artist)
    found = artist_names(track.artist)
    if not wanted & found and not any(
        w in f or f in w for w in wanted for f in found if min(len(w), len(f)) >= 4
    ):
        return 0.0
    result = 1.0
    if duration and track.duration:
        delta = abs(duration - track.duration)
        if delta > DURATION_TOLERANCE_SECONDS * 3:
            return 0.0
        if delta <= DURATION_TOLERANCE_SECONDS:
            result += 1.0
    if track.genres:
        result += 0.5
    if track.bpm:
        result += 0.25
    return result


def best_match(
    tracks: list[Track], artist: str, title: str, duration: float | None
) -> Track | None:
    ranked = [(score(track, artist, title, duration), track) for track in tracks]
    ranked = [entry for entry in ranked if entry[0] > 0]
    if not ranked:
        return None
    return max(ranked, key=lambda entry: entry[0])[1]


def _like_prefix(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escaped}%"


class Library:
    def __init__(self, database: str) -> None:
        self.database = database

    def _query(self, sql: str, params: tuple[Any, ...]) -> list[Track]:
        uri = f"file:{self.database}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            rows = connection.execute(sql, params).fetchall()
        finally:
            connection.close()
        return [track_from_row(row) for row in rows]

    def by_isrc(self, isrc: str) -> list[Track]:
        sql = (
            f"{SELECT_ROWS} AND EXISTS (SELECT 1 FROM json_each(media_file.tags, "
            "'$.isrc') WHERE upper(json_extract(value, '$.value')) = ?) LIMIT 20"
        )
        return self._query(sql, (isrc.strip().upper(),))

    def by_title(self, title: str) -> list[Track]:
        sql = f"{SELECT_ROWS} AND title LIKE ? ESCAPE '\\' LIMIT 200"
        return self._query(sql, (_like_prefix(clean_title(title)),))

    def lookup(
        self, isrc: str, artist: str, title: str, duration: float | None
    ) -> tuple[Track | None, str | None]:
        if isrc:
            tracks = self.by_isrc(isrc)
            if tracks:
                match = best_match(tracks, artist, title, duration) if title else None
                return match or max(tracks, key=lambda t: len(t.genres)), "isrc"
        if artist and title:
            match = best_match(self.by_title(title), artist, title, duration)
            if match:
                return match, "title"
        return None, None


def make_handler(library: Library) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: dict[str, Any]) -> None:
            payload = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            url = urlparse(self.path)
            if url.path == "/healthz":
                self._send(200, {"status": "ok"})
                return
            if url.path != "/lookup":
                self._send(404, {"error": "not found"})
                return
            query = {k: v[0] for k, v in parse_qs(url.query).items() if v}
            try:
                duration = float(query["duration"]) if "duration" in query else None
            except ValueError:
                self._send(400, {"error": "duration must be a number"})
                return
            try:
                match, matched_by = library.lookup(
                    query.get("isrc", ""),
                    query.get("artist", ""),
                    query.get("title", ""),
                    duration,
                )
            except sqlite3.Error as error:
                logger.warning("library query failed: %s", error)
                self._send(503, {"error": "library unavailable"})
                return
            self._send(
                200,
                {
                    "match": match.as_dict() if match else None,
                    "matched_by": matched_by,
                },
            )

        def log_message(self, format: str, *args: Any) -> None:
            logger.debug(format, *args)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="/data/navidrome.db")
    parser.add_argument("--listen", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=4534)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    server = ThreadingHTTPServer(
        (args.listen, args.port), make_handler(Library(args.database))
    )
    logger.info("serving on %s:%d", args.listen, args.port)
    server.serve_forever()


if __name__ == "__main__":
    main()
