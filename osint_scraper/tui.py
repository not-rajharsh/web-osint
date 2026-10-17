"""Terminal UI layer built on Rich.

Design-system structure (see README for the full spec):

* **Tokens**   - semantic colours, box style and padding defined once below.
* **Components** - methods on :class:`UI`, each rendering one kind of data.
* **Accessibility** - every status carries a text label and a symbol, so
  colour is never the only signal; ``--no-color`` / ``NO_COLOR`` are honoured
  and symbols fall back to ASCII on non-UTF-8 terminals.

Scraped content is always wrapped in ``rich.text.Text`` so that stray square
brackets in a page (e.g. "[bold]") are never interpreted as Rich markup.
"""

from __future__ import annotations

from pathlib import Path

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme
from rich.tree import Tree

from .config import APP_NAME, __version__
from .models import ScrapeReport

# ------------------------------------------------------------------- tokens
THEME = Theme(
    {
        "brand": "bold cyan",
        "accent": "magenta",
        "success": "bold green",
        "warning": "bold yellow",
        "danger": "bold red",
        "muted": "dim",
        "label": "bold",
        "link": "cyan",
    }
)
BOX = box.ROUNDED
PADDING = (0, 1)

LINK_TYPE_STYLE = {
    "internal": "success",
    "external": "warning",
    "mailto": "accent",
    "tel": "accent",
}
HEADING_LEVEL_STYLE = {1: "brand", 2: "label", 3: "label"}

_SYMBOLS_UNICODE = {"ok": "✔", "warn": "▲", "error": "✖", "info": "●"}
_SYMBOLS_ASCII = {"ok": "OK", "warn": "!", "error": "x", "info": "*"}


def _symbols(console: Console) -> dict[str, str]:
    encoding = (console.encoding or "").lower()
    return _SYMBOLS_UNICODE if encoding.startswith("utf") else _SYMBOLS_ASCII


class _Spinner:
    """Status spinner whose message can be updated; a no-op when disabled."""

    def __init__(self, console: Console, message: str, enabled: bool) -> None:
        self._status = (
            console.status(Text(message, style="brand"), spinner="dots", spinner_style="brand")
            if enabled
            else None
        )

    def __enter__(self) -> "_Spinner":
        if self._status:
            self._status.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._status:
            self._status.stop()

    def update(self, message: str) -> None:
        if self._status:
            self._status.update(Text(message, style="brand"))


