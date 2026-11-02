from __future__ import annotations

import pytest

from osint_scraper.models import PageData
from osint_scraper.parser import parse_page

BASE_URL = "https://example.com/page"

SAMPLE_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>  Example &amp; Test  </title>
  <meta name="description" content="A demo page">
  <meta name="keywords" content="osint, recon, , cli">
  <meta name="author" content="Jane Doe">
  <meta name="robots" content="index,follow">
  <meta name="generator" content="pytest">
  <meta property="og:title" content="OG Title">
  <meta property="og:image" content="/og.png">
  <meta name="twitter:card" content="summary">
  <link rel="canonical" href="/canonical">
  <link rel="shortcut icon" href="/favicon.ico">
</head>
<body>
  <h1>Main <span>Heading</span></h1>
  <h2>Sub heading</h2>
  <a href="/about">About</a>
  <a href="https://external.test/page">External</a>
  <a href="mailto:test@example.com">Email</a>
  <a href="tel:+123456">Call</a>
  <a href="javascript:void(0)">Skip</a>
  <a href="/about#section">About again</a>
  <a href="#local">Fragment</a>
  <img src="/img/a.png" alt="A" width="100" height="50">
  <img data-src="https://cdn.test/b.jpg" alt="B">
  <img srcset="/small.jpg 320w, /large.jpg 1280w" alt="C">
  <img data-original="/d.jpg" alt="">
  <script>console.log('hidden')</script>
  <style>.hidden{}</style>
  <p>Hello   world</p>
  <noscript>No script</noscript>
</body>
</html>
"""


@pytest.fixture
def page() -> PageData:
    return parse_page(SAMPLE_HTML, BASE_URL)


def test_metadata(page: PageData) -> None:
    metadata = page.metadata
    assert metadata["title"] == "Example & Test"
    assert metadata["description"] == "A demo page"
    assert metadata["keywords"] == ["osint", "recon", "cli"]
    assert metadata["author"] == "Jane Doe"
    assert metadata["language"] == "en"
    assert metadata["charset"] == "utf-8"
    assert metadata["canonical"] == "https://example.com/canonical"
    assert metadata["robots"] == "index,follow"
    assert metadata["generator"] == "pytest"
    assert metadata["favicon"] == "https://example.com/favicon.ico"
    assert metadata["open_graph"] == {
        "title": "OG Title",
        "image": "https://example.com/og.png",
    }
    assert metadata["twitter"] == {"card": "summary"}


def test_headings(page: PageData) -> None:
    assert page.headings == [
        {"level": 1, "text": "Main Heading"},
        {"level": 2, "text": "Sub heading"},
    ]


def test_links_are_unique_and_classified(page: PageData) -> None:
    by_url = {link["url"]: link for link in page.links}
    assert len(page.links) == 4
    assert by_url["https://example.com/about"]["type"] == "internal"
    assert by_url["https://external.test/page"]["type"] == "external"
    assert by_url["mailto:test@example.com"]["type"] == "mailto"
    assert by_url["tel:+123456"]["type"] == "tel"
    assert by_url["https://example.com/about"]["text"] == "About"
    assert page.link_counts() == {"internal": 1, "external": 1, "mailto": 1, "tel": 1}


def test_images_resolve_lazy_and_srcset_sources(page: PageData) -> None:
    by_url = {image["url"]: image for image in page.images}
    assert len(page.images) == 4
    assert by_url["https://example.com/img/a.png"] == {
        "url": "https://example.com/img/a.png",
        "alt": "A",
        "width": "100",
        "height": "50",
    }
    assert by_url["https://cdn.test/b.jpg"]["alt"] == "B"
    assert "https://example.com/large.jpg" in by_url
    assert "https://example.com/d.jpg" in by_url


def test_visible_text_excludes_noise(page: PageData) -> None:
    assert "Hello world" in page.text
    assert "hidden" not in page.text
    assert "No script" not in page.text
    assert page.word_count == len(page.text.split())


def test_empty_document_returns_empty_sections() -> None:
    page = parse_page("", BASE_URL)
    assert page.headings == []
    assert page.links == []
    assert page.images == []
    assert page.text == ""
    assert page.word_count == 0
