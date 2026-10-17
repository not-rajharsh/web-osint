"""Modular web scraping and OSINT extraction toolkit.

Layers (each independent of the others, connected only through ``models``):
    fetcher  -> HTTP retrieval, user-agent rotation, retries
    parser   -> HTML to structured data
    exporter -> JSON / Markdown files
    tui      -> Rich terminal rendering
"""

from .config import __version__

__all__ = ["__version__"]
