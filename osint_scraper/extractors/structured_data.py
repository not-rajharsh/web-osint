"""JSON-LD, schema.org, and microdata extraction."""

from __future__ import annotations

import json
from typing import Any

from bs4 import BeautifulSoup, Tag

from ..models import Finding
from ._util import line_number
from .base import ExtractionContext, Extractor, register


def _jsonld_objects(value: Any) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            objects.extend(_jsonld_objects(item))
    elif isinstance(value, dict):
        graph = value.get("@graph")
        if isinstance(graph, list):
            objects.extend(_jsonld_objects(graph))
        if value.get("@type") or not isinstance(graph, list):
            objects.append(value)
    return objects


def _type_label(item: dict[str, Any]) -> str:
    item_type = item.get("@type")
    if isinstance(item_type, list):
        return ", ".join(str(part) for part in item_type) or "JSON-LD"
    if isinstance(item_type, str) and item_type.strip():
        return item_type.strip()
    return "JSON-LD"


def _source_for(html: str, needle: str, fallback: str) -> str:
    if needle:
        index = html.find(needle)
        if index >= 0:
            return f"{fallback}:line {line_number(html, index)}"
    return fallback


def _property_value(tag: Tag) -> str | None:
    for attribute in ("content", "href", "src", "datetime"):
        value = tag.get(attribute)
        if isinstance(value, str) and value.strip():
            return value.strip()
    text = " ".join(tag.get_text(" ").split())
    return text or None


def _microdata_item(tag: Tag) -> dict[str, Any]:
    item: dict[str, Any] = {}
    item_type = tag.get("itemtype")
    if isinstance(item_type, str) and item_type.strip():
        item["@type"] = item_type.strip()

    for prop in tag.find_all(attrs={"itemprop": True}):
        parents = prop.find_parents(attrs={"itemscope": True}, limit=1)
        if parents and parents[0] is not tag:
            continue
        name = prop.get("itemprop")
        if isinstance(name, list):
            name = " ".join(str(part) for part in name)
        if not isinstance(name, str) or not name.strip():
            continue
        value: Any
        if prop.get("itemscope"):
            value = _microdata_item(prop)
        else:
            value = _property_value(prop)
        if value in (None, "", {}, []):
            continue
        if name in item:
            existing = item[name]
            if isinstance(existing, list):
                existing.append(value)
            else:
                item[name] = [existing, value]
        else:
            item[name] = value
    return item


@register
class StructuredDataExtractor(Extractor):
    name = "structured_data"

    def extract(self, context: ExtractionContext) -> list[Finding]:
        soup = BeautifulSoup(context.html, "html.parser")
        findings: list[Finding] = []

        for index, tag in enumerate(soup.find_all("script")):
            script_type = tag.get("type")
            if not isinstance(script_type, str) or "ld+json" not in script_type.lower():
                continue
            raw = tag.string if tag.string is not None else tag.get_text()
            if not raw.strip():
                continue
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            for item in _jsonld_objects(parsed):
                label = _type_label(item)
                findings.append(
                    Finding(
                        category=self.name,
                        value=label,
                        confidence=1.0,
                        source=_source_for(context.html, raw.strip()[:80], f"json-ld[{index}]"),
                        data={"format": "json-ld", "object": item},
                    )
                )

        for index, tag in enumerate(soup.find_all(attrs={"itemscope": True})):
            if tag.find_parent(attrs={"itemscope": True}) is not None:
                continue
            item = _microdata_item(tag)
            if not item:
                continue
            item_type = tag.get("itemtype")
            label = (
                item_type.strip()
                if isinstance(item_type, str) and item_type.strip()
                else str(item.get("name") or "Microdata item")
            )
            findings.append(
                Finding(
                    category=self.name,
                    value=label,
                    confidence=0.9,
                    source=_source_for(
                        context.html,
                        item_type if isinstance(item_type, str) else "",
                        f"microdata[{index}]",
                    ),
                    data={"format": "microdata", "object": item},
                )
            )

        return findings
