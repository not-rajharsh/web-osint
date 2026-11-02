"""Shared validation and provenance helpers for extractors."""

from __future__ import annotations

from urllib.parse import urlsplit

from tldextract import TLDExtract

_TLD_EXTRACT = TLDExtract(suffix_list_urls=())


def host(url: str) -> str:
    name = (urlsplit(url).hostname or "").lower()
    return name[4:] if name.startswith("www.") else name


def registrable_domain(hostname: str) -> str:
    extracted = _TLD_EXTRACT(hostname)
    return ".".join(part for part in (extracted.domain, extracted.suffix) if part) or hostname


def has_valid_tld(domain: str) -> bool:
    extracted = _TLD_EXTRACT(domain.lower())
    return bool(extracted.domain and extracted.suffix)


def line_number(text: str, index: int) -> int:
    return text.count("\n", 0, max(index, 0)) + 1


def source_line(label: str, text: str, index: int) -> str:
    return f"{label}:line {line_number(text, index)}"


def mask_secret(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}…{value[-4:]}"
