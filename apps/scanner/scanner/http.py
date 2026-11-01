"""Shared HTTP client with per-host politeness, retries and geo-block detection."""

import asyncio
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from scanner import config

RETRY_STATUSES = {429, 500, 502, 503, 504}
GEO_BLOCK_STATUS = 571  # Link11/Reblaze page "access from abroad is restricted"


class SourceBlockedError(RuntimeError):
    """The municipal firewall refused us, almost always because the IP is outside Israel."""


class FileTooLargeError(RuntimeError):
    """The server announced a file bigger than the caller's limit; nothing was downloaded."""

    def __init__(self, url: str, size: int) -> None:
        super().__init__(f"{url} is {size / 1_000_000:.0f} MB")
        self.size = size


class Fetcher:
    def __init__(self, *, attempts: int = 3, timeout: float = 60.0) -> None:
        self._client = httpx.AsyncClient(
            headers={"User-Agent": config.USER_AGENT},
            timeout=timeout,
            follow_redirects=True,
        )
        self._attempts = attempts
        self._slots: dict[str, asyncio.Semaphore] = {}
        self._last_start: dict[str, float] = defaultdict(float)
        self._pace_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def __aenter__(self) -> "Fetcher":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        return self._client

    @staticmethod
    def _polite(host: str) -> bool:
        return host.endswith(config.POLITE_HOST_SUFFIXES)

    def _slot(self, host: str) -> asyncio.Semaphore:
        if host not in self._slots:
            limit = config.PER_HOST_CONCURRENCY if self._polite(host) else config.CDN_CONCURRENCY
            self._slots[host] = asyncio.Semaphore(limit)
        return self._slots[host]

    async def _pace(self, host: str) -> None:
        if not self._polite(host):
            return
        async with self._pace_locks[host]:
            wait = self._last_start[host] + config.PER_HOST_MIN_INTERVAL_S - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_start[host] = time.monotonic()

    async def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        host = urlsplit(url).hostname or ""
        last_error: Exception | None = None
        async with self._slot(host):
            for attempt in range(self._attempts):
                await self._pace(host)
                try:
                    response = await self._client.request(method, url, **kwargs)
                except (httpx.TimeoutException, httpx.TransportError) as err:
                    last_error = err
                else:
                    if response.status_code == GEO_BLOCK_STATUS:
                        raise SourceBlockedError(
                            f"{host} refused the request (HTTP 571): the municipality blocks non-Israeli IPs"
                        )
                    if response.status_code not in RETRY_STATUSES:
                        return response
                    last_error = httpx.HTTPStatusError(
                        f"HTTP {response.status_code}", request=response.request, response=response
                    )
                await asyncio.sleep(1.5 * 2**attempt)
        assert last_error is not None
        raise last_error

    async def get(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs) -> httpx.Response:
        return await self.request("POST", url, **kwargs)

    async def get_json(self, url: str, **kwargs) -> dict:
        response = await self.get(url, **kwargs)
        response.raise_for_status()
        return response.json()

    async def download(
        self, url: str, dest: Path, *, stalls: int = 4, max_tries: int = 60, max_bytes: int | None = None
    ) -> int:
        """Stream a file to disk, resuming with Range requests when the server drops the connection.

        Keeps resuming as long as attempts make progress; gives up after `stalls` attempts in a row add nothing.
        With `max_bytes`, a larger announced size raises FileTooLargeError before any body is read.
        """
        host = urlsplit(url).hostname or ""
        dest.parent.mkdir(parents=True, exist_ok=True)
        partial = dest.with_name(dest.name + ".part")
        partial.unlink(missing_ok=True)
        last_error: Exception | None = None
        stalled = 0
        async with self._slot(host):
            for _ in range(max_tries):
                if stalled >= stalls:
                    break
                await self._pace(host)
                have = partial.stat().st_size if partial.exists() else 0
                headers = {"Range": f"bytes={have}-"} if have else {}
                try:
                    async with self._client.stream("GET", url, headers=headers) as response:
                        if response.status_code == GEO_BLOCK_STATUS:
                            raise SourceBlockedError(f"{host} refused the request (HTTP 571)")
                        if response.status_code in RETRY_STATUSES:
                            raise httpx.HTTPStatusError(
                                f"HTTP {response.status_code}", request=response.request, response=response
                            )
                        response.raise_for_status()
                        size = int(response.headers.get("content-length") or 0)
                        if max_bytes and response.status_code == 200 and size > max_bytes:
                            raise FileTooLargeError(url, size)
                        resumed = have and response.status_code == 206
                        with partial.open("ab" if resumed else "wb") as handle:
                            async for chunk in response.aiter_bytes(256 * 1024):
                                handle.write(chunk)
                    partial.replace(dest)
                    return dest.stat().st_size
                except SourceBlockedError:
                    raise
                except (httpx.TransportError, httpx.HTTPStatusError) as err:
                    if isinstance(err, httpx.HTTPStatusError) and err.response.status_code not in RETRY_STATUSES:
                        raise
                    last_error = err
                progressed = (partial.stat().st_size if partial.exists() else 0) > have
                stalled = 0 if progressed else stalled + 1
                await asyncio.sleep(1 if progressed else min(2 * 2**stalled, 20))
        partial.unlink(missing_ok=True)
        assert last_error is not None
        raise last_error
