"""Technology fingerprinting from headers, metadata, and assets."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Finding
from ._util import source_line
from .base import ExtractionContext, Extractor, register

_HEADER_RULES: tuple[tuple[str, re.Pattern[str], str, float], ...] = (
    ("server", re.compile(r"nginx(?:/([\w.]+))?", re.IGNORECASE), "Nginx", 0.95),
    ("server", re.compile(r"apache(?:/([\w.]+))?", re.IGNORECASE), "Apache", 0.95),
    ("server", re.compile(r"microsoft-iis/([\w.]+)", re.IGNORECASE), "IIS", 0.95),
    ("server", re.compile(r"cloudflare", re.IGNORECASE), "Cloudflare", 0.95),
    ("server", re.compile(r"amazons3", re.IGNORECASE), "Amazon S3", 0.95),
    ("server", re.compile(r"litespeed", re.IGNORECASE), "LiteSpeed", 0.95),
    ("server", re.compile(r"caddy(?:/([\w.]+))?", re.IGNORECASE), "Caddy", 0.95),
    ("server", re.compile(r"openresty(?:/([\w.]+))?", re.IGNORECASE), "OpenResty", 0.9),
    ("x-powered-by", re.compile(r"php/([\w.]+)", re.IGNORECASE), "PHP", 0.95),
    ("x-powered-by", re.compile(r"asp\.net", re.IGNORECASE), "ASP.NET", 0.95),
    ("x-powered-by", re.compile(r"express", re.IGNORECASE), "Express", 0.95),
    ("x-powered-by", re.compile(r"next\.js", re.IGNORECASE), "Next.js", 0.95),
    ("cf-ray", re.compile(r".+"), "Cloudflare", 1.0),
    ("x-vercel-id", re.compile(r".+"), "Vercel", 1.0),
    ("x-amz-cf-id", re.compile(r".+"), "Amazon CloudFront", 1.0),
    ("x-shopify-stage", re.compile(r".+"), "Shopify", 1.0),
    ("x-drupal-cache", re.compile(r".+"), "Drupal", 1.0),
)

_GENERATOR_RULES: tuple[tuple[re.Pattern[str], str, float], ...] = (
    (re.compile(r"wordpress\s*([\d.]+)?", re.IGNORECASE), "WordPress", 0.9),
    (re.compile(r"drupal\s*([\d.]+)?", re.IGNORECASE), "Drupal", 0.9),
    (re.compile(r"joomla!?\s*([\d.]+)?", re.IGNORECASE), "Joomla", 0.9),
    (re.compile(r"ghost\s*([\d.]+)?", re.IGNORECASE), "Ghost", 0.9),
    (re.compile(r"hugo\s*([\d.]+)?", re.IGNORECASE), "Hugo", 0.9),
    (re.compile(r"jekyll\s*([\d.]+)?", re.IGNORECASE), "Jekyll", 0.9),
    (re.compile(r"gatsby\s*([\d.]+)?", re.IGNORECASE), "Gatsby", 0.9),
    (re.compile(r"astro\s*([\d.]+)?", re.IGNORECASE), "Astro", 0.9),
    (re.compile(r"mediawiki\s*([\d.]+)?", re.IGNORECASE), "MediaWiki", 0.9),
)

_ASSET_RULES: tuple[tuple[re.Pattern[str], str, float], ...] = (
    (re.compile(r"jquery(?:-([\d.]+))?(?:\.min)?\.js", re.IGNORECASE), "jQuery", 0.85),
    (
        re.compile(r"bootstrap(?:-([\d.]+))?(?:\.min)?\.(?:js|css)", re.IGNORECASE),
        "Bootstrap",
        0.85,
    ),
    (
        re.compile(r"react(?:-([\d.]+))?(?:\.production)?(?:\.min)?\.js", re.IGNORECASE),
        "React",
        0.8,
    ),
    (re.compile(r"_next/static", re.IGNORECASE), "Next.js", 0.9),
    (re.compile(r"vue(?:@([\d.]+))?(?:\.min)?\.js", re.IGNORECASE), "Vue.js", 0.85),
    (re.compile(r"angular(?:\.min)?\.js", re.IGNORECASE), "Angular", 0.85),
    (re.compile(r"font-?awesome", re.IGNORECASE), "Font Awesome", 0.8),
    (
        re.compile(r"googletagmanager\.com/(?:gtm\.js|gtag/js)", re.IGNORECASE),
        "Google Tag Manager",
        0.95,
    ),
    (
        re.compile(r"google-analytics\.com/(?:analytics|ga)\.js", re.IGNORECASE),
        "Google Analytics",
        0.95,
    ),
    (re.compile(r"connect\.facebook\.net", re.IGNORECASE), "Facebook Pixel", 0.95),
    (re.compile(r"cdn\.shopify\.com", re.IGNORECASE), "Shopify", 0.9),
    (re.compile(r"static\.wixstatic\.com|wix\.com", re.IGNORECASE), "Wix", 0.9),
    (re.compile(r"/wp-(?:content|includes)/", re.IGNORECASE), "WordPress", 0.9),
    (re.compile(r"squarespace", re.IGNORECASE), "Squarespace", 0.9),
    (re.compile(r"tailwind", re.IGNORECASE), "Tailwind CSS", 0.7),
    (re.compile(r"alpinejs", re.IGNORECASE), "Alpine.js", 0.85),
    (re.compile(r"htmx", re.IGNORECASE), "htmx", 0.85),
)

_MARKER_RULES: tuple[tuple[str, str, float], ...] = (
    ("__NEXT_DATA__", "Next.js", 0.95),
    ("__NUXT__", "Nuxt.js", 0.95),
    ("ng-version", "Angular", 0.9),
    ("data-reactroot", "React", 0.8),
)


@dataclass
class _Detection:
    confidence: float
    sources: list[str] = field(default_factory=list)
    version: str | None = None


@register
class TechnologyExtractor(Extractor):
    name = "technologies"

    def extract(self, context: ExtractionContext) -> list[Finding]:
        detected: dict[str, _Detection] = {}
        headers = {name.lower(): value for name, value in context.headers.items()}

        for header, pattern, name, confidence in _HEADER_RULES:
            value = headers.get(header)
            if value is None:
                continue
            match = pattern.search(value)
            if match:
                version = match.group(1) if match.groups() else None
                self._add(detected, name, confidence, f"header:{header}", version)

        generator = context.page.metadata.get("generator")
        if isinstance(generator, str):
            for pattern, name, confidence in _GENERATOR_RULES:
                match = pattern.search(generator)
                if match:
                    self._add(
                        detected,
                        name,
                        confidence,
                        "meta:generator",
                        match.group(1) if match.groups() else None,
                    )

        soup = BeautifulSoup(context.html, "html.parser")
        for tag_name, attribute in (("script", "src"), ("link", "href")):
            for element in soup.find_all(tag_name):
                raw = element.get(attribute)
                if not isinstance(raw, str) or not raw.strip():
                    continue
                url = urljoin(context.base_url, raw.strip())
                for pattern, name, confidence in _ASSET_RULES:
                    match = pattern.search(url)
                    if match:
                        version = match.group(1) if match.groups() else None
                        self._add(detected, name, confidence, f"asset:{url}", version)

        for marker, name, confidence in _MARKER_RULES:
            index = context.html.find(marker)
            if index >= 0:
                self._add(
                    detected,
                    name,
                    confidence,
                    source_line("html", context.html, index),
                )

        findings: list[Finding] = []
        for name, details in detected.items():
            findings.append(
                Finding(
                    category=self.name,
                    value=f"{name} {details.version}" if details.version else name,
                    confidence=details.confidence,
                    source="; ".join(details.sources[:3]),
                    data={"evidence": details.sources},
                )
            )
        return findings

    def _add(
        self,
        detected: dict[str, _Detection],
        name: str,
        confidence: float,
        source: str,
        version: str | None = None,
    ) -> None:
        current = detected.get(name)
        if current is None:
            detected[name] = _Detection(confidence, [source], version)
            return
        if source not in current.sources:
            current.sources.append(source)
        current.confidence = max(current.confidence, confidence)
        if current.version is None:
            current.version = version
