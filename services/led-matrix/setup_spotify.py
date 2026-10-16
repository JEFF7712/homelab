"""Authorize Spotify using a local form without putting credentials in chat."""

from __future__ import annotations

import argparse
import html
import json
import secrets
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import cast

import requests

from spotify_matrix_streamer import (
    AUTH_URL,
    SCOPE,
    TOKEN_URL,
    SpotifyClient,
    write_private_file,
)

REDIRECT_URI = "http://127.0.0.1:8888/callback"
ENV_PATH = Path(__file__).with_name(".env")
TOKEN_PATH = Path.home() / ".cache" / "spotify_matrix_token.json"


class SpotifySetup:
    def __init__(
        self, env_path: Path = ENV_PATH, token_path: Path = TOKEN_PATH
    ) -> None:
        self.env_path = env_path
        self.token_path = token_path
        self.form_nonce = secrets.token_urlsafe(32)
        self.oauth_state = secrets.token_urlsafe(32)
        self.client_id = ""
        self.client_secret = ""
        self.complete = False

    def handler(self) -> type[BaseHTTPRequestHandler]:
        setup = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

            def page(self, title: str, body: str, status: int = 200) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
                )
                self.end_headers()
                document = f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title><style>
body {{background:#141817;color:#edf2ef;font:17px system-ui;max-width:480px;margin:10vh auto;padding:24px}}
h1 {{font-size:28px}} label {{display:block;margin:24px 0 8px}}
input,button {{box-sizing:border-box;width:100%;font:inherit;border-radius:7px;padding:12px}}
input {{background:#202a25;color:inherit;border:1px solid #597365}}
button {{margin-top:24px;background:#7ee5a4;border:0;color:#102218;cursor:pointer}}
p {{line-height:1.6;color:#bfcfc5}} code {{overflow-wrap:anywhere}}
</style><h1>{html.escape(title)}</h1>{body}</html>"""
                self.wfile.write(document.encode())

            def do_GET(self) -> None:
                if (
                    self.headers.get("Host")
                    != f"127.0.0.1:{cast(HTTPServer, self.server).server_port}"
                ):
                    self.page(
                        "Invalid host", "<p>Use the loopback setup address.</p>", 400
                    )
                    return
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path == "/":
                    self.page(
                        "Connect Spotify to your matrix",
                        f"""
<p>Enter the credentials from your Spotify developer app. They stay on this laptop
in private files. You will then continue to Spotify to approve access to your currently playing track.</p>
<form action="/connect" method="post" autocomplete="off">
<input type="hidden" name="nonce" value="{setup.form_nonce}">
<label for="client_id">Client ID</label><input id="client_id" name="client_id" required spellcheck="false">
<label for="client_secret">Client secret</label><input id="client_secret" name="client_secret" type="password" required autocomplete="new-password">
<button type="submit">Continue to Spotify</button></form>
<p>Registered redirect URI:<br><code>{REDIRECT_URI}</code></p>""",
                    )
                    return
                if parsed.path != "/callback":
                    self.page("Not found", "<p>Return to the setup page.</p>", 404)
                    return
                params = urllib.parse.parse_qs(parsed.query)
                state = params.get("state", [""])[0]
                if (
                    not secrets.compare_digest(
                        state.encode(), setup.oauth_state.encode()
                    )
                    or not setup.client_id
                ):
                    self.page(
                        "Authorization rejected",
                        "<p>The authorization state did not match. Return to the setup page.</p>",
                        400,
                    )
                    return
                code = params.get("code", [""])[0]
                if not code or "error" in params:
                    self.page(
                        "Spotify access was not granted",
                        "<p><a href='/'>Try again</a> when you are ready.</p>",
                        400,
                    )
                    return
                try:
                    response = requests.post(
                        TOKEN_URL,
                        auth=(setup.client_id, setup.client_secret),
                        data={
                            "grant_type": "authorization_code",
                            "code": code,
                            "redirect_uri": REDIRECT_URI,
                        },
                        timeout=15,
                    )
                    response.raise_for_status()
                    token = response.json()
                    if not token.get("access_token") or not token.get("refresh_token"):
                        raise ValueError("Incomplete token response")
                except (requests.RequestException, ValueError):
                    self.page(
                        "Spotify authorization failed",
                        "<p>Check the client credentials and registered redirect URI, then <a href='/'>try again</a>.</p>",
                        400,
                    )
                    return
                try:
                    configuration = {
                        "SPOTIFY_CLIENT_ID": setup.client_id,
                        "SPOTIFY_CLIENT_SECRET": setup.client_secret,
                        "SPOTIFY_REDIRECT_URI": REDIRECT_URI,
                        "PICO_MATRIX_IP": "10.0.20.131",
                    }
                    write_private_file(
                        setup.env_path,
                        "".join(
                            f"{key}={json.dumps(value)}\n"
                            for key, value in configuration.items()
                        ),
                    )
                    client = SpotifyClient(
                        setup.client_id,
                        setup.client_secret,
                        REDIRECT_URI,
                        setup.token_path,
                    )
                    client._save_token(token)
                except OSError:
                    self.page(
                        "Could not save authorization",
                        "<p>Check local file permissions, then try setup again.</p>",
                        500,
                    )
                    return
                setup.client_secret = ""
                setup.complete = True
                self.page(
                    "Spotify connected",
                    "<p>Authorization is saved. You can close this tab and start playing a track in Spotify.</p>",
                )

            def do_POST(self) -> None:
                if (
                    self.headers.get("Host")
                    != f"127.0.0.1:{cast(HTTPServer, self.server).server_port}"
                ):
                    self.page(
                        "Invalid host", "<p>Use the loopback setup address.</p>", 400
                    )
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                if self.path != "/connect" or not 0 < length <= 4096:
                    self.page("Invalid request", "<p>Return to setup.</p>", 400)
                    return
                params = urllib.parse.parse_qs(
                    self.rfile.read(length).decode(errors="replace")
                )
                if not secrets.compare_digest(
                    params.get("nonce", [""])[0].encode(), setup.form_nonce.encode()
                ):
                    self.page("Invalid request", "<p>Reload the setup page.</p>", 403)
                    return
                client_id = params.get("client_id", [""])[0].strip()
                client_secret = params.get("client_secret", [""])[0].strip()
                if (
                    not client_id
                    or not client_secret
                    or any(c in client_id + client_secret for c in "\r\n\x00")
                ):
                    self.page(
                        "Credentials required",
                        "<p>Enter both client credentials.</p>",
                        400,
                    )
                    return
                setup.client_id, setup.client_secret = client_id, client_secret
                setup.oauth_state = secrets.token_urlsafe(32)
                query = urllib.parse.urlencode(
                    {
                        "client_id": client_id,
                        "response_type": "code",
                        "redirect_uri": REDIRECT_URI,
                        "scope": SCOPE,
                        "state": setup.oauth_state,
                    }
                )
                self.send_response(303)
                self.send_header("Location", f"{AUTH_URL}?{query}")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()

        return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=1800)
    args = parser.parse_args()
    setup = SpotifySetup()
    with HTTPServer(("127.0.0.1", 8888), setup.handler()) as server:
        server.timeout = 1
        print("Spotify setup ready at http://127.0.0.1:8888", flush=True)
        deadline = time.monotonic() + args.timeout
        while not setup.complete and time.monotonic() < deadline:
            server.handle_request()
    if not setup.complete:
        raise SystemExit("Spotify setup expired before authorization completed")
    print("Spotify authorization saved in private local files.", flush=True)


if __name__ == "__main__":
    main()
