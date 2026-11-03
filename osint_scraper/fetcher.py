"""Async fetch layer built on httpx.

The public interface is unchanged: :meth:`Fetcher.fetch`,
:meth:`Fetcher.fetch_text` and :meth:`Fetcher.is_allowed` still return the same
results and raise the same :class:`FetchError`. Internally requests are async,
share one connection pool, and pass through a per-host rate limiter.
"""

from __future__ import annotations

import asyncio
import random
import re
import time
from typing import Callable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4.dammit import UnicodeDammit

from .config import (
    BASE_HEADERS,
    DEFAULT_BACKOFF,
    DEFAULT_MAX_BYTES,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT,
    HTML_CONTENT_TYPES,
    MAX_REDIRECTS,
    MAX_RETRY_AFTER,
    RETRYABLE_STATUS,
    SCRIPT_CONTENT_TYPES,
    USER_AGENTS,
)
from .models import FetchResult
from .urls import origin

_CHARSET_RE = re.compile(r"charset=([\w\-]+)", re.IGNORECASE)


class FetchError(Exception):
    """A fetch failed. ``kind`` is stable and safe to branch on.

    Kinds: timeout, connection, dns, ssl, redirects, http, content_type,
    too_large, empty, robots, request.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: str = "request",
        status_code: int | None = None,
        retryable: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code
        self.retryable = retryable
        self.retry_after = retry_after


class HostRateLimiter:
    """Enforce a minimum delay between request starts for each host."""

    def __init__(self, delay: float) -> None:
        self.delay = max(delay, 0.0)
        self._next: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def wait(self, url: str) -> None:
        if self.delay <= 0:
            return
        host = urlsplit(url).netloc.lower()
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            ready_at = self._next.get(host, 0.0)
            if ready_at > now:
                await asyncio.sleep(ready_at - now)
            self._next[host] = loop.time() + self.delay


class Fetcher:
    """Async HTTP client with retry, backoff, UA rotation and robots caching."""

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        backoff: float = DEFAULT_BACKOFF,
        max_bytes: int = DEFAULT_MAX_BYTES,
        user_agent: str | None = None,
        delay: float = 0.0,
        on_event: Callable[[str], None] | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.max_bytes = max_bytes
        self.fixed_user_agent = user_agent
        self.on_event = on_event
        self.client = client or httpx.AsyncClient(
            follow_redirects=True,
            max_redirects=MAX_REDIRECTS,
        )
        self._limiter = HostRateLimiter(delay)
        self._robots: dict[str, RobotFileParser | None] = {}
        self._robots_tasks: dict[str, asyncio.Task[RobotFileParser | None]] = {}

    async def __aenter__(self) -> "Fetcher":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self.client.aclose()

    # ------------------------------------------------------------------ public
    async def fetch(self, url: str) -> FetchResult:
        """Fetch an HTML page, retrying transient failures with exponential backoff."""
        return await self._fetch(
            url, HTML_CONTENT_TYPES, is_html=True, max_bytes=self.max_bytes
        )

    async def fetch_text(
        self,
        url: str,
        *,
        max_bytes: int | None = None,
        content_types: frozenset[str] | None = None,
    ) -> str:
        """Fetch a JavaScript/text/XML resource and return its decoded body."""
        result = await self._fetch(
            url,
            content_types or SCRIPT_CONTENT_TYPES,
            is_html=False,
            max_bytes=max_bytes or self.max_bytes,
        )
        return result.html

    async def is_allowed(self, url: str) -> bool:
        """Check robots.txt for ``url`` using the wildcard (``*``) rules.

        Robots rules are cached per origin. A 401/403 on robots.txt means
        "disallow all"; other 4xx, 5xx or network failures mean "no rules were
        published", so crawling is permitted.
        """
        key = origin(url)
        if key not in self._robots:
            task = self._robots_tasks.get(key)
            if task is None:
                task = asyncio.create_task(self._load_robots(key))
                self._robots_tasks[key] = task
            try:
                self._robots[key] = await task
            finally:
                self._robots_tasks.pop(key, None)
        rules = self._robots[key]
        return True if rules is None else rules.can_fetch("*", url)

    async def _load_robots(self, origin_url: str) -> RobotFileParser | None:
        robots_url = f"{origin_url}/robots.txt"
        await self._limiter.wait(robots_url)
        try:
            response = await self.client.get(
                robots_url,
                headers=self._headers(),
                timeout=self.timeout,
            )
        except httpx.HTTPError:
            return None
        if response.status_code in (401, 403):
            parser = RobotFileParser()
            parser.parse(["User-agent: *", "Disallow: /"])
            return parser
        if response.status_code != 200:
            return None
        parser = RobotFileParser()
        parser.parse(response.text.splitlines())
        return parser

    # ----------------------------------------------------------------- private
    async def _fetch(
        self,
        url: str,
        content_types: frozenset[str],
        *,
        is_html: bool,
        max_bytes: int,
    ) -> FetchResult:
        total_attempts = self.retries + 1
        for attempt in range(1, total_attempts + 1):
            try:
                return await self._attempt(
                    url,
                    attempt,
                    content_types=content_types,
                    is_html=is_html,
                    max_bytes=max_bytes,
                )
            except FetchError as exc:
                if not exc.retryable or attempt >= total_attempts:
                    raise
                wait = self.backoff * (2 ** (attempt - 1)) + random.uniform(0, 0.25)
                if exc.retry_after:
                    wait = max(wait, min(exc.retry_after, MAX_RETRY_AFTER))
                self._emit(f"{exc} - retry {attempt}/{self.retries} in {wait:.1f}s")
                await asyncio.sleep(wait)
        raise FetchError("Retry loop exited unexpectedly.")  # pragma: no cover

    def _emit(self, message: str) -> None:
        if self.on_event:
            self.on_event(message)

    def _pick_user_agent(self) -> str:
        return self.fixed_user_agent or random.choice(USER_AGENTS)

    def _headers(self, user_agent: str | None = None) -> dict[str, str]:
        return {**BASE_HEADERS, "User-Agent": user_agent or self._pick_user_agent()}

    async def _attempt(
        self,
        url: str,
        attempt: int,
        *,
        content_types: frozenset[str],
        is_html: bool,
        max_bytes: int,
    ) -> FetchResult:
        user_agent = self._pick_user_agent()
        started = time.perf_counter()
        await self._limiter.wait(url)
        try:
            async with self.client.stream(
                "GET",
                url,
                headers=self._headers(user_agent),
                timeout=self.timeout,
            ) as response:
                self._check_status(response)
                content_type = (
                    response.headers.get("Content-Type", "").split(";")[0].strip().lower()
                )
                if content_type and content_type not in content_types:
                    expected = "HTML" if is_html else "JavaScript or text"
                    raise FetchError(
                        f"Unsupported content type '{content_type}' (expected {expected})",
                        kind="content_type",
                    )
                body = await self._read_body(response, max_bytes)
                final_url = str(response.url)
                status_code = response.status_code
                reason = response.reason_phrase or ""
                raw_content_type = response.headers.get("Content-Type", "")
                response_headers = dict(response.headers)
        except httpx.TooManyRedirects as exc:
            raise FetchError(
                f"Gave up after {MAX_REDIRECTS} redirects (possible redirect loop)",
                kind="redirects",
            ) from exc
        except httpx.TimeoutException as exc:
            raise FetchError(
                f"Timed out after {self.timeout:g}s waiting for a response",
                kind="timeout",
                retryable=True,
            ) from exc
        except httpx.ConnectError as exc:
            kind, message = _describe_connection_error(exc)
            raise FetchError(message, kind=kind, retryable=kind != "dns") from exc
        except (httpx.InvalidURL, httpx.HTTPError) as exc:
            raise FetchError(f"Request failed: {_short(exc)}", kind="request") from exc

        if not body.strip():
            raise FetchError("The server returned an empty response body", kind="empty")

        html, encoding = _decode(body, raw_content_type, is_html=is_html)
        return FetchResult(
            requested_url=url,
            final_url=final_url,
            status_code=status_code,
            reason=reason,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            content_type=content_type or "unknown",
            encoding=encoding,
            size_bytes=len(body),
            user_agent=user_agent,
            attempts=attempt,
            html=html,
            headers=response_headers,
        )

    def _check_status(self, response: httpx.Response) -> None:
        code = response.status_code
        if code < 400:
            return
        retry_after = None
        header = response.headers.get("Retry-After", "")
        if header.strip().isdigit():
            retry_after = float(header.strip())
        raise FetchError(
            f"HTTP {code} {response.reason_phrase or ''}".strip(),
            kind="http",
            status_code=code,
            retryable=code in RETRYABLE_STATUS,
            retry_after=retry_after,
        )

    async def _read_body(self, response: httpx.Response, max_bytes: int) -> bytes:
        declared = response.headers.get("Content-Length", "").strip()
        if declared.isdigit() and int(declared) > max_bytes:
            raise FetchError(
                f"Response is {int(declared):,} bytes; limit is {max_bytes:,}",
                kind="too_large",
            )
        chunks: list[bytes] = []
        total = 0
        try:
            async for chunk in response.aiter_bytes():
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise FetchError(
                        f"Response exceeded the {max_bytes:,} byte limit",
                        kind="too_large",
                    )
                chunks.append(chunk)
        except httpx.TimeoutException as exc:
            raise FetchError(
                f"Timed out after {self.timeout:g}s while reading the body",
                kind="timeout",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise FetchError(
                f"Connection dropped while reading the body: {_short(exc)}",
                kind="connection",
                retryable=True,
            ) from exc
        return b"".join(chunks)


# --------------------------------------------------------------------- helpers
def _short(exc: BaseException, limit: int = 160) -> str:
    text = " ".join(str(exc).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _describe_connection_error(exc: BaseException) -> tuple[str, str]:
    """Translate verbose httpx connection errors into one line."""
    text = str(exc).lower()
    if "ssl" in text or "certificate" in text or "tls" in text:
        if "wrong_version_number" in text:
            return "ssl", "The server does not speak HTTPS on this port (try an http:// URL)"
        return "ssl", f"TLS/SSL error: {_short(exc)}"
    dns_markers = (
        "name or service not known",
        "nameresolutionerror",
        "getaddrinfo failed",
        "nodename nor servname",
        "temporary failure in name resolution",
        "no address associated with hostname",
    )
    if any(marker in text for marker in dns_markers):
        return "dns", "DNS lookup failed - the host name could not be resolved"
    if "connection refused" in text or "actively refused" in text:
        return "connection", "Connection refused by the server"
    if "connection reset" in text:
        return "connection", "Connection reset by the server"
    if "network is unreachable" in text:
        return "connection", "Network is unreachable"
    return "connection", f"Connection failed: {_short(exc)}"


def _decode(body: bytes, content_type_header: str, *, is_html: bool = True) -> tuple[str, str]:
    """Decode bytes to text honouring header charset, <meta> charset, then sniffing."""
    match = _CHARSET_RE.search(content_type_header)
    hints = [match.group(1)] if match else []
    dammit = UnicodeDammit(body, known_definite_encodings=hints, is_html=is_html)
    if dammit.unicode_markup is None:
        return body.decode("utf-8", errors="replace"), "utf-8 (lossy)"
    return dammit.unicode_markup, dammit.original_encoding or "unicode"
