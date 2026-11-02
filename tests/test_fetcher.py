from __future__ import annotations

import pytest
import requests

from osint_scraper import fetcher as fetcher_module
from osint_scraper.fetcher import Fetcher, FetchError


class FakeResponse:
    def __init__(
        self,
        body: bytes = b"<html><body>ok</body></html>",
        *,
        status: int = 200,
        reason: str = "OK",
        url: str = "https://example.com/",
        headers: dict[str, str] | None = None,
    ) -> None:
        self._body = body
        self.status_code = status
        self.reason = reason
        self.url = url
        self.headers = headers or {
            "Content-Type": "text/html; charset=utf-8",
            "Content-Length": str(len(body)),
        }

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def iter_content(self, chunk_size: int = 8192) -> object:
        yield self._body

    @property
    def text(self) -> str:
        return self._body.decode("utf-8", errors="replace")


def make_fetcher(*, retries: int = 0, max_bytes: int = 10 * 1024 * 1024) -> Fetcher:
    return Fetcher(timeout=1.0, retries=retries, backoff=0.0, max_bytes=max_bytes, user_agent="TestUA")


def test_fetch_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fetcher = make_fetcher()
    monkeypatch.setattr(fetcher.session, "get", lambda *args, **kwargs: FakeResponse())

    result = fetcher.fetch("https://example.com")

    assert result.status_code == 200
    assert result.final_url == "https://example.com/"
    assert result.content_type == "text/html"
    assert result.encoding == "utf-8"
    assert result.attempts == 1
    assert result.user_agent == "TestUA"
    assert "<body>ok</body>" in result.html


def test_fetch_retries_transient_http(monkeypatch: pytest.MonkeyPatch) -> None:
    fetcher = make_fetcher(retries=1)
    responses = iter(
        [
            FakeResponse(status=503, reason="Service Unavailable", headers={"Content-Type": "text/html"}),
            FakeResponse(),
        ]
    )
    calls: list[str] = []

    def fake_get(*args: object, **kwargs: object) -> FakeResponse:
        calls.append("called")
        return next(responses)

    monkeypatch.setattr(fetcher.session, "get", fake_get)
    monkeypatch.setattr(fetcher_module.time, "sleep", lambda _seconds: None)

    result = fetcher.fetch("https://example.com")

    assert result.status_code == 200
    assert result.attempts == 2
    assert len(calls) == 2


def test_fetch_does_not_retry_non_transient_http(monkeypatch: pytest.MonkeyPatch) -> None:
    fetcher = make_fetcher(retries=2)
    calls: list[str] = []

    def fake_get(*args: object, **kwargs: object) -> FakeResponse:
        calls.append("called")
        return FakeResponse(status=404, reason="Not Found", headers={"Content-Type": "text/html"})

    monkeypatch.setattr(fetcher.session, "get", fake_get)

    with pytest.raises(FetchError) as excinfo:
        fetcher.fetch("https://example.com/missing")

    assert excinfo.value.kind == "http"
    assert excinfo.value.status_code == 404
    assert len(calls) == 1


def test_fetch_retries_timeouts_then_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    fetcher = make_fetcher(retries=1)
    calls: list[str] = []

    def fake_get(*args: object, **kwargs: object) -> FakeResponse:
        calls.append("called")
        raise requests.exceptions.Timeout("slow")

    monkeypatch.setattr(fetcher.session, "get", fake_get)
    monkeypatch.setattr(fetcher_module.time, "sleep", lambda _seconds: None)

    with pytest.raises(FetchError) as excinfo:
        fetcher.fetch("https://example.com")

    assert excinfo.value.kind == "timeout"
    assert len(calls) == 2


def test_fetch_rejects_non_html_content_type(monkeypatch: pytest.MonkeyPatch) -> None:
    fetcher = make_fetcher()
    response = FakeResponse(headers={"Content-Type": "application/json", "Content-Length": "2"})
    monkeypatch.setattr(fetcher.session, "get", lambda *args, **kwargs: response)

    with pytest.raises(FetchError) as excinfo:
        fetcher.fetch("https://example.com/data.json")

    assert excinfo.value.kind == "content_type"


def test_fetch_rejects_oversized_body(monkeypatch: pytest.MonkeyPatch) -> None:
    fetcher = make_fetcher(max_bytes=10)
    response = FakeResponse(headers={"Content-Type": "text/html", "Content-Length": "999"})
    monkeypatch.setattr(fetcher.session, "get", lambda *args, **kwargs: response)

    with pytest.raises(FetchError) as excinfo:
        fetcher.fetch("https://example.com/huge")

    assert excinfo.value.kind == "too_large"


def test_fetch_rejects_empty_body(monkeypatch: pytest.MonkeyPatch) -> None:
    fetcher = make_fetcher()
    monkeypatch.setattr(fetcher.session, "get", lambda *args, **kwargs: FakeResponse(body=b"   "))

    with pytest.raises(FetchError) as excinfo:
        fetcher.fetch("https://example.com/empty")

    assert excinfo.value.kind == "empty"


def test_robots_rules_are_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    fetcher = make_fetcher()
    body = b"User-agent: *\nDisallow: /private\n"
    response = FakeResponse(body=body, headers={"Content-Type": "text/plain"})
    monkeypatch.setattr(fetcher.session, "get", lambda *args, **kwargs: response)

    assert fetcher.is_allowed("https://example.com/private/page") is False
    assert fetcher.is_allowed("https://example.com/public") is True


def test_robots_network_failure_allows_crawl(monkeypatch: pytest.MonkeyPatch) -> None:
    fetcher = make_fetcher()

    def fake_get(*args: object, **kwargs: object) -> FakeResponse:
        raise requests.exceptions.ConnectionError("unreachable")

    monkeypatch.setattr(fetcher.session, "get", fake_get)

    assert fetcher.is_allowed("https://example.com/page") is True


def test_robots_forbidden_response_disallows(monkeypatch: pytest.MonkeyPatch) -> None:
    fetcher = make_fetcher()
    response = FakeResponse(status=403, reason="Forbidden", headers={"Content-Type": "text/plain"})
    monkeypatch.setattr(fetcher.session, "get", lambda *args, **kwargs: response)

    assert fetcher.is_allowed("https://example.com/page") is False
