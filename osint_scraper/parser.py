"""Parse layer: turn an HTML string into structured :class:`PageData`.

Pure function of (html, base_url). No network, no files, no terminal output.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urldefrag, urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from .models import PageData

_HEADING_TAGS = ["h1", "h2", "h3", "h4", "h5", "h6"]
_LAZY_SRC_ATTRS = ("src", "data-src", "data-lazy-src", "data-original")
_URL_META_KEYS = ("og:image", "og:url", "og:video", "twitter:image")
_NOISE_TAGS = ["script", "style", "noscript", "template", "svg", "iframe"]


class ParseError(Exception):
    """The document could not be parsed into structured data."""


def parse_page(html: str, base_url: str) -> PageData:
    """Extract metadata, headings, links, images and visible text."""
    try:
        soup = BeautifulSoup(html, "html.parser")
        metadata = _extract_metadata(soup, base_url)
        headings = _extract_headings(soup)
        links = _extract_links(soup, base_url)
        images = _extract_images(soup, base_url)
        # Text extraction goes last because it strips <script>/<style> in place.
        text = _extract_text(soup)
    except Exception as exc:  # BeautifulSoup is lenient; guard against the rare crash.
        raise ParseError(f"Could not parse the document: {exc}") from exc

    return PageData(
        metadata=metadata,
        headings=headings,
        links=links,
        images=images,
        text=text,
        word_count=len(text.split()),
    )


# ------------------------------------------------------------------ utilities
def _clean(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        value = " ".join(str(item) for item in value)
    return " ".join(str(value).split())


def _rel_values(tag: Tag) -> set[str]:
    rel = tag.get("rel") or []
    if isinstance(rel, str):
        rel = rel.split()
    return {item.lower() for item in rel}


def _host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


# ------------------------------------------------------------------- metadata
def _extract_metadata(soup: BeautifulSoup, base_url: str) -> dict[str, Any]:
    meta_map: dict[str, str] = {}
    for tag in soup.find_all("meta"):
        key = (tag.get("property") or tag.get("name") or "").strip().lower()
        content = tag.get("content")
        if key and content is not None:
            meta_map.setdefault(key, _clean(content))

    title_tag = soup.find("title")
    html_tag = soup.find("html")
    charset_tag = soup.find("meta", charset=True)

    canonical = None
    favicon = None
    for link in soup.find_all("link", href=True):
        rels = _rel_values(link)
        if canonical is None and "canonical" in rels:
            canonical = urljoin(base_url, link["href"].strip())
        if favicon is None and rels & {"icon", "shortcut", "apple-touch-icon"}:
            favicon = urljoin(base_url, link["href"].strip())

    keywords = [
        part.strip() for part in meta_map.get("keywords", "").split(",") if part.strip()
    ]

    # Resolve URL-valued social tags so exports contain absolute URLs.
    for url_key in _URL_META_KEYS:
        if meta_map.get(url_key):
            meta_map[url_key] = urljoin(base_url, meta_map[url_key])

    return {
        "title": _clean(title_tag.get_text()) if title_tag else None,
        "description": meta_map.get("description"),
        "keywords": keywords,
        "author": meta_map.get("author"),
        "language": _clean(html_tag.get("lang")) if isinstance(html_tag, Tag) else None,
        "charset": _clean(charset_tag.get("charset")) if isinstance(charset_tag, Tag) else None,
        "canonical": canonical,
        "robots": meta_map.get("robots"),
        "generator": meta_map.get("generator"),
        "favicon": favicon,
        "open_graph": {k[3:]: v for k, v in meta_map.items() if k.startswith("og:")},
        "twitter": {k[8:]: v for k, v in meta_map.items() if k.startswith("twitter:")},
    }


# ------------------------------------------------------------------- headings
def _extract_headings(soup: BeautifulSoup) -> list[dict[str, Any]]:
    headings = []
    for tag in soup.find_all(_HEADING_TAGS):
        text = _clean(tag.get_text(" "))
        if text:
            headings.append({"level": int(tag.name[1]), "text": text})
    return headings


# ---------------------------------------------------------------------- links
def _extract_links(soup: BeautifulSoup, base_url: str) -> list[dict[str, Any]]:
    """Unique links in document order, classified internal/external/mailto/tel.

    "Internal" means the same host as the page (ignoring a leading ``www.``);
    other subdomains count as external.
    """
    page_host = _host(base_url)
    seen: set[str] = set()
    links: list[dict[str, Any]] = []

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        lowered = href.lower()
        if not href or href.startswith("#") or lowered.startswith(("javascript:", "data:")):
            continue

        if lowered.startswith("mailto:"):
            url, kind = href, "mailto"
        elif lowered.startswith("tel:"):
            url, kind = href, "tel"
        else:
            url = urldefrag(urljoin(base_url, href))[0]
            if urlsplit(url).scheme not in ("http", "https"):
                continue
            kind = "internal" if _host(url) == page_host else "external"

        if url in seen:
            continue
        seen.add(url)

        label = _clean(anchor.get_text(" "))
        if not label:
            label = _clean(anchor.get("title"))
        if not label:
            image = anchor.find("img")
            label = _clean(image.get("alt")) if image else ""

        rel = " ".join(sorted(_rel_values(anchor))) or None
        links.append({"text": label, "url": url, "type": kind, "rel": rel})
    return links


# --------------------------------------------------------------------- images
def _best_from_srcset(srcset: str) -> str | None:
    """Pick the highest-resolution candidate from a ``srcset`` attribute."""
    best_url, best_score = None, -1.0
    for candidate in re.split(r",\s+", srcset.strip()):
        pieces = candidate.split()
        if not pieces:
            continue
        score = 1.0
        if len(pieces) > 1 and pieces[1][:-1].replace(".", "", 1).isdigit():
            score = float(pieces[1][:-1])
        if score > best_score:
            best_url, best_score = pieces[0], score
    return best_url


def _extract_images(soup: BeautifulSoup, base_url: str) -> list[dict[str, Any]]:
    seen: set[str] = set()
    images: list[dict[str, Any]] = []

    for img in soup.find_all("img"):
        source = next(
            (
                img[attr].strip()
                for attr in _LAZY_SRC_ATTRS
                if img.get(attr) and not img[attr].strip().lower().startswith("data:")
            ),
            None,
        )
        if source is None:
            srcset = img.get("srcset") or img.get("data-srcset")
            source = _best_from_srcset(srcset) if srcset else None
        if not source:
            continue

        url = urljoin(base_url, source)
        if urlsplit(url).scheme not in ("http", "https") or url in seen:
            continue
        seen.add(url)
        images.append(
            {
                "url": url,
                "alt": _clean(img.get("alt")),
                "width": _clean(img.get("width")) or None,
                "height": _clean(img.get("height")) or None,
            }
        )
    return images


# ----------------------------------------------------------------------- text
def _extract_text(soup: BeautifulSoup) -> str:
    """Visible text, one non-empty line per block, whitespace collapsed."""
    root = soup.body or soup
    for noise in root.find_all(_NOISE_TAGS):
        noise.decompose()
    lines = (_clean(line) for line in root.get_text("\n").splitlines())
    return "\n".join(line for line in lines if line)
