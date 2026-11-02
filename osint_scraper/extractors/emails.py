"""Email address extraction, including common obfuscation patterns."""

from __future__ import annotations

import re

from ..models import Finding
from ._util import has_valid_tld, source_line
from .base import ExtractionContext, Extractor, register

_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+\b"
)
_AT_RE = re.compile(
    r"[ \t]*(?:\[\s*at\s*\]|\(\s*at\s*\)|\{\s*at\s*\}|&#0*64;|&#x0*40;)[ \t]*",
    re.IGNORECASE,
)
_DOT_RE = re.compile(
    r"[ \t]*(?:\[\s*dot\s*\]|\(\s*dot\s*\)|\{\s*dot\s*\}|"
    r"\[\s*\.\s*\]|\(\s*\.\s*\)|&#0*46;|&#x0*2e;)[ \t]*",
    re.IGNORECASE,
)


def _normalize(text: str) -> str:
    return _DOT_RE.sub(".", _AT_RE.sub("@", text))


@register
class EmailExtractor(Extractor):
    name = "emails"

    def extract(self, context: ExtractionContext) -> list[Finding]:
        findings: list[Finding] = []
        seen: set[str] = set()
        sources = [("html", context.html)]
        sources.extend((script.label, script.content) for script in context.scripts)

        for label, text in sources:
            normalized = _normalize(text)
            for match in _EMAIL_RE.finditer(normalized):
                email = match.group(0).strip(".")
                local, _, domain = email.partition("@")
                if not local or not domain or len(local) > 64 or len(domain) > 255:
                    continue
                if not has_valid_tld(domain):
                    continue
                key = email.lower()
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    Finding(
                        category=self.name,
                        value=email,
                        confidence=0.95,
                        source=source_line(label, normalized, match.start()),
                    )
                )
        return findings
