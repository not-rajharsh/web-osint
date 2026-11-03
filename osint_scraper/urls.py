"""URL canonicalisation and scope helpers shared by crawling and extraction."""

from __future__ import annotations

import posixpath
from urllib.parse import urljoin, urlsplit, urlunsplit

from tldextract import TLDExtract

_TLD_EXTRACT = TLDExtract(suffix_list_urls=())


def host(url: str) -> str:
    """Return a lower-case hostname without a leading ``www.``."""
    name = (urlsplit(url).hostname or "").lower()
    return name[4:] if name.startswith("www.") else name


def registrable_domain(hostname: str) -> str:
    """Return the registrable domain for a hostname, using a bundled PSL."""
    extracted = _TLD_EXTRACT(hostname.lower())
    return ".".join(part for part in (extracted.domain, extracted.suffix) if part) or hostname


def has_valid_tld(domain: str) -> bool:
    extracted = _TLD_EXTRACT(domain.lower())
    return bool(extracted.domain and extracted.suffix)


def origin(url: str) -> str:
    """Return ``scheme://host[:port]`` for an absolute URL."""
    parts = urlsplit(url)
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


def same_site(first: str, second: str) -> bool:
    return registrable_domain(host(first)) == registrable_domain(host(second))


def in_scope(url: str, seed: str, scope: str) -> bool:
    """Check whether ``url`` belongs to the configured crawl scope."""
    if scope == "host":
        return host(url) == host(seed)
    return same_site(url, seed)


def canonicalize_url(
    raw: str,
    *,
    base_url: str | None = None,
    default_scheme: bool = False,
) -> str | None:
    """Normalise an HTTP(S) URL for queueing, or return ``None`` if unusable.

    Relative URLs are resolved against ``base_url``. The result lower-cases the
    scheme and host, removes fragments and default ports, and normalises an
    empty path to ``/``. Query strings are preserved as-is.
    """
    text = raw.strip()
    if not text:
        return None
    if base_url is not None:
        text = urljoin(base_url, text)
    elif default_scheme and "://" not in text:
        text = "https://" + text
    if "://" not in text:
        return None

    try:
        parts = urlsplit(text)
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        return None

    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not hostname:
        return None
    if parts.username is not None or parts.password is not None:
        return None

    hostname = hostname.lower()
    if not hostname.isascii():
        try:
            hostname = hostname.encode("idna").decode("ascii")
        except UnicodeError:
            return None
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"

    default_port = (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443
    )
    netloc = hostname if port is None or default_port else f"{hostname}:{port}"
    path = parts.path or "/"
    trailing_slash = path.endswith("/")
    path = posixpath.normpath(path)
    if trailing_slash and not path.endswith("/"):
        path += "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))
