# ADR-001: Modular fetch → parse → export pipeline for the scraper CLI

**Status:** Accepted (amended by Phase 3 addendum)
**Date:** 2026-10-17
**Deciders:** Project owner

## Context

We need a local, single-command CLI that extracts headings, links, metadata,
images and text from one URL, survives common network failures, writes JSON and
Markdown to `exports/`, and shows a readable terminal UI. Constraints: Python
3.10+, httpx, BeautifulSoup4, Rich, argparse; no placeholders; easy to
extend (e.g. new export formats, new extractors) without rewriting the tool.

## Decision

Use a small package with **four layers that never import each other**, joined
only by plain dataclasses in `models.py`:

```
scraper.py (CLI / orchestration)
   ├─ urls.py      URL  → canonical URL / scope decisions
   ├─ fetcher.py   URL  → FetchResult   (httpx, retries, rate limiting, robots)
   ├─ parser.py    HTML → PageData      (BeautifulSoup, pure function)
   ├─ pipeline.py  URL  → PageResult    (fetch -> parse -> extract)
   ├─ crawler.py   seed → CrawlResult   (queue, sitemaps, aggregation)
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
Option A is cheaper today but couples them; Option C solves the crawling problem
with a much heavier framework, so we keep a small in-house queue instead.

Secondary decisions:

- **`html.parser` over `lxml`:** no compiled dependency, installs anywhere;
  slower on very large pages, acceptable for single-page use.
- **Own retry loop instead of transport-level retries:** lets us rotate the
  user-agent on every attempt and surface retry progress in the spinner.
- **Streaming body with a 10 MiB cap:** protects against accidentally pulling a
  huge file; non-HTML content types are rejected before download.
- **Brotli not advertised:** `Accept-Encoding` is `gzip, deflate` because httpx
  cannot decode `br` without an extra package.
- **robots.txt is opt-in for single pages (`--respect-robots`):** keeps the
  default behaviour simple while making the responsible choice one flag away.
  Crawls and linked-script fetches always honour robots.txt.

## Consequences

- **Easier:** adding formats (CSV, SQLite), extractors (JSON-LD, emails),
  crawl policies, or a second front end that reuses `fetcher` + `parser`.
- **Harder:** nothing significant; contributors must keep layers decoupled.
- **Revisit:** JavaScript-rendered pages (would need a headless browser behind
  the same async `Fetcher` interface), `lxml` if performance becomes an issue,
  and `--urls-file` batch mode.

## Phase 3 addendum: async fetching and scoped crawling

The fetch layer moved from Requests to `httpx.AsyncClient` with the public
`Fetcher` methods (`fetch`, `fetch_text`, `is_allowed`) retaining their names,
return models, and error types. A per-host `HostRateLimiter` serialises request
starts, and robots.txt lookups are cached per origin and de-duplicated when
concurrent callers race.

Crawling lives in `crawler.py` rather than inside the CLI: a breadth-first queue
of canonical URLs is processed level by level with bounded concurrency and a
global attempt cap. Scope is `host` or registrable `domain`; redirects that leave
scope and robots-denied pages are recorded as failures instead of aborting the
run. `origin/sitemap.xml` and sitemap indexes contribute depth-1 URLs, bounded
by document, byte, and URL caps. Per-page findings are merged by category, value,
and data, keeping the maximum confidence and the list of pages where each value
was seen.

## Action Items

1. [x] Implement the four layers and typed errors
2. [x] Verify against a local test server (success, retry, timeout, 403/404, TLS, DNS, robots, bad content type, unwritable output)
3. [x] Add unit tests for `parser.py` using saved HTML fixtures
4. [x] Add async fetching, rate limiting, and scoped crawl mode
5. [x] Add unit tests for URL handling, crawling, sitemaps, and aggregation
6. [ ] Decide whether to add a `--urls-file` batch mode (needs a rate-limit design)
