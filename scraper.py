#!/usr/bin/env python3
"""Backwards-compatible entry point for the OSINT Scraper CLI."""

from __future__ import annotations

import sys

from osint_scraper.scraper import main

if __name__ == "__main__":
    sys.exit(main())
