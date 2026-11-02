"""Fetch layer: HTTP retrieval with user-agent rotation and clean errors.

This module knows nothing about HTML structure, files or the terminal. Its only
job is to turn a URL into a :class:`FetchResult` or raise a :class:`FetchError`
that carries a machine-readable ``kind`` and a human-readable message.
"""

from __future__ import annotations

import random
import re
import time
from typing import Callable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import requests
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


class Fetcher:
    """HTTP client wrapper with retry, backoff and user-agent rotation."""

    def __init__(
        self,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        backoff: float = DEFAULT_BACKOFF,
        max_bytes: int = DEFAULT_MAX_BYTES,
        user_agent: str | None = None,
        on_event: Callable[[str], None] | None = None,
    ) -> None:
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.max_bytes = max_bytes
        self.fixed_user_agent = user_agent
        self.on_event = on_event
        self.session = requests.Session()
        self.session.max_redirects = MAX_REDIRECTS

    # ------------------------------------------------------------------ public
    def fetch(self, url: str) -> FetchResult:
        """Fetch an HTML page, retrying transient failures with exponential backoff."""
        return self._fetch(url, HTML_CONTENT_TYPES, is_html=True, max_bytes=self.max_bytes)

    def fetch_text(self, url: str, *, max_bytes: int | None = None) -> str:
        """Fetch a JavaScript/text resource and return its decoded body."""
        result = self._fetch(
            url,
            SCRIPT_CONTENT_TYPES,
            is_html=False,
            max_bytes=max_bytes or self.max_bytes,
        )
        return result.html

    def _fetch(
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
                return self._attempt(
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
                self._emit(
                    f"{exc} - retry {attempt}/{self.retries} in {wait:.1f}s"
                )
                time.sleep(wait)
        raise FetchError("Retry loop exited unexpectedly.")  # pragma: no cover

    def is_allowed(self, url: str) -> bool:
        """Check robots.txt for ``url`` using the wildcard (``*``) rules.

        Follows the common interpretation of RFC 9309: a 401/403 on robots.txt
        means "disallow all"; other 4xx, 5xx or network failures mean "no rules
        were published", so crawling is permitted.
        """
        parts = urlsplit(url)
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        try:
            response = self.session.get(
                robots_url, headers=self._headers(), timeout=self.timeout
            )
        except requests.exceptions.RequestException:
            return True
        if response.status_code in (401, 403):
            return False
        if response.status_code != 200:
            return True
        parser = RobotFileParser()
        parser.parse(response.text.splitlines())
        return parser.can_fetch("*", url)

    # ----------------------------------------------------------------- private
    def _emit(self, message: str) -> None:
        if self.on_event:
            self.on_event(message)

    def _pick_user_agent(self) -> str:
        return self.fixed_user_agent or random.choice(USER_AGENTS)

    def _headers(self, user_agent: str | None = None) -> dict[str, str]:
        return {**BASE_HEADERS, "User-Agent": user_agent or self._pick_user_agent()}

    def _attempt(
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
        try:
            response = self.session.get(
                url,
                headers=self._headers(user_agent),
                timeout=self.timeout,
                stream=True,
                allow_redirects=True,
            )
        except requests.exceptions.SSLError as exc:
            if "wrong_version_number" in str(exc).lower():
                message = "The server does not speak HTTPS on this port (try an http:// URL)"
            else:
                message = f"TLS/SSL error: {_short(exc)}"
            raise FetchError(message, kind="ssl") from exc
        except requests.exceptions.Timeout as exc:
            raise FetchError(
                f"Timed out after {self.timeout:g}s waiting for a response",
                kind="timeout",
                retryable=True,
            ) from exc
        except requests.exceptions.TooManyRedirects as exc:
            raise FetchError(
                f"Gave up after {MAX_REDIRECTS} redirects (possible redirect loop)",
                kind="redirects",
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            kind, message = _describe_connection_error(exc)
            raise FetchError(message, kind=kind, retryable=kind != "dns") from exc
        except requests.exceptions.RequestException as exc:
            raise FetchError(f"Request failed: {_short(exc)}", kind="request") from exc

        with response:
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
            body = self._read_body(response, max_bytes)
            final_url = response.url
            status_code = response.status_code
            reason = response.reason or ""
            raw_content_type = response.headers.get("Content-Type", "")
            response_headers = dict(response.headers)

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

    def _check_status(self, response: requests.Response) -> None:
        code = response.status_code
        if code < 400:
            return
        retry_after = None
        header = response.headers.get("Retry-After", "")
        if header.strip().isdigit():
            retry_after = float(header.strip())
        raise FetchError(
            f"HTTP {code} {response.reason or ''}".strip(),
            kind="http",
            status_code=code,
            retryable=code in RETRYABLE_STATUS,
            retry_after=retry_after,
        )

    def _read_body(self, response: requests.Response, max_bytes: int) -> bytes:
        declared = response.headers.get("Content-Length", "").strip()
        if declared.isdigit() and int(declared) > max_bytes:
            raise FetchError(
                f"Response is {int(declared):,} bytes; limit is {max_bytes:,}",
                kind="too_large",
            )
        chunks: list[bytes] = []
        total = 0
        try:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise FetchError(
                        f"Response exceeded the {max_bytes:,} byte limit",
                        kind="too_large",
                    )
                chunks.append(chunk)
        except requests.exceptions.Timeout as exc:
            raise FetchError(
                f"Timed out after {self.timeout:g}s while reading the body",
                kind="timeout",
                retryable=True,
            ) from exc
        except requests.exceptions.RequestException as exc:
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
    """Translate verbose urllib3/requests connection errors into one line."""
    text = str(exc).lower()
    dns_markers = (
        "name or service not known",
        "nameresolutionerror",
        "getaddrinfo failed",
        "nodename nor servname",
        "temporary failure in name resolution",
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
