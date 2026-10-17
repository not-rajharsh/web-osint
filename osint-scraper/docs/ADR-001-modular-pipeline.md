# ADR-001: Modular fetch → parse → export pipeline for the scraper CLI

**Status:** Accepted
**Date:** 2026-10-17
**Deciders:** Project owner

## Context

We need a local, single-command CLI that extracts headings, links, metadata,
images and text from one URL, survives common network failures, writes JSON and
Markdown to `exports/`, and shows a readable terminal UI. Constraints: Python
3.10+, Requests, BeautifulSoup4, Rich, argparse; no placeholders; easy to
extend (e.g. new export formats, new extractors) without rewriting the tool.

## Decision

Use a small package with **four layers that never import each other**, joined
only by plain dataclasses in `models.py`:

```
scraper.py (CLI / orchestration)
   ├─ fetcher.py   URL  → FetchResult   (Requests, retries, UA rotation, robots)
   ├─ parser.py    HTML → PageData      (BeautifulSoup, pure function)
   ├─ exporter.py  ScrapeReport → files (JSON, Markdown)
   └─ tui.py       ScrapeReport → terminal (Rich; the only module that prints)
```

Failures are typed (`FetchError.kind`, `ParseError`, `ExportError`) and mapped
to distinct exit codes in the CLI, so scripts can react to them.

## Options Considered

### Option A: Single-file script
| Dimension | Assessment |
|-----------|------------|
| Complexity | Low initially, grows quickly |
| Cost | Cheapest to write |
| Scalability | Poor: fetch, parse and print logic intertwine |
| Team familiarity | High |

**Pros:** Trivial to distribute and read top to bottom.
**Cons:** Hard to test layers in isolation; every new feature touches everything.

### Option B: Modular package with dataclass hand-offs (chosen)
| Dimension | Assessment |
|-----------|------------|
| Complexity | Low-Medium |
| Cost | A few small files |
| Scalability | Good: add an exporter or extractor in one place |
| Team familiarity | High (standard library patterns) |

**Pros:** Each layer testable alone; parser is a pure function; only `tui.py`
prints, so `--quiet` and future output modes are trivial.
**Cons:** More files than the task strictly needs today.

### Option C: Scrapy
| Dimension | Assessment |
|-----------|------------|
| Complexity | High for a one-URL tool |
| Cost | Heavy dependency, framework conventions |
| Scalability | Excellent for crawling |
| Team familiarity | Medium; also outside the specified stack |

**Pros:** Built-in crawling, throttling, pipelines.
**Cons:** Overkill for single-page extraction; violates the stated tech stack.

## Trade-off Analysis

The tool is small but has four distinct concerns with different reasons to
change (network behaviour, HTML structure, file formats, terminal design).
Option B pays a small structural cost up front to keep those changes isolated.
Option A is cheaper today but couples them; Option C solves a crawling problem
we do not have.

Secondary decisions:

- **`html.parser` over `lxml`:** no compiled dependency, installs anywhere;
  slower on very large pages, acceptable for single-page use.
- **Own retry loop instead of `urllib3.Retry`:** lets us rotate the user-agent
  on every attempt and surface retry progress in the spinner.
- **Streaming body with a 10 MiB cap:** protects against accidentally pulling a
  huge file; non-HTML content types are rejected before download.
- **Brotli not advertised:** `Accept-Encoding` is `gzip, deflate` because
  Requests cannot decode `br` without an extra package.
- **robots.txt is opt-in (`--respect-robots`):** keeps the default behaviour
  simple while making the responsible choice one flag away.

## Consequences

- **Easier:** adding formats (CSV, SQLite), extractors (JSON-LD, emails),
  or a second front end that reuses `fetcher` + `parser`.
- **Harder:** nothing significant; contributors must keep layers decoupled.
- **Revisit:** multi-URL / crawl mode (needs rate limiting and a queue),
  JavaScript-rendered pages (would need a headless browser behind the same
  `Fetcher` interface), and `lxml` if performance becomes an issue.

## Action Items

1. [x] Implement the four layers and typed errors
2. [x] Verify against a local test server (success, retry, timeout, 403/404, TLS, DNS, robots, bad content type, unwritable output)
3. [ ] Add unit tests for `parser.py` using saved HTML fixtures
4. [ ] Decide whether to add a `--urls-file` batch mode (needs a rate-limit design)
