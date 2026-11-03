"""Depth-limited, same-scope crawling with sitemap discovery."""

from __future__ import annotations

import asyncio
import json
import xml.etree.ElementTree as ET
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from typing import Callable

from .config import (
    DEFAULT_CONCURRENCY,
    DEFAULT_CRAWL_DEPTH,
    DEFAULT_MAX_PAGES,
    DEFAULT_SCOPE,
    MAX_SITEMAP_BYTES,
    MAX_SITEMAP_DOCUMENTS,
    MAX_SITEMAP_URLS,
    XML_CONTENT_TYPES,
)
from .fetcher import Fetcher, FetchError
from .models import CrawlFailure, CrawlInfo, CrawlResult, Finding, PageResult
from .pipeline import scrape_page
from .urls import canonicalize_url, in_scope, origin


@dataclass(frozen=True)
class CrawlConfig:
    depth: int = DEFAULT_CRAWL_DEPTH
    max_pages: int = DEFAULT_MAX_PAGES
    scope: str = DEFAULT_SCOPE
    concurrency: int = DEFAULT_CONCURRENCY


def aggregate_findings(pages: tuple[PageResult, ...]) -> tuple[Finding, ...]:
    """Merge per-page findings and record every page where each value appeared."""
    merged: dict[tuple[str, str, str], Finding] = {}

    for page in pages:
        for finding in page.findings:
            data_key = json.dumps(finding.data, sort_keys=True, default=str) if finding.data else ""
            key = (finding.category, finding.value, data_key)
            current = merged.get(key)
            if current is None:
                merged[key] = replace(finding, pages=(page.url,))
                continue
            pages_seen = tuple(sorted({*current.pages, page.url}))
            merged[key] = replace(
                current,
                confidence=max(current.confidence, finding.confidence),
                pages=pages_seen,
            )

    return tuple(merged.values())


