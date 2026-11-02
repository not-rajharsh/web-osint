# OSINT Scraper

A local, CLI-driven utility that extracts structured data from a web page and
saves it as JSON and Markdown, with a color-coded terminal UI.

Extracts: **metadata** (title, description, keywords, canonical, language,
Open Graph, Twitter cards, favicon), **headings** (H1-H6 hierarchy), **links**
(deduplicated, classified internal / external / mailto / tel), **images**
(including lazy-loaded and `srcset`), and **visible text**.

> Only scrape sites you are permitted to access and respect their terms of use.
> Use `--respect-robots` to honour robots.txt.

## Install

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Requires Python 3.10+.

## Usage

```bash
python scraper.py --url https://example.com
python scraper.py --url https://example.com --format json
python scraper.py -u example.org -f md --respect-robots --limit 25
python scraper.py -u https://example.com -q        # prints only the file paths
```

| Option | Default | Description |
|---|---|---|
| `-u, --url` | required | Target page. Scheme optional (defaults to `https://`). |
| `-f, --format` | `both` | `json`, `md`, or `both`. |
| `-o, --output-dir` | `exports` | Directory for output files (created if missing). |
| `-t, --timeout` | `15` | Network timeout in seconds. |
| `-r, --retries` | `2` | Retries for timeouts, connection drops, and HTTP 429/5xx. |
| `-a, --user-agent` | rotate | Use a fixed User-Agent instead of the built-in pool. |
| `--respect-robots` | off | Refuse pages disallowed by robots.txt. |
| `-l, --limit` | `15` | Rows per terminal table (exports always contain everything\*). |
| `--no-color` | off | Disable colour (`NO_COLOR` is also honoured). |
| `-q, --quiet` | off | No spinner or tables; print only exported paths. |

\*The Markdown summary caps links at 100 and images at 50 and says so; JSON is complete.

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
  "tool": "OSINT Scraper", "version": "1.0.0", "scraped_at": "2026-10-17T03:25:15+00:00",
  "fetch": { "requested_url": "...", "final_url": "...", "status_code": 200,
             "elapsed_ms": 120, "content_type": "text/html", "encoding": "utf-8",
             "size_bytes": 1187, "user_agent": "...", "attempts": 1, "redirected": false },
  "page":  { "metadata": { "title": "...", "open_graph": {}, "twitter": {} },
             "headings": [{ "level": 1, "text": "..." }],
             "links":    [{ "text": "...", "url": "...", "type": "internal", "rel": null }],
             "images":   [{ "url": "...", "alt": "...", "width": null, "height": null }],
             "text": "...", "word_count": 22 }
}
```

## Project layout

```
osint_scraper/
  scraper.py               CLI entry point and orchestration
  config.py                defaults, user-agent pool, limits
  models.py                dataclasses shared between layers
  fetcher.py               Requests session, retries, UA rotation, robots.txt
  parser.py                BeautifulSoup extraction (pure function)
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
| Summary / Metadata / Links / Images tables | populated; truncated (footer says "Showing N of M"); empty (warn notice) | Long cells ellipsize or fold |
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
- **New extractor** (e.g. JSON-LD, emails): add a function in `parser.py`, a field on `PageData`, and surface it in `tui.py` / `exporter.py`.
- **JavaScript-rendered pages:** implement a `Fetcher` with the same `fetch()` signature backed by a headless browser.
