from __future__ import annotations

from datetime import datetime, timezone
from io import StringIO

from rich.console import Console

from osint_scraper.config import DEFAULT_TABLE_ROWS
from osint_scraper.models import FetchResult, Finding, PageData, ScrapeReport
from osint_scraper.tui import THEME, UI


def make_report(findings: tuple[Finding, ...]) -> ScrapeReport:
    return ScrapeReport(
        tool="OSINT Scraper",
        version="1.0.0",
        scraped_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        fetch=FetchResult(
            requested_url="https://example.com",
            final_url="https://example.com/",
            status_code=200,
            reason="OK",
            elapsed_ms=10,
            content_type="text/html",
            encoding="utf-8",
            size_bytes=10,
            user_agent="TestUA",
            attempts=1,
            html="<html></html>",
        ),
        page=PageData(
            metadata={"title": "Example"},
            headings=[],
            links=[],
            images=[],
            text="",
            word_count=0,
        ),
        findings=findings,
    )


def render(*, findings: tuple[Finding, ...]) -> str:
    output = StringIO()
    ui = UI(no_color=True)
    console = Console(file=output, theme=THEME, width=200, no_color=True, highlight=False)
    ui.console = console
    ui.err = console
    ui.report(make_report(findings), DEFAULT_TABLE_ROWS)
    return output.getvalue()


def test_report_renders_findings_with_labels() -> None:
    findings = (
        Finding("emails", "alice@example.com", 0.95, "html:line 1"),
        Finding("secrets", "AKIA…CDEF", 0.95, "inline-script[0]:line 2"),
    )

    text = render(findings=findings)

    assert "Entities & Findings (2)" in text
    assert "Emails" in text
    assert "alice@example.com" in text
    assert "Exposed secrets" in text
    assert "AKIA…CDEF" in text
    assert "Findings" in text


def test_report_renders_empty_findings_notice() -> None:
    text = render(findings=())

    assert "No entities or technologies detected." in text


def test_report_truncates_findings_and_shows_footer() -> None:
    findings = tuple(
        Finding("emails", f"user{index}@example.com", 0.9, "html:line 1")
        for index in range(DEFAULT_TABLE_ROWS + 1)
    )

    text = render(findings=findings)

    assert f"Showing {DEFAULT_TABLE_ROWS} of {DEFAULT_TABLE_ROWS + 1}" in text


def test_quiet_mode_renders_nothing() -> None:
    output = StringIO()
    ui = UI(no_color=True, quiet=True)
    ui.console = Console(file=output, theme=THEME, width=200, no_color=True)
    ui.err = ui.console
    ui.report(make_report(()), DEFAULT_TABLE_ROWS)

    assert output.getvalue() == ""