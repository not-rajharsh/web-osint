"""Reusable fetch -> parse -> extract pipeline for one page."""

from __future__ import annotations

from typing import Callable

from .config import SCRIPT_DELAY
from .extractors import ExtractionContext, run_extractors
from .fetcher import Fetcher
from .models import PageResult
from .parser import parse_page
from .scripts import collect_scripts


async def scrape_page(
    fetcher: Fetcher,
    url: str,
    *,
    depth: int = 0,
    scan_scripts: bool = False,
    script_delay: float = SCRIPT_DELAY,
    on_event: Callable[[str], None] | None = None,
) -> PageResult:
    fetch = await fetcher.fetch(url)
    if on_event:
        on_event("Parsing HTML")
    page = parse_page(fetch.html, fetch.final_url)
    if on_event:
        on_event("Collecting scripts")
    scripts = await collect_scripts(
        fetch.html,
        fetch.final_url,
        fetcher=fetcher,
        scan_linked=scan_scripts,
        respect_robots=True,
        delay=script_delay,
        on_event=on_event,
    )
    if on_event:
        on_event("Extracting entities")
    context = ExtractionContext(
        html=fetch.html,
        base_url=fetch.final_url,
        headers=fetch.headers,
        page=page,
        scripts=tuple(scripts),
    )
    return PageResult(
        url=fetch.final_url,
        depth=depth,
        fetch=fetch,
        page=page,
        findings=run_extractors(context),
    )
