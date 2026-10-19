from __future__ import annotations

import ipaddress
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from .errors import Code, ProtocolError

MAX_RESPONSE = 2097152


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        return None


class Api:
    def __init__(
        self,
        base: str,
        authorization: Callable[[bool], str],
        *,
        allow_plaintext: bool = False,
        ca_file: str | None = None,
    ):
        if not isinstance(base, str):
            raise ProtocolError(Code.INVALID_SPEC)
        try:
            url = urllib.parse.urlsplit(base)
            url.port
        except ValueError:
            raise ProtocolError(Code.INVALID_SPEC) from None
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
        ):
            raise ProtocolError(Code.INVALID_SPEC)
        loopback = url.hostname == "localhost"
        try:
            loopback = loopback or ipaddress.ip_address(url.hostname).is_loopback
        except ValueError:
            pass
        if url.scheme == "http" and not loopback and not allow_plaintext:
            raise ProtocolError(Code.INVALID_SPEC)
        self.base = base.rstrip("/")
        self.authorization = authorization
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            NoRedirect(),
            urllib.request.HTTPSHandler(
                context=ssl.create_default_context(cafile=ca_file)
            ),
        )

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: dict[str, Any] | None = None,
        missing: bool = False,
    ) -> Any:
        if (
            not path.startswith("/")
            or path.startswith("//")
            or ".." in path
            or "#" in path
            or method not in {"GET", "POST", "PUT", "PATCH"}
        ):
            raise ProtocolError(Code.INVALID_SPEC)
        headers = {
            "Accept": "application/json",
            "Authorization": self.authorization(method != "GET"),
        }
        data = None
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base + path, data=data, headers=headers, method=method
        )
        try:
            with self.opener.open(request, timeout=15) as response:
                raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise ProtocolError(Code.DEPENDENCY_UNAVAILABLE)
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            error.close()
            if error.code == 404 and missing:
                return None
            code = (
                Code.AUTHORIZATION_REQUIRED
                if error.code in {401, 403}
                else Code.RESOURCE_CONFLICT
                if error.code in {409, 412, 422}
                else Code.DEPENDENCY_UNAVAILABLE
            )
            raise ProtocolError(code) from None
        except (OSError, ValueError, urllib.error.URLError):
            raise ProtocolError(Code.DEPENDENCY_UNAVAILABLE) from None

    def pages(self, path: str) -> list[dict[str, Any]]:
        result = []
        separator = "&" if "?" in path else "?"
        for page in range(1, 21):
            values = self.request(f"{path}{separator}page={page}&limit=50&perPage=50")
            if not isinstance(values, list) or not all(
                isinstance(value, dict) for value in values
            ):
                raise ProtocolError(Code.DEPENDENCY_UNAVAILABLE)
            result.extend(values)
            if len(values) < 50:
                return result
        raise ProtocolError(Code.DEPENDENCY_UNAVAILABLE)


def segment(value: str) -> str:
    return urllib.parse.quote(value, safe="")
