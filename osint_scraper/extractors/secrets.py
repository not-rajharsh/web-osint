"""Known-provider secret and API-key patterns in scripts."""

from __future__ import annotations

import re

from ..models import Finding
from ._util import mask_secret, source_line
from .base import ExtractionContext, Extractor, register

_PATTERNS: tuple[tuple[str, re.Pattern[str], float, int], ...] = (
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b"), 0.95, 0),
    (
        "aws_secret_key",
        re.compile(
            r"(?i)aws_secret_access_key\s*[:=]\s*['\"]([A-Za-z0-9/+=]{40})['\"]"
        ),
        0.9,
        1,
    ),
    (
        "github_token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{82})\b"),
        0.95,
        0,
    ),
    ("gitlab_token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20}\b"), 0.95, 0),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), 0.95, 0),
    ("stripe_key", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{24,}\b"), 0.95, 0),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), 0.95, 0),
    (
        "jwt",
        re.compile(
            r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
        ),
        0.85,
        0,
    ),
    (
        "private_key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
        1.0,
        0,
    ),
)


@register
class SecretExtractor(Extractor):
    name = "secrets"

    def extract(self, context: ExtractionContext) -> list[Finding]:
        findings: list[Finding] = []
        seen: set[tuple[str, str]] = set()

        for script in context.scripts:
            for name, pattern, confidence, group in _PATTERNS:
                for match in pattern.finditer(script.content):
                    raw = match.group(group)
                    key = (name, raw)
                    if key in seen:
                        continue
                    seen.add(key)
                    findings.append(
                        Finding(
                            category=self.name,
                            value=mask_secret(raw),
                            confidence=confidence,
                            source=source_line(
                                script.label, script.content, match.start(group)
                            ),
                            data={"pattern": name},
                        )
                    )

        return findings
