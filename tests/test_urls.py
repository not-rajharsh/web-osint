from __future__ import annotations

import pytest

from osint_scraper.urls import (
    canonicalize_url,
    has_valid_tld,
    host,
    in_scope,
    origin,
    registrable_domain,
    same_site,
)


def test_canonicalize_url_normalizes_common_variants() -> None:
    assert canonicalize_url("HTTPS://Example.COM:443") == "https://example.com/"
    assert canonicalize_url("http://Example.COM:80/a/../b#frag") == "http://example.com/b"
    assert canonicalize_url("https://example.com/?q=A%20B#top") == "https://example.com/?q=A%20B"


def test_canonicalize_url_resolves_relative_links() -> None:
    base = "https://example.com/docs/page"
    assert canonicalize_url("../about?x=1", base_url=base) == "https://example.com/about?x=1"
    assert canonicalize_url("/", base_url=base) == "https://example.com/"


def test_canonicalize_url_defaults_scheme_when_requested() -> None:
    assert canonicalize_url("example.com/path", default_scheme=True) == "https://example.com/path"
    assert canonicalize_url("example.com/path") is None


@pytest.mark.parametrize(
    "value",
    [
        "",
        "mailto:alice@example.com",
        "javascript:alert(1)",
        "https://user:pass@example.com/",
        "https://example.com:not-a-port/",
        "https:///missing-host",
    ],
)
def test_canonicalize_url_rejects_unusable_values(value: str) -> None:
    assert canonicalize_url(value) is None


def test_host_helpers_handle_www_and_subdomains() -> None:
    assert host("https://www.Example.com/path") == "example.com"
    assert registrable_domain("blog.example.co.uk") == "example.co.uk"
    assert registrable_domain("localhost") == "localhost"
    assert has_valid_tld("example.com")
    assert not has_valid_tld("not-a-domain")
    assert same_site("https://www.example.com", "https://api.example.com")
    assert not same_site("https://example.com", "https://example.org")


def test_origin_and_scope() -> None:
    assert origin("HTTPS://Example.COM:8443/path") == "https://example.com:8443"
    seed = "https://www.example.com/"
    assert in_scope("https://api.example.com/", seed, "domain")
    assert not in_scope("https://api.example.com/", seed, "host")
    assert in_scope("https://www.example.com/about", seed, "host")
    assert not in_scope("https://example.org/", seed, "domain")
