"""Discovery and execution of registered extraction plugins."""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

from ..models import Finding
from .base import ExtractionContext, Extractor, registered_extractors


def load_extractors() -> tuple[Extractor, ...]:
    package = __package__ or "osint_scraper.extractors"
    for module in pkgutil.iter_modules([str(Path(__file__).parent)]):
        if module.name.startswith("_"):
            continue
        importlib.import_module(f"{package}.{module.name}")
    return registered_extractors()


def run_extractors(
    context: ExtractionContext,
    extractors: tuple[Extractor, ...] | None = None,
) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    for extractor in extractors if extractors is not None else load_extractors():
        findings.extend(extractor.extract(context))
    return tuple(findings)
