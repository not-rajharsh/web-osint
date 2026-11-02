"""Static configuration: version, defaults, and the user-agent pool."""

from __future__ import annotations

APP_NAME = "OSINT Scraper"
__version__ = "1.0.0"

# --- Network defaults -------------------------------------------------------
DEFAULT_TIMEOUT = 15.0            # seconds, applied to connect and read
DEFAULT_RETRIES = 2               # extra attempts after the first
DEFAULT_BACKOFF = 1.0             # base seconds for exponential backoff
DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # refuse bodies larger than 10 MiB
MAX_REDIRECTS = 10
MAX_RETRY_AFTER = 30.0            # cap on honouring a Retry-After header

# HTTP statuses worth retrying (transient by nature).
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# Content types we are willing to parse as markup.
HTML_CONTENT_TYPES = frozenset(
    {"text/html", "application/xhtml+xml", "application/xml", "text/xml"}
)

# --- Output defaults --------------------------------------------------------
DEFAULT_OUTPUT_DIR = "exports"
DEFAULT_TABLE_ROWS = 15           # rows shown per table in the terminal
MD_MAX_LINKS = 100                # rows kept in the Markdown summary
MD_MAX_IMAGES = 50
MD_MAX_FINDINGS_PER_CATEGORY = 50
MD_TEXT_EXCERPT_CHARS = 2000

# --- Extraction defaults ----------------------------------------------------
DEFAULT_SCRIPT_LIMIT = 10         # linked scripts fetched per run
MAX_SCRIPT_BYTES = 2 * 1024 * 1024
SCRIPT_DELAY = 0.5                # seconds between linked-script requests
SCRIPT_CONTENT_TYPES = frozenset(
    {
        "application/javascript",
        "application/x-javascript",
        "text/javascript",
        "text/plain",
        "application/json",
    }
)

FINDING_CATEGORIES = (
    "emails",
    "phones",
    "social",
    "crypto",
    "secrets",
    "structured_data",
    "technologies",
)
FINDING_LABELS = {
    "emails": "Emails",
    "phones": "Phone numbers",
    "social": "Social profiles",
    "crypto": "Crypto addresses",
    "secrets": "Exposed secrets",
    "structured_data": "Structured data",
    "technologies": "Technologies",
}

# --- User-agent rotation ----------------------------------------------------
USER_AGENTS: tuple[str, ...] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:143.0) Gecko/20100101 Firefox/143.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:143.0) Gecko/20100101 Firefox/143.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:142.0) Gecko/20100101 Firefox/142.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.6 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.6 Mobile/15E148 Safari/604.1",
)

# "br" is deliberately omitted: requests cannot decode Brotli without an extra
# dependency, and advertising it would yield undecodable bodies.
BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}
