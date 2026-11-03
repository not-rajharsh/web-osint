"""Shared validation and provenance helpers for extractors."""

from __future__ import annotations


def line_number(text: str, index: int) -> int:
    return text.count("\n", 0, max(index, 0)) + 1


def source_line(label: str, text: str, index: int) -> str:
    return f"{label}:line {line_number(text, index)}"


def mask_secret(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}…{value[-4:]}"
