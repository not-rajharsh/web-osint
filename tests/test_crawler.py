from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from osint_scraper.crawler import CrawlConfig, aggregate_findings, crawl, parse_sitemap
from osint_scraper.exporter import render_json, render_markdown
from osint_scraper.fetcher import FetchError
from osint_scraper.models import (
    CrawlInfo,
    FetchResult,
    Finding,
    PageData,
    PageResult,
    ScrapeReport,
)


def page_html(title: str, *links: str) -> str:
    anchors = "".join(f'<a href="{link}">{link}</a>' for link in links)
    return f"<html><head><title>{title}</title></head><body>{anchors}</body></html>"


class FakeFetcher:
    def __init__(
        self,
        pages: dict[str, str],
        *,
        sitemaps: dict[str, str] | None = None,
        disallowed: set[str] | None = None,
        final_urls: dict[str, str] | None = None,
    ) -> None:
        self.pages = pages
        self.sitemaps = sitemaps or {}
        self.disallowed = disallowed or set()
        self.final_urls = final_urls or {}
        self.fetched: list[str] = []
        self.text_fetched: list[str] = []

    async def is_allowed(self, url: str) -> bool:
        return url not in self.disallowed

    async def fetch(self, url: str) -> FetchResult:
        self.fetched.append(url)
        if url not in self.pages:
            raise FetchError(f"HTTP 404 for {url}", kind="http")
        html = self.pages[url]
        return FetchResult(
            requested_url=url,
            final_url=self.final_urls.get(url, url),
            status_code=200,
            reason="OK",
            elapsed_ms=1,
            content_type="text/html",
            encoding="utf-8",
            size_bytes=len(html),
            user_agent="TestUA",
            attempts=1,
            html=html,
        )

    async def fetch_text(self, url: str, **_kwargs: object) -> str:
        self.text_fetched.append(url)
        if url not in self.sitemaps:
            raise FetchError(f"HTTP 404 for {url}", kind="http")
        return self.sitemaps[url]


def run_crawl(
    fetcher: FakeFetcher, seed: str, config: CrawlConfig
) -> object:
    return asyncio.run(crawl(fetcher, seed, config))


def test_crawl_follows_links_dedupes_and_applies_host_scope() -> None:
    fetcher = FakeFetcher(
        {
            "https://example.com/": page_html(
                "Home",
                "/about",
                "/about#team",
                "https://other.test/",
                "mailto:alice@example.com",
            ),
            "https://example.com/about": page_html("About", "/team"),
            "https://example.com/team": page_html("Team"),
            "https://other.test/": page_html("Other"),
        }
    )

    result = run_crawl(
        fetcher,
        "https://example.com",
        CrawlConfig(depth=1, max_pages=10, scope="host"),
    )

    assert [page.url for page in result.pages] == [  # type: ignore[attr-defined]
        "https://example.com/",
        "https://example.com/about",
    ]
    assert [page.depth for page in result.pages] == [0, 1]  # type: ignore[attr-defined]
    assert "https://other.test/" not in fetcher.fetched
    assert "https://example.com/team" not in fetcher.fetched


def test_crawl_honours_max_pages() -> None:
    fetcher = FakeFetcher(
        {
            "https://example.com/": page_html("Home", "/about", "/team"),
            "https://example.com/about": page_html("About"),
            "https://example.com/team": page_html("Team"),
        }
    )

    result = run_crawl(
        fetcher,
        "https://example.com/",
        CrawlConfig(depth=2, max_pages=2, concurrency=1),
    )

    assert len(result.pages) == 2  # type: ignore[attr-defined]
    assert len(fetcher.fetched) == 2


def test_crawl_records_robots_failures_without_aborting() -> None:
    private = "https://example.com/private"
    fetcher = FakeFetcher(
        {
            "https://example.com/": page_html("Home", "/private"),
            private: page_html("Private"),
        },
        disallowed={private},
    )

    result = run_crawl(
        fetcher,
        "https://example.com/",
        CrawlConfig(depth=1, max_pages=10),
    )

    assert [page.url for page in result.pages] == ["https://example.com/"]  # type: ignore[attr-defined]
    assert result.info.failures[0].url == private  # type: ignore[attr-defined]
    assert result.info.failures[0].kind == "robots"  # type: ignore[attr-defined]


def test_crawl_records_out_of_scope_redirects() -> None:
    fetcher = FakeFetcher(
        {
            "https://example.com/": page_html("Home", "/redirect"),
            "https://example.com/redirect": page_html("Redirect"),
        },
        final_urls={"https://example.com/redirect": "https://other.test/landing"},
    )

    result = run_crawl(
        fetcher,
        "https://example.com/",
        CrawlConfig(depth=1, max_pages=10, scope="host"),
    )

    assert [page.url for page in result.pages] == ["https://example.com/"]  # type: ignore[attr-defined]
    assert result.info.failures[0].kind == "scope"  # type: ignore[attr-defined]
    assert result.info.failures[0].url == "https://example.com/redirect"  # type: ignore[attr-defined]


