"""Search query parser for Telegram bot commands."""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field


@dataclass
class ParsedSearchQuery:
    includes: list[str] = field(default_factory=list)
    excludes: list[str] = field(default_factory=list)
    fuzzy: list[str] = field(default_factory=list)
    regex: str | None = None


def parse_search_query(raw_query: str) -> ParsedSearchQuery:
    """
    Parse query syntax:
    - plain token => include (AND semantics)
    - +token      => include
    - -token      => exclude
    - ~tok*en     => fuzzy token ("*" acts like wildcard)
    - /regex/     => regex (admin-only at execution layer)
    Quoted phrases are supported.
    """
    parsed = ParsedSearchQuery()
    tokens = shlex.split(raw_query)

    for token in tokens:
        if not token.strip():
            continue

        if token.startswith("/") and token.endswith("/") and len(token) > 2:
            parsed.regex = token[1:-1]
            continue

        if token.startswith("+") and len(token) > 1:
            parsed.includes.append(token[1:])
            continue

        if token.startswith("-") and len(token) > 1:
            parsed.excludes.append(token[1:])
            continue

        if token.startswith("~") and len(token) > 1:
            parsed.fuzzy.append(token[1:])
            continue

        parsed.includes.append(token)

    return parsed
