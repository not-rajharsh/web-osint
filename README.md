# OSINT Scraper

A local, CLI-driven utility that extracts structured data from a web page or a
scoped, depth-limited crawl and saves it as JSON and Markdown, with a color-coded
terminal UI.

Extracts: **metadata** (title, description, keywords, canonical, language,
Open Graph, Twitter cards, favicon), **headings** (H1-H6 hierarchy), **links**
(deduplicated, classified internal / external / mailto / tel), **images**
(including lazy-loaded and `srcset`), and **visible text**.

It also extracts **entities and findings** with confidence and line-level
provenance: email addresses (including common obfuscation), international phone
numbers, social profiles, Bitcoin/Ethereum addresses, exposed secrets from
inline JavaScript (and optionally linked scripts), JSON-LD/microdata, and
technology fingerprints. See [Entity extraction](#entity-extraction).

> Only scrape sites you are permitted to access and respect their terms of use.
> Crawls always honour robots.txt; use `--respect-robots` to enforce it for a
> single-page scrape too. Linked-script requests are always robots-checked.
>
> Detection is best-effort, not proof. Review findings before acting on them.
> Secret values are always masked in exports and terminal output.

## Install

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install .                                       # runtime only
pip install -e ".[dev]"                             # development (tests, ruff, mypy)
```

Requires Python 3.10+.

## Usage

```bash
osint-scraper --url https://example.com
osint-scraper --url https://example.com --format json
osint-scraper -u example.org -f md --respect-robots --limit 25
osint-scraper -u https://example.com -q        # prints only the file paths
osint-scraper -u https://example.com --scan-scripts  # also scan same-site JS
osint-scraper -u https://example.com --crawl   # crawl same-site pages
osint-scraper -u example.com --crawl --depth 3 --max-pages 100 --scope host

python scraper.py --url https://example.com    # works from a checkout too
```

| Option | Default | Description |
|---|---|---|
| `-u, --url` | required | Target page. Scheme optional (defaults to `https://`). |
| `-f, --format` | `both` | `json`, `md`, or `both`. |
| `-o, --output-dir` | `exports` | Directory for output files (created if missing). |
| `-t, --timeout` | `15` | Network timeout in seconds. |
| `-r, --retries` | `2` | Retries for timeouts, connection drops, and HTTP 429/5xx. |
| `-a, --user-agent` | rotate | Use a fixed User-Agent instead of the built-in pool. |
| `--respect-robots` | off | Refuse a single page disallowed by robots.txt (crawls always check). |
| `--scan-scripts` | off | Fetch same-site linked JavaScript for secret scanning (robots.txt is always checked for these requests). |
| `--crawl` | off | Crawl linked pages starting from `--url` and aggregate findings. |
| `--depth` | `2` | Maximum link depth below the seed page. |
| `--max-pages` | `25` | Maximum successful and failed page fetches per crawl. |
| `--scope` | `domain` | Stay within the seed `host` or registrable `domain` (subdomains included). |
| `--delay` | `1.0` | Minimum seconds between requests to the same host during a crawl. |
| `--concurrency` | `5` | Maximum in-flight page requests per crawl. |
| `-l, --limit` | `15` | Rows per terminal table (exports always contain everything\*). |
| `--no-color` | off | Disable colour (`NO_COLOR` is also honoured). |
| `-q, --quiet` | off | No spinner or tables; print only exported paths. |

\*The Markdown summary caps links at 100, images at 50, and each finding category
at 50 and says so; JSON is complete.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Fetch failed (timeout, DNS, TLS, HTTP error, bad content type, robots) |
| 2 | Invalid input |
| 3 | Export failed (e.g. unwritable directory) |
| 4 | Parse failed |
| 130 | Interrupted (Ctrl+C) |

## Output

Files are written to `exports/` as `<host>-<path>_<UTC timestamp>.json|md`.
Existing files are never overwritten (`-1`, `-2`, ... suffix is added).

```jsonc
{
  "tool": "OSINT Scraper", "version": "1.1.0", "scraped_at": "2026-10-17T03:25:15+00:00",
  "fetch": { "requested_url": "...", "final_url": "...", "status_code": 200,
             "elapsed_ms": 120, "content_type": "text/html", "encoding": "utf-8",
             "size_bytes": 1187, "user_agent": "...", "attempts": 1, "redirected": false },
  "page":  { "metadata": { "title": "...", "open_graph": {}, "twitter": {} },
             "headings": [{ "level": 1, "text": "..." }],
             "links":    [{ "text": "...", "url": "...", "type": "internal", "rel": null }],
             "images":   [{ "url": "...", "alt": "...", "width": null, "height": null }],
             "text": "...", "word_count": 22 },
  "findings": {
    "emails": [{ "value": "alice@example.com", "confidence": 0.95, "source": "html:line 12" }],
    "secrets": [{ "value": "AKIA…CDEF", "confidence": 0.95,
                  "source": "https://example.com/app.js:line 3",
                  "data": { "pattern": "aws_access_key" },
                  "pages": ["https://example.com/"] }]
  },
  "pages": [
    { "url": "https://example.com/", "depth": 0, "fetch": {}, "page": {}, "findings": [] }
  ],
  "crawl": {
    "depth": 2, "max_pages": 25, "scope": "domain",
    "sitemaps": ["https://example.com/sitemap.xml"],
    "failures": [{ "url": "...", "depth": 1, "kind": "robots", "message": "..." }]
  }
}
```

Findings are grouped in a fixed category order (`emails`, `phones`, `social`,
`crypto`, `secrets`, `structured_data`, `technologies`). Each finding includes a
0–1 confidence and a source such as `html:line 12`, `text:line 3`, or a script
URL plus line number. Crawl findings are merged by category, value, and data;
`pages` lists every page where the value appeared, and confidence is the maximum
seen. The `pages` and `crawl` keys are present only for crawl runs.

## Crawling

`--crawl` processes the seed page, then follows same-scope HTML links breadth-first
up to `--depth`. URLs are canonicalised and deduplicated (fragments removed,
default ports normalised, dot segments collapsed) before queueing. The crawl also
reads `origin/sitemap.xml`, follows sitemap indexes, and queues in-scope URLs at
depth 1, subject to the same depth/page/scope limits and robots checks.

Crawls always check robots.txt, apply a per-host delay, cap concurrency, and stop
after `--max-pages` attempts. Cross-scope redirects and disallowed/failed pages are
recorded under `crawl.failures` rather than aborting the whole run. If the seed
itself fails, the command exits with fetch error code 1. Sitemap loading is capped
at 5 documents, 5 MiB each, and 1,000 URLs.

## Entity extraction

| Category | What is detected | Notes |
|---|---|---|
| Emails | Addresses in HTML and JavaScript | Handles `[at]` / `[dot]` and HTML-entity obfuscation; requires a valid public suffix. |
| Phones | Numbers in page text and `tel:` links | Validated with libphonenumber and normalised to E.164. |
| Social | Profile URLs and handles | GitHub, Twitter/X, LinkedIn, Facebook, Instagram, YouTube, TikTok, Reddit, Telegram, Mastodon, Pinterest. |
| Crypto | Bitcoin and Ethereum addresses | Bitcoin Base58Check and Bech32 checksums are verified. |
| Secrets | Known token/key formats | AWS, GitHub, GitLab, Slack, Stripe, Google API keys, JWTs, and private-key headers. Values are masked (`AKIA…CDEF`). |
| Structured data | JSON-LD and schema.org microdata | Malformed JSON-LD is ignored. |
| Technologies | Server, framework, CMS, and library hints | Uses response headers, `generator` metadata, assets, and framework markers. |

Inline scripts are always scanned. Linked scripts are fetched only with
`--scan-scripts`, only from the same registrable domain, and with robots.txt
always enforced. To stay polite, script scanning is capped at 10 requests,
2 MiB per script, and 0.5 s between requests.

To add a category, create a module in `osint_scraper/extractors/` with a
subclass of `Extractor`, decorate it with `@register`, and add its name and
label to `FINDING_CATEGORIES` / `FINDING_LABELS` in `config.py`. Modules are
discovered automatically.

## Project layout

```
osint_scraper/
  scraper.py               CLI entry point and async orchestration
  config.py                defaults, user-agent pool, crawl limits
  models.py                dataclasses shared between layers
  urls.py                  URL canonicalisation and scope helpers
  fetcher.py               Async httpx client, retries, rate limiting, robots.txt
  pipeline.py              Single-page fetch/parse/extract stage
  crawler.py               Scoped queue, sitemap discovery, finding aggregation
  parser.py                BeautifulSoup extraction (pure function)
  scripts.py               Inline and linked JavaScript collection
  extractors/              Auto-discovered extraction plugins
    base.py                Extractor interface, context, registry
    runner.py              Plugin discovery and execution
    emails.py phones.py social.py crypto.py secrets.py
    structured_data.py technologies.py
  exporter.py              JSON and Markdown writers
  tui.py                   Rich design tokens and components
scraper.py                 backwards-compatible wrapper for the CLI
tests/                     pytest unit tests (network mocked)
docs/ADR-001-modular-pipeline.md
```

Design rationale: [docs/ADR-001-modular-pipeline.md](docs/ADR-001-modular-pipeline.md).

## Resilience behaviour

- **User-agent rotation:** a different browser UA is chosen for every attempt (or pin one with `-a`).
- **Retries:** exponential backoff with jitter for timeouts, dropped connections, and HTTP 429/500/502/503/504; honours `Retry-After` (capped at 30 s). Non-transient errors (403, 404, DNS, TLS) fail immediately.
- **Concurrency:** `httpx.AsyncClient` fetches pages concurrently, while a per-host lock enforces `--delay` between request starts. Crawl robots checks and sitemap reads share the same limiter.
- **Safety limits:** non-HTML content types are rejected before download; bodies over 10 MiB are refused; redirect loops stop after 10 hops.
- **Encoding:** decoded using the header charset, then `<meta>` charset, then sniffing.

## Terminal UI design system

### Tokens

| Token | Value | Used for |
|---|---|---|
| `brand` | bold cyan | titles, spinner, banner |
| `success` | bold green | OK status, internal links, saved files |
| `warning` | bold yellow | redirects, external links, empty-section notices |
| `danger` | bold red | errors, 4xx/5xx status |
| `accent` | magenta | metadata keys, mailto/tel links |
| `muted` | dim | secondary text, indices, user-agent |
| `link` | cyan | URLs |
| Box / padding | rounded / `(0, 1)` | every table and panel |

### Components

| Component | Variants / states | Notes |
|---|---|---|
| Banner | default; hidden in `--quiet` | Name and version |
| Status spinner | message updates per stage (robots, fetch, retry, parse, write); no-op in `--quiet` | Retry messages show attempt count and wait |
| Notice | ok / warn / error / info | Errors go to stderr |
| Error panel | title, message, optional hint | One hint per failure kind |
| Summary / Metadata / Entities & Findings / Links / Images tables | populated; truncated (footer says "Showing N of M"); empty (warn notice) | Findings show type, value, confidence, and source; long cells ellipsize or fold |
| Heading tree | nested by level | Respects `--limit` |
| Saved panel | one line per file | `--quiet` prints bare paths for piping |

### Accessibility

- Colour is never the only signal: every status has a symbol **and** a text label (`✔ 200 OK`, `✖ Fetch failed (timeout)`); link types are spelled out.
- Symbols fall back to ASCII on non-UTF-8 terminals.
- `--no-color` and the `NO_COLOR` environment variable are honoured.
- Scraped text is rendered literally (never parsed as Rich markup), so pages containing `[bold]` cannot restyle or break the output.
- Errors use stderr; `--quiet` output is stable and script-friendly.

## Extending

- **New export format:** add a `render_*` function and a branch in `export_report` (`exporter.py`).
- **New extractor:** add a module under `osint_scraper/extractors/` as described above.
- **JavaScript-rendered pages:** implement an async `Fetcher.fetch()` with the same signature backed by a headless browser.
