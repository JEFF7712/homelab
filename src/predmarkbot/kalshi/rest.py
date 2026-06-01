"""Async REST client for Kalshi with optional request signing and retry."""
from __future__ import annotations

import asyncio
import json as _json
from typing import Any, Self

import httpx

from predmarkbot.kalshi.auth import KalshiSigner


class KalshiApiError(RuntimeError):
    def __init__(self, status: int, body: object) -> None:
        super().__init__(f"Kalshi API error {status}: {body!r}")
        self.status = status
        self.body = body


class KalshiRestClient:
    """Thin wrapper around httpx.AsyncClient that handles signing + retry."""

    def __init__(
        self,
        *,
        base_url: str,
        signer: KalshiSigner | None,
        timeout_seconds: float = 10.0,
        retry_max: int = 3,
        retry_base_delay: float = 0.5,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._signer = signer
        self._retry_max = retry_max
        self._retry_base_delay = retry_base_delay
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._client.aclose()

    async def get(self, path: str, *, signed: bool = False) -> dict[str, Any]:
        return await self._request("GET", path, signed=signed)

    async def post(
        self, path: str, *, json: dict[str, Any], signed: bool = False
    ) -> dict[str, Any]:
        return await self._request("POST", path, json=json, signed=signed)

    async def delete(self, path: str, *, signed: bool = False) -> dict[str, Any]:
        return await self._request("DELETE", path, signed=signed)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        signed: bool = False,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = self._base_url + path
        attempt = 0
        while True:
            attempt += 1
            headers: dict[str, str] = {}
            if signed:
                if self._signer is None:
                    raise RuntimeError("signed=True but no signer configured")
                # NOTE: signing path is the full URL path including /trade-api/v2/...
                signing_path = path if path.startswith("/trade-api") else f"/trade-api/v2{path}"
                headers.update(self._signer.sign(method=method, path=signing_path))

            try:
                resp = await self._client.request(method, url, headers=headers, json=json)
            except httpx.HTTPError as exc:
                if attempt >= self._retry_max:
                    raise KalshiApiError(0, str(exc)) from exc
                await asyncio.sleep(self._retry_base_delay * (2 ** (attempt - 1)))
                continue

            if 200 <= resp.status_code < 300:
                return resp.json() if resp.content else {}
            if resp.status_code == 429:
                # Rate-limited: back off and retry like 5xx (but with a
                # bigger base delay so we don't immediately hammer again).
                if attempt >= self._retry_max:
                    raise KalshiApiError(429, resp.text)
                backoff = max(self._retry_base_delay * (2 ** attempt), 1.0)
                await asyncio.sleep(backoff)
                continue
            if 400 <= resp.status_code < 500:
                # Client error: don't retry
                try:
                    body: object = resp.json()
                except (ValueError, _json.JSONDecodeError):
                    body = resp.text
                raise KalshiApiError(resp.status_code, body)
            # 5xx or other: retry
            if attempt >= self._retry_max:
                raise KalshiApiError(resp.status_code, resp.text)
            await asyncio.sleep(self._retry_base_delay * (2 ** (attempt - 1)))
