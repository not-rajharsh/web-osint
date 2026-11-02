"""Bitcoin and Ethereum address extraction with checksum validation."""

from __future__ import annotations

import hashlib
import re

from ..models import Finding
from ._util import source_line
from .base import ExtractionContext, Extractor, register

_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32_GENERATORS = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
_BECH32_CONSTANTS = (1, 0x2BC830A3)

_BTC_BASE58_RE = re.compile(r"\b[13][1-9A-HJ-NP-Za-km-z]{25,34}\b")
_BTC_BECH32_RE = re.compile(r"\b(?:bc|tb)1[02-9ac-hj-np-z]{11,71}\b", re.IGNORECASE)
_ETH_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")


def _valid_base58check(address: str) -> bool:
    number = 0
    for char in address:
        if char not in _BASE58_ALPHABET:
            return False
        number = number * 58 + _BASE58_ALPHABET.index(char)
    combined = number.to_bytes((number.bit_length() + 7) // 8, "big")
    combined = b"\x00" * (len(address) - len(address.lstrip("1"))) + combined
    if len(combined) != 25 or combined[0] not in (0x00, 0x05):
        return False
    payload, checksum = combined[:-4], combined[-4:]
    return hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4] == checksum


def _bech32_polymod(values: list[int]) -> int:
    checksum = 1
    for value in values:
        top = checksum >> 25
        checksum = ((checksum & 0x1FFFFFF) << 5) ^ value
        for index, generator in enumerate(_BECH32_GENERATORS):
            if (top >> index) & 1:
                checksum ^= generator
    return checksum


def _bech32_hrp_expand(hrp: str) -> list[int]:
    return [ord(char) >> 5 for char in hrp] + [0] + [ord(char) & 31 for char in hrp]


def _valid_bech32(address: str) -> bool:
    if address.lower() != address and address.upper() != address:
        return False
    lowered = address.lower()
    position = lowered.rfind("1")
    if position < 1 or position + 7 > len(lowered):
        return False
    hrp, data = lowered[:position], lowered[position + 1 :]
    if hrp not in {"bc", "tb"} or any(char not in _BECH32_CHARSET for char in data):
        return False
    values = [_BECH32_CHARSET.index(char) for char in data]
    return _bech32_polymod(_bech32_hrp_expand(hrp) + values) in _BECH32_CONSTANTS


def _valid_eth(address: str) -> bool:
    return bool(re.fullmatch(r"0x[a-fA-F0-9]{40}", address))


@register
class CryptoExtractor(Extractor):
    name = "crypto"

    def extract(self, context: ExtractionContext) -> list[Finding]:
        findings: list[Finding] = []
        seen: set[str] = set()
        sources = [("html", context.html)]
        sources.extend((script.label, script.content) for script in context.scripts)

        for label, text in sources:
            for match in _BTC_BASE58_RE.finditer(text):
                address = match.group(0)
                if not _valid_base58check(address):
                    continue
                key = address.lower()
                if key in seen:
                    continue
                seen.add(key)
                findings.append(self._finding(address, "BTC", 0.99, label, text, match.start()))

            for match in _BTC_BECH32_RE.finditer(text):
                address = match.group(0)
                if not _valid_bech32(address):
                    continue
                key = address.lower()
                if key in seen:
                    continue
                seen.add(key)
                findings.append(self._finding(address, "BTC", 0.99, label, text, match.start()))

            for match in _ETH_RE.finditer(text):
                address = match.group(0)
                if not _valid_eth(address):
                    continue
                key = address.lower()
                if key in seen:
                    continue
                seen.add(key)
                findings.append(self._finding(address, "ETH", 0.9, label, text, match.start()))

        return findings

    def _finding(
        self,
        address: str,
        asset: str,
        confidence: float,
        label: str,
        text: str,
        index: int,
    ) -> Finding:
        return Finding(
            category=self.name,
            value=address,
            confidence=confidence,
            source=source_line(label, text, index),
            data={"asset": asset},
        )