def parse_sitemap(content: str) -> tuple[list[str], bool]:
    """Return sitemap locations and whether the document is a sitemap index."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return [], False
    kind = _local_name(root.tag)
    if kind not in {"urlset", "sitemapindex"}:
        return [], False
    locations = [
        (element.text or "").strip()
        for element in root.iter()
        if _local_name(element.tag) == "loc" and (element.text or "").strip()
    ]
    return locations, kind == "sitemapindex"


async def crawl(
    fetcher: Fetcher,
    seed_url: str,
    config: CrawlConfig,
    *,
    scan_scripts: bool = False,
    on_event: Callable[[str], None] | None = None,
) -> CrawlResult:
    """Crawl from ``seed_url`` and return successful pages plus crawl metadata."""
    seed = canonicalize_url(seed_url)
    if seed is None:
        raise FetchError("The seed URL is not a valid HTTP(S) URL.", kind="request")
    if config.max_pages < 1:
        raise ValueError("max_pages must be at least 1")
    if config.concurrency < 1:
        raise ValueError("concurrency must be at least 1")

    if not await fetcher.is_allowed(seed):
        raise FetchError("This URL is disallowed by the site's robots.txt.", kind="robots")

    sitemaps, sitemap_urls = await _discover_sitemaps(
        fetcher, seed, config, on_event=on_event
    )

    queues: dict[int, deque[tuple[str, int]]] = defaultdict(deque)
    queues[0].append((seed, 0))
    seen = {seed}
    for url in sitemap_urls:
        if config.depth >= 1 and url not in seen and in_scope(url, seed, config.scope):
            seen.add(url)
            queues[1].append((url, 1))

    pages: list[PageResult] = []
    failures: list[CrawlFailure] = []
    attempts = 0

    for depth in range(config.depth + 1):
        while queues[depth] and attempts < config.max_pages:
            batch: list[asyncio.Task[PageResult | CrawlFailure]] = []
            while (
                queues[depth]
                and len(batch) < config.concurrency
                and attempts < config.max_pages
            ):
                url, item_depth = queues[depth].popleft()
                attempts += 1
                batch.append(
                    asyncio.create_task(
                        _crawl_one(
                            fetcher,
                            url,
                            item_depth,
                            seed,
                            config,
                            scan_scripts=scan_scripts,
                            on_event=on_event,
                        )
                    )
                )
            results = await asyncio.gather(*batch)
            for result in results:
                if isinstance(result, CrawlFailure):
                    failures.append(result)
                    continue
                pages.append(result)
                final_url = canonicalize_url(result.fetch.final_url)
                if final_url:
                    seen.add(final_url)
                if result.depth >= config.depth:
                    continue
                for link in result.page.links:
                    if link["type"] not in {"internal", "external"}:
                        continue
                    child = canonicalize_url(
                        link["url"], base_url=result.fetch.final_url
                    )
                    if (
                        child is None
                        or child in seen
                        or not in_scope(child, seed, config.scope)
                    ):
                        continue
                    seen.add(child)
                    queues[result.depth + 1].append((child, result.depth + 1))

    if not pages:
        seed_failure = next(
            (failure for failure in failures if failure.url == seed), None
        )
        if seed_failure is not None:
            raise FetchError(seed_failure.message, kind=seed_failure.kind)
        raise FetchError("No pages could be crawled.", kind="request")

    return CrawlResult(
        pages=tuple(pages),
        info=CrawlInfo(
            depth=config.depth,
            max_pages=config.max_pages,
            scope=config.scope,
            sitemaps=sitemaps,
            failures=tuple(failures),
        ),
    )


async def _crawl_one(
    fetcher: Fetcher,
    url: str,
    depth: int,
    seed: str,
    config: CrawlConfig,
    *,
    scan_scripts: bool,
    on_event: Callable[[str], None] | None,
) -> PageResult | CrawlFailure:
    if on_event:
        on_event(f"Crawling depth {depth}: {url}")
    try:
        if not await fetcher.is_allowed(url):
            raise FetchError("Disallowed by robots.txt.", kind="robots")
        result = await scrape_page(
            fetcher,
            url,
            depth=depth,
            scan_scripts=scan_scripts,
            on_event=on_event,
        )
        final_url = canonicalize_url(result.fetch.final_url)
        if final_url is None or not in_scope(final_url, seed, config.scope):
            raise FetchError(
                "The page redirected outside the configured crawl scope.",
                kind="scope",
            )
        return result
    except FetchError as exc:
        return CrawlFailure(url=url, depth=depth, kind=exc.kind, message=str(exc))


async def _discover_sitemaps(
    fetcher: Fetcher,
    seed: str,
    config: CrawlConfig,
    *,
    on_event: Callable[[str], None] | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if config.depth < 1 or config.max_pages < 2:
        return (), ()

    pending: deque[str] = deque([f"{origin(seed)}/sitemap.xml"])
    documents: list[str] = []
    page_urls: list[str] = []
    seen_documents: set[str] = set()
    seen_pages: set[str] = set()

    while (
        pending
        and len(documents) < MAX_SITEMAP_DOCUMENTS
        and len(page_urls) < MAX_SITEMAP_URLS
    ):
        sitemap = canonicalize_url(pending.popleft())
        if sitemap is None or sitemap in seen_documents:
            continue
        seen_documents.add(sitemap)
        if not await fetcher.is_allowed(sitemap):
            continue
        if on_event:
            on_event(f"Reading sitemap: {sitemap}")
        try:
            content = await fetcher.fetch_text(
                sitemap,
                max_bytes=MAX_SITEMAP_BYTES,
                content_types=XML_CONTENT_TYPES,
            )
        except FetchError:
            continue
        locations, is_index = parse_sitemap(content)
        if not locations:
            continue
        documents.append(sitemap)
        if is_index:
            for location in locations:
                child = canonicalize_url(location, base_url=sitemap)
                if child and in_scope(child, seed, config.scope):
                    pending.append(child)
            continue
        for location in locations:
            child = canonicalize_url(location, base_url=sitemap)
            if (
                child is None
                or child in seen_pages
                or not in_scope(child, seed, config.scope)
            ):
                continue
            seen_pages.add(child)
            page_urls.append(child)
            if len(page_urls) >= MAX_SITEMAP_URLS:
                break

    return tuple(documents), tuple(page_urls)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()
