"""Plugin-style extraction package.

Every non-underscore module in this package is imported automatically. Modules
declare extractors by subclassing :class:`Extractor` and decorating the class
with :func:`register`.
"""

from __future__ import annotations

from ..models import Finding, ScriptSource
from .base import ExtractionContext, Extractor, register, registered_extractors
from .runner import load_extractors, run_extractors

__all__ = [
    "Extractor",
    "ExtractionContext",
    "Finding",
    "ScriptSource",
    "load_extractors",
    "register",
    "registered_extractors",
    "run_extractors",
]
