"""Core HTTP engine with proxy rotation, rate limiting, retries."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Optional

import httpx
from fake_useragent import UserAgent

logger = logging.getLogger("scraper.engine")


class ScraperEngine:
    """Manages HTTP sessions with proxy rotation, rate limiting, and retries."""

    def __init__(
        self,
        proxies: list[str] | None = None,
        delay_min: float = 1.0,
        delay_max: float = 3.0,
        backoff_base: float = 5.0,
        max_retries: int = 3,
        timeout: float = 30.0,
    ):
        self.proxies = proxies or []
        self._proxy_index = 0
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.backoff_base = backoff_base
        self.max_retries = max_retries
        self.timeout = timeout
        self._ua = UserAgent()
        self._client: httpx.AsyncClient | None = None
        self._last_request_time = 0.0
        self._request_count = 0

    def _get_proxy(self) -> str | None:
        if not self.proxies:
            return None
        proxy = self.proxies[self._proxy_index % len(self.proxies)]
        self._proxy_index += 1
        return proxy

    def _get_headers(self) -> dict[str, str]:
        return {
            "User-Agent": self._ua.random,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,it;q=0.8,de;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            proxy = self._get_proxy()
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                follow_redirects=True,
                proxy=proxy,
                headers=self._get_headers(),
            )
        return self._client

    async def _rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_time
        delay = random.uniform(self.delay_min, self.delay_max)
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)
        self._last_request_time = time.monotonic()

    async def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        """GET request with rate limiting and retries."""
        return await self._request("GET", url, params=params, headers=headers, timeout=timeout)

    async def _request(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        last_exc: Exception | None = None

        for attempt in range(self.max_retries + 1):
            await self._rate_limit()
            client = await self._ensure_client()

            req_headers = self._get_headers()
            if headers:
                req_headers.update(headers)

            req_timeout = httpx.Timeout(timeout) if timeout else None

            try:
                resp = await client.request(
                    method, url, params=params, headers=req_headers,
                    timeout=req_timeout, **kwargs,
                )
                self._request_count += 1

                if resp.status_code == 429:
                    wait = self.backoff_base * (2 ** attempt)
                    logger.warning(f"Rate limited (429). Waiting {wait:.1f}s...")
                    await asyncio.sleep(wait)
                    # Rotate proxy on rate limit
                    await self.close()
                    continue

                if resp.status_code == 403:
                    logger.warning(f"Forbidden (403) on {url}. Rotating proxy...")
                    await self.close()
                    continue

                resp.raise_for_status()
                return resp

            except httpx.TimeoutException as e:
                last_exc = e
                logger.warning(f"Timeout on attempt {attempt + 1}: {e}")
                await self.close()
            except httpx.HTTPStatusError as e:
                last_exc = e
                logger.warning(f"HTTP error {e.response.status_code} on attempt {attempt + 1}")
                if e.response.status_code >= 500:
                    await asyncio.sleep(self.backoff_base * (2 ** attempt))
                    await self.close()
                else:
                    raise
            except httpx.HTTPError as e:
                last_exc = e
                logger.warning(f"HTTP error on attempt {attempt + 1}: {e}")
                await asyncio.sleep(self.backoff_base * (2 ** attempt))
                await self.close()

        raise last_exc or RuntimeError(f"Failed after {self.max_retries + 1} attempts")

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    @property
    def request_count(self) -> int:
        return self._request_count
