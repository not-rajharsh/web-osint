from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from osint_scraper.config import MAX_SCRIPT_BYTES
from osint_scraper.fetcher import FetchError
from osint_scraper.models import ScriptSource
from osint_scraper.scripts import collect_scripts

BASE_URL = "https://example.com/page"


class FakeFetcher:
    def __init__(self, *, allowed: bool = True, failing: set[str] | None = None) -> None:
        self.allowed = allowed
        self.failing = failing or set()
        self.robots_calls: list[str] = []
        self.fetches: list[tuple[str, int]] = []

    async def is_allowed(self, url: str) -> bool:
        self.robots_calls.append(url)
        return self.allowed

    async def fetch_text(self, url: str, *, max_bytes: int) -> str:
        self.fetches.append((url, max_bytes))
        if url in self.failing:
            raise FetchError("missing", kind="http", status_code=404)
        return f"// content of {url}"


def run(
    coro: Callable[[], Awaitable[list[ScriptSource]]],
) -> list[ScriptSource]:
    return asyncio.run(coro())


def test_inline_scripts_are_always_collected() -> None:
    html = """
    <script>const first = 1;</script>
    <script src="/linked.js"></script>
    <script>const second = 2;</script>
    """

    sources = run(lambda: collect_scripts(html, BASE_URL))

    assert [source.label for source in sources] == [
        "inline-script[0]",
        "inline-script[1]",
    ]
    assert sources[0].content.strip() == "const first = 1;"
    assert all(source.url is None for source in sources)


def test_linked_scripts_are_opt_in_same_site_and_limited() -> None:
    html = """
    <script src="/one.js"></script>
    <script src="https://cdn.example.com/two.js"></script>
    <script src="//static.example.com/three.js"></script>
    <script src="https://external.test/four.js"></script>
    <script src="https://other.example.org/five.js"></script>
    <script>const inline = true;</script>
    """
    fetcher = FakeFetcher()
    events: list[str] = []

    sources = run(
        lambda: collect_scripts(
            html,
            BASE_URL,
            fetcher=fetcher,
            scan_linked=True,
            limit=2,
            delay=0,
            on_event=events.append,
        )
    )

    assert [source.url for source in sources] == [
        "https://example.com/one.js",
        "https://cdn.example.com/two.js",
        None,
    ]
    assert fetcher.fetches == [
        ("https://example.com/one.js", MAX_SCRIPT_BYTES),
        ("https://cdn.example.com/two.js", MAX_SCRIPT_BYTES),
    ]
    assert len(fetcher.robots_calls) == 2
    assert len(events) == 2


def test_robots_cache_and_failures_are_handled() -> None:
    html = """
    <script src="/a.js"></script>
    <script src="/b.js"></script>
    <script src="/c.js"></script>
    """
    fetcher = FakeFetcher(failing={"https://example.com/b.js"})

    sources = run(
        lambda: collect_scripts(
            html,
            BASE_URL,
            fetcher=fetcher,
            scan_linked=True,
            delay=0,
        )
    )

    assert [source.url for source in sources] == [
        "https://example.com/a.js",
        "https://example.com/c.js",
    ]
    assert fetcher.robots_calls == ["https://example.com/a.js"]


def test_robots_disallow_skips_linked_requests() -> None:
    fetcher = FakeFetcher(allowed=False)

    sources = run(
        lambda: collect_scripts(
            '<script src="/blocked.js"></script>',
            BASE_URL,
            fetcher=fetcher,
            scan_linked=True,
            delay=0,
        )
    )

    assert sources == []
    assert fetcher.robots_calls == ["https://example.com/blocked.js"]
    assert fetcher.fetches == []


def test_cross_site_and_non_http_links_are_skipped() -> None:
    fetcher = FakeFetcher()
    html = """
    <script src="https://external.test/app.js"></script>
    <script src="data:text/javascript,alert(1)"></script>
    """

    sources = run(
        lambda: collect_scripts(html, BASE_URL, fetcher=fetcher, scan_linked=True)
    )

    assert sources == []
    assert fetcher.fetches == []
