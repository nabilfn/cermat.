"""Shared grounding check: every number a model writes must exist in its facts."""

from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from typing import Any

_NUMBER = re.compile(r"(?<![A-Za-z])[+\-−]?\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> set[Decimal]:
    found: set[Decimal] = set()
    for token in _NUMBER.findall(text):
        cleaned = token.replace(",", "").replace("−", "-").lstrip("+-")
        try:
            found.add(Decimal(cleaned).normalize())
        except InvalidOperation:
            continue
    return found


def ungrounded(texts: list[str], facts: dict[str, Any]) -> set[Decimal]:
    """Numbers in ``texts`` that do not appear anywhere in ``facts``."""
    allowed = numbers_in(json.dumps(facts, ensure_ascii=False, default=str))
    allowed.update({Decimal(0), Decimal(1)})
    written: set[Decimal] = set()
    for text in texts:
        written |= numbers_in(text)
    return {value for value in written if value not in allowed}
