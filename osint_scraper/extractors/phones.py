"""Phone number extraction validated with libphonenumber."""

from __future__ import annotations

import re
from urllib.parse import unquote

import phonenumbers

from ..models import Finding
from ._util import line_number, source_line
from .base import ExtractionContext, Extractor, register

_TEL_RE = re.compile(r"^tel:(.+)$", re.IGNORECASE)


@register
class PhoneExtractor(Extractor):
    name = "phones"

    def extract(self, context: ExtractionContext) -> list[Finding]:
        findings: list[Finding] = []
        seen: set[str] = set()
        text = context.page.text

        for match in phonenumbers.PhoneNumberMatcher(text, None):
            if not phonenumbers.is_valid_number(match.number):
                continue
            value = phonenumbers.format_number(
                match.number, phonenumbers.PhoneNumberFormat.E164
            )
            if value in seen:
                continue
            seen.add(value)
            findings.append(
                Finding(
                    category=self.name,
                    value=value,
                    confidence=0.95,
                    source=f"text:line {line_number(text, match.start)}",
                )
            )

        for link in context.page.links:
            if link["type"] != "tel":
                continue
            tel_match = _TEL_RE.match(link["url"])
            if not tel_match:
                continue
            raw = unquote(tel_match.group(1)).strip()
            try:
                number = phonenumbers.parse(raw, None)
            except phonenumbers.NumberParseException:
                continue
            if not phonenumbers.is_valid_number(number):
                continue
            value = phonenumbers.format_number(
                number, phonenumbers.PhoneNumberFormat.E164
            )
            if value in seen:
                continue
            seen.add(value)
            index = context.html.find(link["url"])
            source = (
                source_line("html", context.html, index)
                if index >= 0
                else "tel link"
            )
            findings.append(
                Finding(
                    category=self.name,
                    value=value,
                    confidence=0.95,
                    source=source,
                )
            )

        return findings
