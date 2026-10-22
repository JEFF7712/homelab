from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class APIError(RuntimeError):
    def __init__(self, method: str, path: str, status: int) -> None:
        self.status = status
        super().__init__(f"{method} {path}: HTTP {status}")


class Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(newurl)
        if (old.scheme, old.netloc) != (new.scheme, new.netloc):
            if new.scheme != "https":
                raise RuntimeError("Refusing a redirect to an insecure origin")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and (old.scheme, old.netloc) != (
            new.scheme,
            new.netloc,
        ):
            redirected.remove_header("Authorization")
        return redirected


class API:
    def __init__(self, origin: str, token: str, prefix: str = "") -> None:
        parsed = urllib.parse.urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path:
            raise ValueError("API origin must contain only a scheme and host")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("API origin must not contain credentials or parameters")
        self.origin = origin
        self.token = token
        self.prefix = prefix
        self.opener = urllib.request.build_opener(Redirects())

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json"
            if not self.prefix
            else "application/json",
            "User-Agent": "homelab-release-mirror",
        }

    def json(
        self, path: str, method: str = "GET", data: dict[str, Any] | None = None
    ) -> Any:
        request = urllib.request.Request(
            self.origin + self.prefix + path,
            data=json.dumps(data).encode() if data is not None else None,
            method=method,
            headers=dict(self.headers(), **{"Content-Type": "application/json"}),
        )
        try:
            with self.opener.open(request, timeout=60) as response:
                body = response.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as exc:
            raise APIError(method, path, exc.code) from None

    def pages(self, path: str, size_key: str = "per_page") -> list[dict[str, Any]]:
        result = []
        page = 1
        while True:
            batch = self.json(f"{path}?{size_key}=100&page={page}")
            if not isinstance(batch, list):
                raise RuntimeError(f"Expected an array from {path}")
            result.extend(batch)
            if len(batch) < 100:
                return result
            page += 1

    def download(
        self, path: str, destination: Path, *, binary_api: bool = False
    ) -> str:
        headers = self.headers()
        if binary_api:
            headers["Accept"] = "application/octet-stream"
        request = urllib.request.Request(self.origin + path, headers=headers)
        digest = hashlib.sha256()
        try:
            with self.opener.open(request, timeout=120) as response:
                with destination.open("wb") as output:
                    while block := response.read(1024 * 1024):
                        digest.update(block)
                        output.write(block)
        except urllib.error.HTTPError as exc:
            raise APIError("GET", path, exc.code) from None
        return "sha256:" + digest.hexdigest()

    def upload(
        self, repository: str, release_id: int, name: str, path: Path
    ) -> dict[str, Any]:
        upload_path = (
            f"/repos/{repository}/releases/{release_id}/assets?"
            + urllib.parse.urlencode({"name": name})
        )
        headers = self.headers() | {
            "Content-Type": "application/octet-stream",
            "Content-Length": str(path.stat().st_size),
        }
        connection = http.client.HTTPSConnection("uploads.github.com", timeout=120)
        try:
            with path.open("rb") as body:
                connection.request("POST", upload_path, body=body, headers=headers)
                response = connection.getresponse()
                if response.status != 201:
                    raise APIError("POST", upload_path, response.status)
                return json.loads(response.read())
        finally:
            connection.close()


def version(tag: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.-]+)?", tag)
    if match is None:
        return None
    return int(match[1]), int(match[2]), int(match[3])


def tag_commit(github: API, repository: str, tag: str) -> str:
    ref = github.json(
        f"/repos/{repository}/git/ref/tags/{urllib.parse.quote(tag, safe='')}"
    )
    obj = ref["object"]
    for _ in range(10):
        if obj["type"] == "commit":
            return obj["sha"]
        if obj["type"] != "tag":
            break
        obj = github.json(f"/repos/{repository}/git/tags/{obj['sha']}")["object"]
    raise RuntimeError(f"{tag}: GitHub tag does not resolve to a commit")


def asset_matches(
    github: API, repository: str, asset: dict[str, Any], digest: str
) -> bool:
    if asset.get("state") != "uploaded":
        return False
    if asset.get("digest"):
        return asset["digest"] == digest
    with tempfile.TemporaryDirectory(prefix="release-mirror-verify-") as temporary:
        actual = github.download(
            f"/repos/{repository}/releases/assets/{asset['id']}",
            Path(temporary) / "asset",
            binary_api=True,
        )
    return actual == digest


