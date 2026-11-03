from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable

import httpx
import pytest

from osint_scraper import fetcher as fetcher_module
from osint_scraper.fetcher import Fetcher, FetchError

Handler = Callable[[httpx.Request], httpx.Response]


def make_response(
    body: bytes = b"<html><body>ok</body></html>",
    *,
    status: int = 200,
    request: httpx.Request,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return httpx.Response(
        status,
        content=body,
        headers=headers
        or {
            "Content-Type": "text/html; charset=utf-8",
            "Content-Length": str(len(body)),
        },
        request=request,
    )


def run_fetch(
    handler: Handler,
    call: Callable[[Fetcher], Awaitable[object]],
    **options: object,
) -> object:
    async def scenario() -> object:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=True,
        )
        settings: dict[str, object] = {
            "timeout": 1.0,
            "retries": 0,
            "backoff": 0.0,
            "max_bytes": 10 * 1024 * 1024,
            "user_agent": "TestUA",
        }
        settings.update(options)
        async with Fetcher(client=client, **settings) as fetcher:  # type: ignore[arg-type]
            return await call(fetcher)

    return asyncio.run(scenario())


def test_fetch_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return make_response(request=request)

    async def call(fetcher: Fetcher) -> object:
        return await fetcher.fetch("https://example.com")

    result = run_fetch(handler, call)

    assert result.status_code == 200  # type: ignore[attr-defined]
    assert result.final_url == "https://example.com"  # type: ignore[attr-defined]
    assert result.content_type == "text/html"  # type: ignore[attr-defined]
    assert result.encoding == "utf-8"  # type: ignore[attr-defined]
    assert result.attempts == 1  # type: ignore[attr-defined]
    assert result.user_agent == "TestUA"  # type: ignore[attr-defined]
    assert "<body>ok</body>" in result.html  # type: ignore[attr-defined]
    assert result.headers["content-type"] == "text/html; charset=utf-8"  # type: ignore[attr-defined]
    assert "headers" not in result.to_dict()  # type: ignore[attr-defined]


def test_fetch_retries_transient_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetcher_module.random, "uniform", lambda *_args: 0.0)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if len(calls) == 1:
            return make_response(
                status=503,
                request=request,
                headers={"Content-Type": "text/html"},
            )
        return make_response(request=request)

    async def call(fetcher: Fetcher) -> object:
        return await fetcher.fetch("https://example.com")

    result = run_fetch(handler, call, retries=1)

    assert result.status_code == 200  # type: ignore[attr-defined]
    assert result.attempts == 2  # type: ignore[attr-defined]
    assert len(calls) == 2


def test_fetch_does_not_retry_non_transient_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fetcher_module.random, "uniform", lambda *_args: 0.0)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return make_response(
            status=404,
            request=request,
            headers={"Content-Type": "text/html"},
        )

    async def call(fetcher: Fetcher) -> object:
        return await fetcher.fetch("https://example.com/missing")

    with pytest.raises(FetchError) as excinfo:
        run_fetch(handler, call, retries=2)

    assert excinfo.value.kind == "http"
    assert excinfo.value.status_code == 404
    assert len(calls) == 1


def test_fetch_retries_timeouts_then_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetcher_module.random, "uniform", lambda *_args: 0.0)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        raise httpx.ReadTimeout("slow", request=request)

    async def call(fetcher: Fetcher) -> object:
        return await fetcher.fetch("https://example.com")

    with pytest.raises(FetchError) as excinfo:
        run_fetch(handler, call, retries=1)

    assert excinfo.value.kind == "timeout"
    assert len(calls) == 2


def test_fetch_maps_connection_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused", request=request)

    async def call(fetcher: Fetcher) -> object:
        return await fetcher.fetch("https://example.com")

    with pytest.raises(FetchError) as excinfo:
        run_fetch(handler, call)

    assert excinfo.value.kind == "connection"
    assert "refused" in str(excinfo.value)


def test_fetch_rejects_non_html_content_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return make_response(
            body=b"{}",
            request=request,
            headers={"Content-Type": "application/json", "Content-Length": "2"},
        )

    async def call(fetcher: Fetcher) -> object:
        return await fetcher.fetch("https://example.com/data.json")

    with pytest.raises(FetchError) as excinfo:
        run_fetch(handler, call)

    assert excinfo.value.kind == "content_type"


def test_fetch_rejects_oversized_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return make_response(
            body=b"x",
            request=request,
            headers={"Content-Type": "text/html", "Content-Length": "999"},
        )

    async def call(fetcher: Fetcher) -> object:
        return await fetcher.fetch("https://example.com/huge")

    with pytest.raises(FetchError) as excinfo:
        run_fetch(handler, call, max_bytes=10)

    assert excinfo.value.kind == "too_large"


def test_fetch_rejects_empty_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return make_response(body=b"   ", request=request)

    async def call(fetcher: Fetcher) -> object:
        return await fetcher.fetch("https://example.com/empty")

    with pytest.raises(FetchError) as excinfo:
        run_fetch(handler, call)

    assert excinfo.value.kind == "empty"


def test_robots_rules_are_applied_and_cached() -> None:
    robots_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        robots_calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return make_response(
                body=b"User-agent: *\nDisallow: /private\n",
                request=request,
                headers={"Content-Type": "text/plain"},
            )
        return make_response(request=request)

    async def call(fetcher: Fetcher) -> object:
        private = await fetcher.is_allowed("https://example.com/private/page")
        public = await fetcher.is_allowed("https://example.com/public")
        again = await fetcher.is_allowed("https://example.com/other")
        return private, public, again

    assert run_fetch(handler, call) == (False, True, True)
    assert len(robots_calls) == 1


def test_robots_network_failure_allows_crawl() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    async def call(fetcher: Fetcher) -> object:
        return await fetcher.is_allowed("https://example.com/page")

    assert run_fetch(handler, call) is True


def test_robots_forbidden_response_disallows() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return make_response(
            status=403,
            request=request,
            headers={"Content-Type": "text/plain"},
        )

    async def call(fetcher: Fetcher) -> object:
        return await fetcher.is_allowed("https://example.com/page")

    assert run_fetch(handler, call) is False


def test_fetch_text_accepts_javascript_and_uses_per_call_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return make_response(
            body=b"console.log('ok');",
            request=request,
            headers={
                "Content-Type": "application/javascript",
                "Content-Length": "18",
            },
        )

    async def call(fetcher: Fetcher) -> object:
        limited = await fetcher.fetch_text(
            "https://example.com/app.js", max_bytes=100
        )
        default = await fetcher.fetch_text("https://example.com/app.js")
        return limited, default

    assert run_fetch(handler, call, max_bytes=1000) == (
        "console.log('ok');",
        "console.log('ok');",
    )


def test_fetch_text_rejects_html() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return make_response(request=request)

    async def call(fetcher: Fetcher) -> object:
        return await fetcher.fetch_text("https://example.com/page")

    with pytest.raises(FetchError) as excinfo:
        run_fetch(handler, call)

    assert excinfo.value.kind == "content_type"


def test_delay_rate_limits_requests_per_host() -> None:
    starts: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        starts.append(time.monotonic())
        return make_response(request=request)

    async def call(fetcher: Fetcher) -> object:
        await fetcher.fetch("https://example.com/one")
        await fetcher.fetch("https://example.com/two")
        return None

    run_fetch(handler, call, delay=0.05)

    assert len(starts) == 2
    assert starts[1] - starts[0] >= 0.03
