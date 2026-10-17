"""Plain data containers shared by the fetch, parse and export stages.

Keeping these in one module means the stages never import each other: each one
only knows about the models, which is what makes them independently testable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class FetchResult:
    """Everything we learned from a single successful HTTP fetch."""

    requested_url: str
    final_url: str
    status_code: int
    reason: str
    elapsed_ms: int
    content_type: str
    encoding: str
    size_bytes: int
    user_agent: str
    attempts: int
    html: str = field(repr=False)

    @property
    def redirected(self) -> bool:
        return self.requested_url != self.final_url

    def to_dict(self) -> dict[str, Any]:
        """Serialisable view; the (large) HTML body is intentionally excluded."""
        data = asdict(self)
        data.pop("html")
        data["redirected"] = self.redirected
        return data


@dataclass(frozen=True)
class PageData:
    """Structured data extracted from one HTML document."""

    metadata: dict[str, Any]
    headings: list[dict[str, Any]]
    links: list[dict[str, Any]]
    images: list[dict[str, Any]]
    text: str
    word_count: int

    def link_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for link in self.links:
            counts[link["type"]] = counts.get(link["type"], 0) + 1
        return counts

    def flat_metadata(self) -> list[tuple[str, str]]:
        """Metadata as ordered (key, value) string pairs, skipping empty values."""
        rows: list[tuple[str, str]] = []
        for key, value in self.metadata.items():
            if value in (None, "", [], {}):
                continue
            if isinstance(value, dict):
                rows.extend((f"{key}.{sub}", str(val)) for sub, val in value.items())
            elif isinstance(value, list):
                rows.append((key, ", ".join(str(item) for item in value)))
            else:
                rows.append((key, str(value)))
        return rows

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ScrapeReport:
    """The complete result of one run: provenance + fetch info + page data."""

    tool: str
    version: str
    scraped_at: datetime
    fetch: FetchResult
    page: PageData

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "version": self.version,
            "scraped_at": self.scraped_at.isoformat(timespec="seconds"),
            "fetch": self.fetch.to_dict(),
            "page": self.page.to_dict(),
        }
