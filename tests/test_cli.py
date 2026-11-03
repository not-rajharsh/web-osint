from __future__ import annotations

from pathlib import Path

import pytest

from osint_scraper import scraper
from osint_scraper.models import FetchResult, ScrapeReport

HTML = """
<html><head><title>Example</title></head><body>
  <p>Contact alice@example.com</p>
  <script>const aws = "AKIA1234567890ABCDEF";</script>
  <script src="/app.js"></script>
</body></html>
"""


class FakeStatus:
    def __init__(self, messages: list[str]) -> None:
        self.messages = messages

    def __enter__(self) -> "FakeStatus":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def update(self, message: str) -> None:
        self.messages.append(message)


class FakeUI:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.rendered_report: ScrapeReport | None = None
        self.paths: list[Path] = []
        self.errors: list[tuple[str, str]] = []

    def banner(self) -> None:
        return None

    def status(self, message: str) -> FakeStatus:
        self.messages.append(message)
        return FakeStatus(self.messages)

    def error(self, title: str, message: str, hint: str | None = None) -> None:
        self.errors.append((title, message))

    def report(self, report: ScrapeReport, limit: int) -> None:
        self.rendered_report = report

    def exports(self, paths: list[Path]) -> None:
        self.paths = paths


class StubFetcher:
    def __init__(self, **_kwargs: object) -> None:
        self.on_event = None

    async def __aenter__(self) -> "StubFetcher":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def aclose(self) -> None:
        return None

    async def is_allowed(self, _url: str) -> bool:
        return True

    async def fetch(self, url: str) -> FetchResult:
        return FetchResult(
            requested_url=url,
            final_url="https://example.com/",
            status_code=200,
            reason="OK",
            elapsed_ms=5,
            content_type="text/html",
            encoding="utf-8",
            size_bytes=len(HTML),
            user_agent="TestUA",
            attempts=1,
            html=HTML,
            headers={"Server": "nginx/1.25.3"},
        )

    async def fetch_text(
        self,
        _url: str,
        *,
        max_bytes: int | None = None,
        content_types: object = None,
    ) -> str:
        assert max_bytes is None or max_bytes > 0
        return 'const gh = "ghp_' + "a" * 36 + '";'


class CrawlStubFetcher(StubFetcher):
    async def fetch(self, url: str) -> FetchResult:
        if url == "https://example.com/about":
            html = "<html><head><title>About</title></head><body><p>alice@example.com</p></body></html>"
        else:
            html = HTML.replace(
                "</body>",
                '<a href="/about">About</a></body>',
            )
        return FetchResult(
            requested_url=url,
            final_url=url,
            status_code=200,
            reason="OK",
            elapsed_ms=5,
            content_type="text/html",
            encoding="utf-8",
            size_bytes=len(html),
            user_agent="TestUA",
            attempts=1,
            html=html,
        )


def test_run_wires_extraction_and_script_scanning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(scraper, "Fetcher", StubFetcher)
    paths = [tmp_path / "report.json"]
    monkeypatch.setattr(scraper, "export_report", lambda *_args: paths)
    ui = FakeUI()
    args = scraper.build_parser().parse_args(
        ["-u", "https://example.com", "--scan-scripts", "-o", str(tmp_path)]
    )

    code = scraper.run(args, ui)  # type: ignore[arg-type]

    assert code == scraper.EXIT_OK
    assert ui.errors == []
    assert ui.paths == paths
    assert ui.rendered_report is not None
    categories = {finding.category for finding in ui.rendered_report.findings}
    assert {"emails", "secrets", "technologies"} <= categories
    secrets = [finding for finding in ui.rendered_report.findings if finding.category == "secrets"]
    assert len(secrets) == 2
    assert all("AKIA1234567890ABCDEF" not in finding.value for finding in secrets)
    assert all("ghp_" + "a" * 36 not in finding.value for finding in secrets)
    assert "Collecting scripts" in ui.messages
    assert "Extracting entities" in ui.messages


def test_run_crawl_aggregates_pages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(scraper, "Fetcher", CrawlStubFetcher)
    paths = [tmp_path / "report.json"]
    monkeypatch.setattr(scraper, "export_report", lambda *_args: paths)
    ui = FakeUI()
    args = scraper.build_parser().parse_args(
        [
            "-u",
            "https://example.com",
            "--crawl",
            "--depth",
            "1",
            "--delay",
            "0",
            "-o",
            str(tmp_path),
        ]
    )

    code = scraper.run(args, ui)  # type: ignore[arg-type]

    assert code == scraper.EXIT_OK
    assert ui.errors == []
    assert ui.rendered_report is not None
    report = ui.rendered_report
    assert report.crawl is not None
    assert [page.url for page in report.pages] == [
        "https://example.com/",
        "https://example.com/about",
    ]
    emails = [finding for finding in report.findings if finding.category == "emails"]
    assert emails[0].pages == ("https://example.com/", "https://example.com/about")