def test_crawl_raises_when_seed_is_disallowed() -> None:
    fetcher = FakeFetcher({}, disallowed={"https://example.com/"})

    with pytest.raises(FetchError) as excinfo:
        run_crawl(fetcher, "https://example.com/", CrawlConfig())

    assert excinfo.value.kind == "robots"


def test_crawl_adds_sitemap_urls_at_depth_one() -> None:
    sitemap = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://example.com/from-sitemap</loc></url>
      <url><loc>https://example.com/</loc></url>
      <url><loc>https://other.test/out-of-scope</loc></url>
    </urlset>
    """
    fetcher = FakeFetcher(
        {
            "https://example.com/": page_html("Home"),
            "https://example.com/from-sitemap": page_html("From sitemap"),
        },
        sitemaps={"https://example.com/sitemap.xml": sitemap},
    )

    result = run_crawl(
        fetcher,
        "https://example.com/",
        CrawlConfig(depth=2, max_pages=10),
    )

    assert [page.url for page in result.pages] == [  # type: ignore[attr-defined]
        "https://example.com/",
        "https://example.com/from-sitemap",
    ]
    assert result.info.sitemaps == ("https://example.com/sitemap.xml",)  # type: ignore[attr-defined]
    assert fetcher.text_fetched == ["https://example.com/sitemap.xml"]


def test_parse_sitemap_supports_indexes_and_namespaces() -> None:
    index = """<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://example.com/map-1.xml</loc></sitemap>
    </sitemapindex>"""
    urlset = """<urlset><url><loc>https://example.com/page</loc></url></urlset>"""

    assert parse_sitemap(index) == (["https://example.com/map-1.xml"], True)
    assert parse_sitemap(urlset) == (["https://example.com/page"], False)
    assert parse_sitemap("not xml") == ([], False)
    assert parse_sitemap("<rss><channel /></rss>") == ([], False)


def make_page(url: str, findings: tuple[Finding, ...]) -> PageResult:
    html = page_html(url)
    fetch = FetchResult(
        requested_url=url,
        final_url=url,
        status_code=200,
        reason="OK",
        elapsed_ms=1,
        content_type="text/html",
        encoding="utf-8",
        size_bytes=len(html),
        user_agent="TestUA",
        attempts=1,
        html=html,
    )
    page = PageData(
        metadata={},
        headings=[],
        links=[],
        images=[],
        text="",
        word_count=0,
    )
    return PageResult(url=url, depth=0, fetch=fetch, page=page, findings=findings)


def test_aggregate_findings_merges_pages_and_keeps_max_confidence() -> None:
    first = "https://example.com/a"
    second = "https://example.com/b"
    pages = (
        make_page(
            first,
            (
                Finding("emails", "alice@example.com", 0.5, "html"),
                Finding("emails", "alice@example.com", 0.7, "script", {"kind": "inline"}),
            ),
        ),
        make_page(
            second,
            (
                Finding("emails", "alice@example.com", 0.9, "html"),
                Finding("technologies", "Nginx", 0.95, "headers"),
            ),
        ),
    )

    merged = aggregate_findings(pages)

    email = next(
        finding
        for finding in merged
        if finding.category == "emails" and finding.data is None
    )
    assert email.confidence == 0.9
    assert email.pages == (first, second)
    assert len([finding for finding in merged if finding.category == "emails"]) == 2


def test_crawl_report_exports_pages_and_failures() -> None:
    page = make_page(
        "https://example.com/",
        (Finding("emails", "alice@example.com", 0.9, "html", pages=("https://example.com/",)),),
    )
    report = ScrapeReport(
        tool="OSINT Scraper",
        version="1.1.0",
        scraped_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        fetch=page.fetch,
        page=page.page,
        findings=page.findings,
        pages=(page,),
        crawl=CrawlInfo(depth=1, max_pages=5, scope="host", sitemaps=("https://example.com/sitemap.xml",)),
    )

    data = json.loads(render_json(report))
    markdown = render_markdown(report)

    assert data["crawl"]["scope"] == "host"
    assert data["pages"][0]["url"] == "https://example.com/"
    assert "| Pages crawled | 1 |" in markdown
    assert "## Crawl (1 pages)" in markdown
    assert "https://example.com/sitemap.xml" in markdown
    assert "pages: https://example.com/" in markdown
