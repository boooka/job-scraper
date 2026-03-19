from __future__ import annotations

import pytest

from src.services.search_query import parse_search_query
from src.services.telegram_bot import TelegramBotService


def test_search_query_parser_supports_special_tokens():
    parsed = parse_search_query('python "data engineer" -java ~back* /py.*sql/')
    assert parsed.includes == ["python", "data engineer"]
    assert parsed.excludes == ["java"]
    assert parsed.fuzzy == ["back*"]
    assert parsed.regex == "py.*sql"


def test_regex_validation_rejects_overly_complex_pattern():
    ok, reason = TelegramBotService._validate_regex_pattern("(" * 11 + ")" * 11)
    assert ok is False
    assert reason is not None
