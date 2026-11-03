"""Modular web scraping and OSINT extraction toolkit.

Layers (each independent of the others, connected only through ``models``):
    urls     -> URL canonicalisation and crawl-scope helpers
    fetcher  -> async HTTP retrieval, rate limiting, robots.txt
    parser   -> HTML to structured data
    pipeline -> fetch -> parse -> extract for one page
    crawler  -> scoped URL queue, sitemap discovery, aggregation
    exporter -> JSON / Markdown files
    tui      -> Rich terminal rendering
"""

from .config import __version__

__all__ = ["__version__"]