def mirror_release(
    forgejo: API, github: API, repository: str, source: dict[str, Any]
) -> dict[str, Any]:
    tag = source["tag_name"]
    encoded_tag = urllib.parse.quote(tag, safe="")
    source_commit = forgejo.json(f"/repos/{repository}/tags/{encoded_tag}")["commit"][
        "sha"
    ]
    if tag_commit(github, repository, tag) != source_commit:
        raise RuntimeError(
            f"{tag}: GitHub tag differs from Forgejo; waiting for Git mirror"
        )
    source_assets = forgejo.pages(
        f"/repos/{repository}/releases/{source['id']}/assets", "limit"
    )
    names = [asset["name"] for asset in source_assets]
    if len(names) != len(set(names)):
        raise RuntimeError(f"{tag}: duplicate source asset names")
    if any(
        not name or name in {".", ".."} or re.search(r"[/\\\x00-\x1f]", name)
        for name in names
    ):
        raise RuntimeError(f"{tag}: unsafe source asset name")
    # The tag endpoint returns only the published release. The release list
    # puts drafts first, so a stray draft for a published tag would otherwise
    # be chosen and its publish rejected with 422 on every run.
    try:
        destination = github.json(f"/repos/{repository}/releases/tags/{encoded_tag}")
    except APIError as error:
        if error.status != 404:
            raise
        destination = next(
            (
                release
                for release in github.pages(f"/repos/{repository}/releases")
                if release["tag_name"] == tag and release["draft"]
            ),
            None,
        )
    created = destination is None
    metadata = {
        "tag_name": tag,
        "name": source.get("name") or tag,
        "body": source.get("body") or "",
        "prerelease": bool(source.get("prerelease")),
    }
    if destination is None:
        destination = github.json(
            f"/repos/{repository}/releases", "POST", metadata | {"draft": True}
        )
    release_id = destination["id"]
    existing = {
        asset["name"]: asset
        for asset in github.pages(f"/repos/{repository}/releases/{release_id}/assets")
    }
    uploaded = 0
    with tempfile.TemporaryDirectory(prefix="release-mirror-") as temporary:
        for source_asset in source_assets:
            name = source_asset["name"]
            path = Path(temporary) / name
            digest = forgejo.download(
                f"/{repository}/releases/download/{encoded_tag}/"
                + urllib.parse.quote(name, safe=""),
                path,
            )
            if path.stat().st_size != source_asset["size"]:
                raise RuntimeError(
                    f"{tag}/{name}: downloaded size differs from Forgejo"
                )
            current = existing.get(name)
            if current is not None:
                if asset_matches(github, repository, current, digest):
                    continue
                if not destination["draft"]:
                    raise RuntimeError(
                        f"{tag}/{name}: refusing to replace a differing published asset"
                    )
                github.json(
                    f"/repos/{repository}/releases/assets/{current['id']}", "DELETE"
                )
            asset = github.upload(repository, release_id, name, path)
            if not asset_matches(github, repository, asset, digest):
                raise RuntimeError(
                    f"{tag}/{name}: uploaded asset hash differs from Forgejo"
                )
            uploaded += 1
    changed = destination["draft"] or any(
        destination.get(key) != value for key, value in metadata.items()
    )
    if changed:
        github.json(
            f"/repos/{repository}/releases/{release_id}",
            "PATCH",
            metadata | {"draft": False},
        )
    return {
        "tag": tag,
        "created": created,
        "updated": bool(changed),
        "uploaded_assets": uploaded,
    }


def synchronize(
    forgejo: API, github: API, repository: str, minimum: str
) -> list[dict[str, Any]]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Expected an explicit owner/repository")
    floor = version(minimum)
    if floor is None:
        raise ValueError("Minimum version must be a version tag")
    releases = forgejo.pages(f"/repos/{repository}/releases", "limit")
    selected = [
        release
        for release in releases
        if not release.get("draft")
        and release.get("published_at")
        and (parsed := version(release["tag_name"])) is not None
        and parsed >= floor
    ]
    selected.sort(
        key=lambda release: (version(release["tag_name"]), release["published_at"])
    )
    return [
        mirror_release(forgejo, github, repository, release) for release in selected
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Mirror published Forgejo release metadata and assets to GitHub"
    )
    parser.add_argument("--repository", required=True)
    parser.add_argument("--minimum-version", required=True)
    parser.add_argument("--forgejo-origin", default="http://git.internal:3000")
    args = parser.parse_args()
    try:
        forgejo = API(args.forgejo_origin, os.environ["FORGEJO_READ_TOKEN"], "/api/v1")
        github = API("https://api.github.com", os.environ["GITHUB_TOKEN"])
        results = synchronize(forgejo, github, args.repository, args.minimum_version)
    except (RuntimeError, ValueError, OSError, KeyError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}), flush=True)
        raise SystemExit(1) from None
    print(json.dumps({"status": "ok", "releases": results}), flush=True)


if __name__ == "__main__":
    main()