class UI:
    """All terminal output lives here; no other module prints."""

    def __init__(self, *, no_color: bool = False, quiet: bool = False) -> None:
        self.quiet = quiet
        self.console = Console(theme=THEME, no_color=no_color, highlight=False)
        self.err = Console(theme=THEME, no_color=no_color, highlight=False, stderr=True)
        self.sym = _symbols(self.console)

    # --------------------------------------------------------------- chrome
    def banner(self) -> None:
        if self.quiet:
            return
        title = Text(f"{APP_NAME} ", style="brand")
        title.append(f"v{__version__}", style="muted")
        self.console.print(Panel(title, box=BOX, border_style="brand", padding=PADDING, expand=False))

    def status(self, message: str) -> _Spinner:
        return _Spinner(self.console, message, enabled=not self.quiet)

    def notice(self, level: str, message: str) -> None:
        """One-line message. ``level`` is one of ok, warn, error, info."""
        if self.quiet and level != "error":
            return
        style = {"ok": "success", "warn": "warning", "error": "danger", "info": "brand"}[level]
        line = Text()
        line.append(f"{self.sym[level]} ", style=style)
        line.append(message)
        (self.err if level == "error" else self.console).print(line)

    def error(self, title: str, message: str, hint: str | None = None) -> None:
        body = Text(message)
        if hint:
            body.append("\n\n")
            body.append("Hint: ", style="label")
            body.append(hint, style="muted")
        header = Text(f"{self.sym['error']} {title}", style="danger")
        self.err.print(Panel(body, title=header, title_align="left", box=BOX, border_style="danger", padding=PADDING))

    # ------------------------------------------------------------- results
    def report(self, report: ScrapeReport, limit: int) -> None:
        """Render the full set of result tables for a finished scrape."""
        if self.quiet:
            return
        self._summary(report)
        self._metadata(report)
        self._headings(report, limit)
        self._links(report, limit)
        self._images(report, limit)
        self._preview(report)

    def exports(self, paths: list[Path]) -> None:
        if self.quiet:
            for path in paths:
                self.console.print(str(path), markup=False, highlight=False, soft_wrap=True)
            return
        body = Text()
        for index, path in enumerate(paths):
            if index:
                body.append("\n")
            body.append(f"{self.sym['ok']} ", style="success")
            body.append(str(path), style="link")
        self.console.print(
            Panel(body, title=Text("Saved", style="success"), title_align="left", box=BOX, border_style="success", padding=PADDING)
        )

    # ----------------------------------------------------------- components
    def _table(self, title: str, *columns: str) -> Table:
        table = Table(title=title, title_style="brand", title_justify="left", box=BOX, header_style="label", padding=PADDING)
        for name in columns:
            table.add_column(name)
        return table

    def _footer(self, shown: int, total: int) -> None:
        if total > shown:
            self.console.print(
                Text(f"  Showing {shown} of {total}. Full data is in the export file.", style="muted")
            )

    def _summary(self, report: ScrapeReport) -> None:
        fetch, page = report.fetch, report.page
        counts = page.link_counts()
        code_style = "success" if fetch.status_code < 300 else "warning" if fetch.status_code < 400 else "danger"

        table = self._table("Summary", "Field", "Value")
        table.columns[0].style = "label"
        status = Text()
        status.append(f"{self.sym['ok'] if fetch.status_code < 400 else self.sym['error']} ", style=code_style)
        status.append(f"{fetch.status_code} {fetch.reason}".strip(), style=code_style)

        table.add_row("Status", status)
        table.add_row("URL", Text(fetch.requested_url, overflow="fold", style="link"))
        if fetch.redirected:
            table.add_row("Final URL", Text(fetch.final_url, overflow="fold", style="link"))
        table.add_row("Title", Text(str(page.metadata.get("title") or "(none)"), overflow="fold"))
        table.add_row("Response", Text(f"{fetch.elapsed_ms} ms, {fetch.size_bytes:,} bytes, {fetch.attempts} attempt(s)"))
        table.add_row("Content", Text(f"{fetch.content_type}, {fetch.encoding}"))
        table.add_row("User-Agent", Text(fetch.user_agent, overflow="fold", style="muted"))
        table.add_row("Words", Text(f"{page.word_count:,}"))
        table.add_row("Headings", Text(str(len(page.headings))))
        table.add_row(
            "Links",
            Text(
                f"{len(page.links)} total "
                f"({counts.get('internal', 0)} internal, {counts.get('external', 0)} external, "
                f"{counts.get('mailto', 0) + counts.get('tel', 0)} contact)"
            ),
        )
        table.add_row("Images", Text(str(len(page.images))))
        self.console.print(table)

    def _metadata(self, report: ScrapeReport) -> None:
        rows = report.page.flat_metadata()
        if not rows:
            self.notice("warn", "No metadata found on this page.")
            return
        table = self._table("Metadata", "Key", "Value")
        table.columns[0].style = "accent"
        for key, value in rows:
            table.add_row(Text(key), Text(value, overflow="fold"))
        self.console.print(table)

    def _headings(self, report: ScrapeReport, limit: int) -> None:
        headings = report.page.headings
        if not headings:
            self.notice("warn", "No headings found on this page.")
            return
        tree = Tree(Text(f"Headings ({len(headings)})", style="brand"), guide_style="muted")
        stack: list[tuple[int, Tree]] = []
        for heading in headings[:limit]:
            level = heading["level"]
            while stack and stack[-1][0] >= level:
                stack.pop()
            parent = stack[-1][1] if stack else tree
            label = Text()
            label.append(f"H{level} ", style="muted")
            label.append(heading["text"], style=HEADING_LEVEL_STYLE.get(level, ""))
            stack.append((level, parent.add(label)))
        self.console.print(tree)
        self._footer(min(limit, len(headings)), len(headings))

    def _links(self, report: ScrapeReport, limit: int) -> None:
        links = report.page.links
        if not links:
            self.notice("warn", "No links found on this page.")
            return
        table = self._table(f"Links ({len(links)})", "#", "Type", "Text", "URL")
        table.columns[0].justify = "right"
        for index, link in enumerate(links[:limit], start=1):
            table.add_row(
                Text(str(index), style="muted"),
                Text(link["type"], style=LINK_TYPE_STYLE.get(link["type"], "")),
                Text(link["text"] or "(no text)", overflow="ellipsis", no_wrap=True, style="" if link["text"] else "muted"),
                Text(link["url"], overflow="ellipsis", no_wrap=True, style="link"),
            )
        table.columns[2].max_width = 36
        table.columns[3].max_width = 64
        self.console.print(table)
        self._footer(min(limit, len(links)), len(links))

    def _images(self, report: ScrapeReport, limit: int) -> None:
        images = report.page.images
        if not images:
            self.notice("warn", "No images found on this page.")
            return
        table = self._table(f"Images ({len(images)})", "#", "Alt", "Size", "URL")
        table.columns[0].justify = "right"
        for index, image in enumerate(images[:limit], start=1):
            size = f"{image['width']}x{image['height']}" if image["width"] and image["height"] else "-"
            table.add_row(
                Text(str(index), style="muted"),
                Text(image["alt"] or "(none)", overflow="ellipsis", no_wrap=True, style="" if image["alt"] else "muted"),
                Text(size, style="muted"),
                Text(image["url"], overflow="ellipsis", no_wrap=True, style="link"),
            )
        table.columns[1].max_width = 30
        table.columns[3].max_width = 64
        self.console.print(table)
        self._footer(min(limit, len(images)), len(images))

    def _preview(self, report: ScrapeReport) -> None:
        text = report.page.text
        if not text:
            self.notice("warn", "No visible text found on this page.")
            return
        excerpt = text[:400] + ("…" if len(text) > 400 else "")
        self.console.print(
            Panel(Text(excerpt), title=Text("Text preview", style="brand"), title_align="left", box=BOX, border_style="muted", padding=PADDING)
        )
