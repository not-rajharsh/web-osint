"""Common interface and registry for extraction plugins."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Mapping

from ..models import Finding, PageData, ScriptSource


@dataclass(frozen=True)
class ExtractionContext:
    """Everything an extractor may inspect for one page."""

    html: str
    base_url: str
    headers: Mapping[str, str]
    page: PageData
    scripts: tuple[ScriptSource, ...] = ()


class Extractor(ABC):
    """A small, stateless extraction plugin."""

    name: ClassVar[str]

    @abstractmethod
    def extract(self, context: ExtractionContext) -> list[Finding]:
        """Return findings discovered in ``context``."""


_REGISTRY: dict[str, type[Extractor]] = {}


def register(cls: type[Extractor]) -> type[Extractor]:
    """Register an extractor class; import of its module is enough to enable it."""
    _REGISTRY[cls.name] = cls
    return cls


def registered_extractors() -> tuple[Extractor, ...]:
    return tuple(cls() for cls in _REGISTRY.values())
