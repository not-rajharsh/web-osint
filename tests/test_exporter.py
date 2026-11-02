from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from osint_scraper.config import MD_MAX_LINKS
from osint_scraper.exporter import ExportError, export_report, render_json, render_markdown
from osint_scraper.models import FetchResult, PageData, ScrapeReport


def make_report(links: list[dict[str, str | None]] | None = None) -> ScrapeReport:
    if links is None:
        links = [
            {"text": "Home", "url": "https://example.com/", "type": "internal", "rel": None},
            {"text": "Mail", "url": "mailto:a@example.com", "type": "mailto", "rel": None},
        ]
    fetch = FetchResult(
        requested_url="https://example.com",
        final_url="https://example.com/",
        status_code=200,
        reason="OK",
        elapsed_ms=42,
        content_type="text/html",
        encoding="utf-8",
        size_bytes=128,
        user_agent="TestUA",
        attempts=1,
        html="<html></html>",
    )
    page = PageData(
        metadata={
            "title": "Example | Page",
            "description": "Demo page",
            "open_graph": {"title": "OG title"},
        },
        headings=[{"level": 1, "text": "Heading"}],
        links=links,
        images=[{"url": "https://example.com/a.png", "alt": "A", "width": "10", "height": "20"}],
        text="Hello world",
        word_count=2,
    )
    return ScrapeReport(
        tool="OSINT Scraper",
        version="1.0.0",
        scraped_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        fetch=fetch,
        page=page,
    )


def test_render_json_excludes_html_body() -> None:
    data = json.loads(render_json(make_report()))
    assert data["tool"] == "OSINT Scraper"
    assert data["version"] == "1.0.0"
    assert data["scraped_at"] == "2026-01-02T03:04:05+00:00"
    assert data["fetch"]["redirected"] is True
    assert "html" not in data["fetch"]
    assert data["page"]["metadata"]["title"] == "Example | Page"


def test_render_markdown_contains_sections_and_escaped_cells() -> None:
    markdown = render_markdown(make_report())
    assert markdown.startswith("# Example | Page")
    assert "## Summary" in markdown
    assert "| title | Example \\| Page |" in markdown
    assert "[Home](https://example.com/) - internal" in markdown
    assert "| Links | 2 (1 internal, 0 external, 1 contact) |" in markdown


def test_markdown_truncates_long_link_lists() -> None:
    links = [
        {"text": f"Link {index}", "url": f"https://example.com/{index}", "type": "internal", "rel": None}
        for index in range(MD_MAX_LINKS + 2)
    ]
    markdown = render_markdown(make_report(links=links))
    assert "...2 more not shown" in markdown


def test_export_report_writes_both_formats(tmp_path: Path) -> None:
    paths = export_report(make_report(), "both", tmp_path)
    assert len(paths) == 2
    assert {path.suffix for path in paths} == {".json", ".md"}
    assert all(path.exists() and path.parent == tmp_path for path in paths)
    assert all(path.name.startswith("example-com_20260102T030405Z") for path in paths)


def test_export_report_never_overwrites(tmp_path: Path) -> None:
    first = export_report(make_report(), "json", tmp_path)
    second = export_report(make_report(), "json", tmp_path)
    assert first != second
    assert second[0].name.endswith("-1.json")


def test_export_report_rejects_unknown_format(tmp_path: Path) -> None:
    with pytest.raises(ExportError):
        export_report(make_report(), "xml", tmp_path)
