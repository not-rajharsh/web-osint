"""Export layer: write a :class:`ScrapeReport` to JSON and/or Markdown files."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from .config import MD_MAX_IMAGES, MD_MAX_LINKS, MD_TEXT_EXCERPT_CHARS
from .models import ScrapeReport

FORMATS = ("json", "md", "both")


class ExportError(Exception):
    """An export file or directory could not be written."""


def export_report(report: ScrapeReport, fmt: str, output_dir: str | Path) -> list[Path]:
    """Write the requested format(s) into ``output_dir`` and return the paths."""
    if fmt not in FORMATS:
        raise ExportError(f"Unknown format '{fmt}'. Choose from: {', '.join(FORMATS)}.")

    directory = Path(output_dir)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ExportError(f"Cannot create output directory '{directory}': {exc}") from exc

    stem = _file_stem(report)
    written: list[Path] = []
    try:
        if fmt in ("json", "both"):
            path = _unique_path(directory / f"{stem}.json")
            path.write_text(render_json(report), encoding="utf-8")
            written.append(path)
        if fmt in ("md", "both"):
            path = _unique_path(directory / f"{stem}.md")
            path.write_text(render_markdown(report), encoding="utf-8")
            written.append(path)
    except OSError as exc:
        raise ExportError(f"Could not write export file: {exc}") from exc
    return written


# ----------------------------------------------------------------------- JSON
def render_json(report: ScrapeReport) -> str:
    return json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n"


# ------------------------------------------------------------------- Markdown
def render_markdown(report: ScrapeReport) -> str:
    page, fetch = report.page, report.fetch
    title = page.metadata.get("title") or fetch.final_url
    counts = page.link_counts()
    lines: list[str] = [
        f"# {_md_inline(title)}",
        "",
        f"> Scraped {report.scraped_at.isoformat(timespec='seconds')} "
        f"by {report.tool} v{report.version}",
        "",
        "## Summary",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| URL | {_md_cell(fetch.requested_url)} |",
    ]
    if fetch.redirected:
        lines.append(f"| Final URL | {_md_cell(fetch.final_url)} |")
    lines += [
        f"| Status | {fetch.status_code} {_md_cell(fetch.reason)} |",
        f"| Response time | {fetch.elapsed_ms} ms |",
        f"| Size | {fetch.size_bytes:,} bytes |",
        f"| Content type | {_md_cell(fetch.content_type)} |",
        f"| Encoding | {_md_cell(fetch.encoding)} |",
        f"| Attempts | {fetch.attempts} |",
        f"| Words | {page.word_count:,} |",
        f"| Headings | {len(page.headings)} |",
        f"| Links | {len(page.links)} "
        f"({counts.get('internal', 0)} internal, {counts.get('external', 0)} external, "
        f"{counts.get('mailto', 0) + counts.get('tel', 0)} contact) |",
        f"| Images | {len(page.images)} |",
        "",
    ]

    metadata_rows = page.flat_metadata()
    lines += ["## Metadata", ""]
    if metadata_rows:
        lines += ["| Key | Value |", "|---|---|"]
        lines += [f"| {_md_cell(k)} | {_md_cell(v)} |" for k, v in metadata_rows]
    else:
        lines.append("_No metadata found._")
    lines.append("")

    lines += [f"## Headings ({len(page.headings)})", ""]
    if page.headings:
        for heading in page.headings:
            indent = "  " * (heading["level"] - 1)
            lines.append(f"{indent}- **H{heading['level']}** {_md_inline(heading['text'])}")
    else:
        lines.append("_No headings found._")
    lines.append("")

    lines += [f"## Links ({len(page.links)})", ""]
    if page.links:
        for link in page.links[:MD_MAX_LINKS]:
            label = _md_inline(link["text"]) or "(no text)"
            lines.append(f"- [{label}]({_md_url(link['url'])}) - {link['type']}")
        if len(page.links) > MD_MAX_LINKS:
            lines.append(
                f"- _...{len(page.links) - MD_MAX_LINKS} more not shown "
                f"(see the JSON export for the full list)._"
            )
    else:
        lines.append("_No links found._")
    lines.append("")

    lines += [f"## Images ({len(page.images)})", ""]
    if page.images:
        for image in page.images[:MD_MAX_IMAGES]:
            alt = _md_inline(image["alt"]) or "(no alt text)"
            lines.append(f"- [{alt}]({_md_url(image['url'])})")
        if len(page.images) > MD_MAX_IMAGES:
            lines.append(
                f"- _...{len(page.images) - MD_MAX_IMAGES} more not shown "
                f"(see the JSON export for the full list)._"
            )
    else:
        lines.append("_No images found._")
    lines.append("")

    lines += ["## Text excerpt", ""]
    if page.text:
        excerpt = page.text[:MD_TEXT_EXCERPT_CHARS]
        lines += [f"> {line}" for line in excerpt.splitlines()]
        if len(page.text) > MD_TEXT_EXCERPT_CHARS:
            lines += [
                ">",
                f"> _...truncated, {len(page.text) - MD_TEXT_EXCERPT_CHARS:,} "
                f"more characters (see the JSON export)._",
            ]
    else:
        lines.append("_No visible text found._")
    lines.append("")

    return "\n".join(lines)


# -------------------------------------------------------------------- helpers
def _file_stem(report: ScrapeReport) -> str:
    parts = urlsplit(report.fetch.final_url)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", f"{parts.hostname or 'page'}{parts.path}")
    slug = slug.strip("-")[:60].strip("-") or "page"
    return f"{slug}_{report.scraped_at.strftime('%Y%m%dT%H%M%SZ')}"


def _unique_path(path: Path) -> Path:
    """Never overwrite an existing export: append -1, -2, ... if needed."""
    if not path.exists():
        return path
    counter = 1
    while True:
        candidate = path.with_name(f"{path.stem}-{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _md_inline(text: str) -> str:
    """Escape characters that would break inline Markdown structure."""
    text = " ".join((text or "").split())
    return re.sub(r"([\\`*_\[\]<>])", r"\\\1", text)


def _md_cell(text: str) -> str:
    """Escape a value for use inside a Markdown table cell."""
    return " ".join((text or "").split()).replace("|", "\\|")


def _md_url(url: str) -> str:
    return url.replace(" ", "%20").replace("(", "%28").replace(")", "%29")
