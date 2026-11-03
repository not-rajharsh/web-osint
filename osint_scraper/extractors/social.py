"""Social media profile URLs and handles."""

from __future__ import annotations

from urllib.parse import urlsplit

from ..models import Finding
from ..urls import host
from ._util import source_line
from .base import ExtractionContext, Extractor, register

_TWITTER_RESERVED = {
    "i", "intent", "share", "hashtag", "search", "home", "login", "signup",
    "tos", "privacy", "settings", "explore",
}
_GITHUB_RESERVED = {
    "features", "topics", "trending", "login", "join", "pricing", "about",
    "contact", "settings", "explore", "marketplace",
}
_FACEBOOK_RESERVED = {
    "sharer", "share", "dialog", "plugins", "tr", "login", "help", "policies",
    "privacy", "terms", "events", "groups", "watch", "marketplace",
}
_INSTAGRAM_RESERVED = {
    "p", "reel", "reels", "explore", "accounts", "stories", "about", "legal",
}
_YOUTUBE_RESERVED = {
    "watch", "shorts", "results", "feed", "playlist", "premium", "about",
    "howyoutubeworks", "creators", "ads", "t", "account",
}
_REDDIT_RESERVED = {
    "r", "u", "user", "search", "login", "register", "settings", "premium",
}
_PINTEREST_RESERVED = {"pin", "ideas", "search", "login", "business"}
_TELEGRAM_RESERVED = {"joinchat", "s", "addstickers", "share", "proxy"}
_MASTODON_HOSTS = {
    "mastodon.social", "mastodon.online", "fosstodon.org", "hachyderm.io",
    "infosec.exchange", "techhub.social", "mstdn.social",
}


def _parts(url: str) -> list[str]:
    return [part for part in urlsplit(url).path.split("/") if part]


def _first(parts: list[str], reserved: set[str]) -> str | None:
    if parts and parts[0].lower() not in reserved:
        return parts[0]
    return None


def _classify(url: str) -> tuple[str, str | None] | None:
    name = host(url)
    parts = _parts(url)
    lowered = [part.lower() for part in parts]

    if name in {"twitter.com", "x.com", "mobile.twitter.com"}:
        handle = _first(parts, _TWITTER_RESERVED)
        return ("Twitter/X", f"@{handle}") if handle else None

    if name in {"github.com", "gist.github.com"}:
        handle = _first(parts, _GITHUB_RESERVED)
        return ("GitHub", handle) if handle else None

    if name == "linkedin.com":
        if len(parts) >= 2 and lowered[0] in {"in", "company", "school"}:
            return ("LinkedIn", f"{lowered[0]}/{parts[1]}")
        return None

    if name in {"facebook.com", "m.facebook.com"}:
        handle = _first(parts, _FACEBOOK_RESERVED)
        return ("Facebook", handle) if handle else None

    if name in {"instagram.com", "m.instagram.com"}:
        handle = _first(parts, _INSTAGRAM_RESERVED)
        return ("Instagram", f"@{handle}") if handle else None

    if name in {"youtube.com", "m.youtube.com", "youtu.be"}:
        if not parts:
            return None
        if parts[0].startswith("@"):
            return ("YouTube", parts[0])
        if lowered[0] in {"channel", "c", "user"} and len(parts) >= 2:
            return ("YouTube", f"{lowered[0]}/{parts[1]}")
        return None

    if name in {"tiktok.com", "m.tiktok.com"}:
        if parts and parts[0].startswith("@"):
            return ("TikTok", parts[0])
        return None

    if name in {"reddit.com", "old.reddit.com"}:
        if len(parts) >= 2 and lowered[0] in {"user", "u"}:
            return ("Reddit", f"u/{parts[1]}")
        return None

    if name in {"t.me", "telegram.me"}:
        handle = _first(parts, _TELEGRAM_RESERVED)
        return ("Telegram", f"@{handle}") if handle else None

    if name in _MASTODON_HOSTS or name.startswith("mastodon."):
        if parts and parts[0].startswith("@"):
            return ("Mastodon", parts[0])
        return None

    if name.endswith("pinterest.com"):
        handle = _first(parts, _PINTEREST_RESERVED)
        return ("Pinterest", handle) if handle else None

    return None


@register
class SocialExtractor(Extractor):
    name = "social"

    def extract(self, context: ExtractionContext) -> list[Finding]:
        findings: list[Finding] = []
        seen: set[tuple[str, str]] = set()

        for link in context.page.links:
            if link["type"] not in {"internal", "external"}:
                continue
            classified = _classify(link["url"])
            if classified is None:
                continue
            platform, handle = classified
            key = (platform, (handle or link["url"]).lower())
            if key in seen:
                continue
            seen.add(key)

            index = context.html.find(link["url"])
            source = (
                source_line("html", context.html, index)
                if index >= 0
                else "link"
            )
            data = {"platform": platform, "profile_url": link["url"]}
            if handle:
                data["handle"] = handle
            findings.append(
                Finding(
                    category=self.name,
                    value=f"{platform} {handle}" if handle else link["url"],
                    confidence=0.9 if handle else 0.8,
                    source=source,
                    data=data,
                )
            )

        return findings
