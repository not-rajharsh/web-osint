"""Collect inline and optionally linked JavaScript for extraction."""

from __future__ import annotations

import time
from typing import Callable
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from tldextract import TLDExtract

from .config import DEFAULT_SCRIPT_LIMIT, MAX_SCRIPT_BYTES, SCRIPT_DELAY
from .fetcher import Fetcher, FetchError
from .models import ScriptSource

_TLD_EXTRACT = TLDExtract(suffix_list_urls=())


def _host(url: str) -> str:
    name = (urlsplit(url).hostname or "").lower()
    return name[4:] if name.startswith("www.") else name


def _registrable_domain(hostname: str) -> str:
    extracted = _TLD_EXTRACT(hostname)
    return ".".join(part for part in (extracted.domain, extracted.suffix) if part) or hostname


def _same_site(first: str, second: str) -> bool:
    return _registrable_domain(_host(first)) == _registrable_domain(_host(second))


def collect_scripts(
    html: str,
    base_url: str,
    *,
    fetcher: Fetcher | None = None,
    scan_linked: bool = False,
    respect_robots: bool = True,
    limit: int = DEFAULT_SCRIPT_LIMIT,
    delay: float = SCRIPT_DELAY,
    on_event: Callable[[str], None] | None = None,
) -> list[ScriptSource]:
    """Return inline scripts and, when enabled, same-site linked scripts."""
    soup = BeautifulSoup(html, "html.parser")
    sources: list[ScriptSource] = []
    inline_index = 0
    linked_count = 0
    robots_cache: dict[str, bool] = {}

    for script in soup.find_all("script"):
        src = script.get("src")
        if not isinstance(src, str) or not src.strip():
            content = script.string if script.string is not None else script.get_text()
            if content.strip():
                sources.append(
                    ScriptSource(label=f"inline-script[{inline_index}]", content=content)
                )
                inline_index += 1
            continue

        if not scan_linked or fetcher is None or linked_count >= limit:
            continue

        url = urljoin(base_url, src.strip())
        if urlsplit(url).scheme not in ("http", "https") or not _same_site(base_url, url):
            continue

        if respect_robots:
            parts = urlsplit(url)
            origin = f"{parts.scheme}://{parts.netloc}"
            if origin not in robots_cache:
                robots_cache[origin] = fetcher.is_allowed(url)
            if not robots_cache[origin]:
                continue

        linked_count += 1
        if linked_count > 1:
            time.sleep(delay)
        if on_event:
            on_event(f"Scanning script {linked_count}/{limit}: {url}")
        try:
            content = fetcher.fetch_text(url, max_bytes=MAX_SCRIPT_BYTES)
        except FetchError:
            continue
        sources.append(ScriptSource(label=url, content=content, url=url))

    return sources
