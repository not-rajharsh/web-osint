#!/usr/bin/env python3
"""OSINT Scraper - extract structured data from a web page.

Examples:
    python scraper.py --url https://example.com
    python scraper.py --url https://example.com --format json
    python scraper.py -u example.org -f md --respect-robots --limit 25
    python scraper.py -u https://example.com -q          # prints only file paths

Exit codes:
    0 success   1 fetch/robots failure   2 invalid input
    3 export failure   4 parse failure   130 interrupted
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from urllib.parse import urldefrag, urlsplit

from .config import (
    APP_NAME,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_RETRIES,
    DEFAULT_TABLE_ROWS,
    DEFAULT_TIMEOUT,
    __version__,
)
from .exporter import FORMATS, ExportError, export_report
from .extractors import ExtractionContext, run_extractors
from .fetcher import Fetcher, FetchError
from .models import ScrapeReport
from .parser import ParseError, parse_page
from .scripts import collect_scripts
from .tui import UI

EXIT_OK = 0
EXIT_FETCH = 1
EXIT_USAGE = 2
EXIT_EXPORT = 3
EXIT_PARSE = 4
EXIT_INTERRUPTED = 130

_FETCH_HINTS = {
    "timeout": "Try a larger --timeout, or check your connection.",
    "dns": "Check the spelling of the host name and your DNS/network settings.",
    "connection": "The server may be down or blocking you. Try again later.",
    "ssl": "Check the URL scheme, or whether the site's certificate is valid.",
    "http": "The server rejected the request. Some sites block automated clients.",
    "content_type": "This tool parses HTML pages, not files or APIs.",
    "too_large": "The page exceeds the 10 MiB safety limit.",
    "robots": "Remove --respect-robots only if you have permission to fetch this page.",
}


# ---------------------------------------------------------------- arg parsing
def _positive_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{value}' is not a number") from None
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return number


def _non_negative_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{value}' is not an integer") from None
    if number < 0:
        raise argparse.ArgumentTypeError("must be 0 or greater")
    return number


def _positive_int(value: str) -> int:
    number = _non_negative_int(value)
    if number == 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="osint-scraper",
        description="Extract headings, links, metadata, images and text from a web page "
        "and save the results as JSON and/or Markdown.",
        epilog="Only scrape sites you are permitted to access, and respect their terms of use.",
    )
    parser.add_argument("-u", "--url", required=True, help="target page (http/https; scheme optional)")
    parser.add_argument(
        "-f", "--format", choices=FORMATS, default="both",
        help="export format (default: %(default)s)",
    )
    parser.add_argument(
        "-o", "--output-dir", default=DEFAULT_OUTPUT_DIR, metavar="DIR",
        help="directory for export files (default: %(default)s)",
    )
    parser.add_argument(
        "-t", "--timeout", type=_positive_float, default=DEFAULT_TIMEOUT, metavar="SEC",
        help="network timeout in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "-r", "--retries", type=_non_negative_int, default=DEFAULT_RETRIES, metavar="N",
        help="retries for transient failures (default: %(default)s)",
    )
    parser.add_argument(
        "-a", "--user-agent", default=None, metavar="UA",
        help="use this User-Agent instead of rotating through the built-in pool",
    )
    parser.add_argument(
        "--respect-robots", action="store_true",
        help="refuse to fetch pages disallowed by the site's robots.txt",
    )
    parser.add_argument(
        "--scan-scripts", action="store_true",
        help="fetch same-site linked JavaScript to scan for exposed secrets "
        "(robots.txt is always honoured for these requests)",
    )
    parser.add_argument(
        "-l", "--limit", type=_positive_int, default=DEFAULT_TABLE_ROWS, metavar="N",
        help="rows shown per terminal table (default: %(default)s)",
    )
    parser.add_argument("--no-color", action="store_true", help="disable colour output")
    parser.add_argument(
        "-q", "--quiet", action="store_true",
        help="no tables or spinner; print only the exported file paths",
    )
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return parser


def normalize_url(raw: str) -> str:
    """Validate a user-supplied URL, defaulting to https when no scheme is given."""
    url = raw.strip()
    if not url:
        raise ValueError("The URL is empty.")
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported scheme '{parts.scheme}'. Use http or https.")
    if not parts.hostname:
        raise ValueError("The URL has no host name.")
    return urldefrag(url)[0]


# ------------------------------------------------------------------- pipeline
def run(args: argparse.Namespace, ui: UI) -> int:
    try:
        url = normalize_url(args.url)
    except ValueError as exc:
        ui.error("Invalid URL", str(exc), hint="Example: python scraper.py --url https://example.com")
        return EXIT_USAGE

    ui.banner()
    fetcher = Fetcher(timeout=args.timeout, retries=args.retries, user_agent=args.user_agent)

    try:
        with ui.status("Starting") as spinner:
            fetcher.on_event = spinner.update

            if args.respect_robots:
                spinner.update("Checking robots.txt")
                if not fetcher.is_allowed(url):
                    raise FetchError("This URL is disallowed by the site's robots.txt.", kind="robots")

            spinner.update("Fetching page")
            fetch = fetcher.fetch(url)

            spinner.update("Parsing HTML")
            page = parse_page(fetch.html, fetch.final_url)

            spinner.update("Collecting scripts")
            scripts = collect_scripts(
                fetch.html,
                fetch.final_url,
                fetcher=fetcher,
                scan_linked=args.scan_scripts,
                respect_robots=True,
                on_event=spinner.update,
            )

            spinner.update("Extracting entities")
            context = ExtractionContext(
                html=fetch.html,
                base_url=fetch.final_url,
                headers=fetch.headers,
                page=page,
                scripts=tuple(scripts),
            )
            findings = run_extractors(context)

            report = ScrapeReport(
                tool=APP_NAME,
                version=__version__,
                scraped_at=datetime.now(timezone.utc),
                fetch=fetch,
                page=page,
                findings=findings,
            )

            spinner.update("Writing exports")
            paths = export_report(report, args.format, args.output_dir)
    except FetchError as exc:
        ui.error(f"Fetch failed ({exc.kind})", str(exc), hint=_FETCH_HINTS.get(exc.kind))
        return EXIT_FETCH
    except ParseError as exc:
        ui.error("Parse failed", str(exc))
        return EXIT_PARSE
    except ExportError as exc:
        ui.error("Export failed", str(exc), hint="Check that the output directory is writable.")
        return EXIT_EXPORT

    ui.report(report, args.limit)
    ui.exports(paths)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ui = UI(no_color=args.no_color, quiet=args.quiet)
    try:
        return run(args, ui)
    except KeyboardInterrupt:
        ui.error("Interrupted", "Cancelled by the user.")
        return EXIT_INTERRUPTED


if __name__ == "__main__":
    sys.exit(main())
