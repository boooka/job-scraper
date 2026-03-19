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


def test_format_vacancy_card_md_escapes_dirty_input():
    card = TelegramBotService._format_vacancy_card_md(
        title="Sr. Backend Python Developer (AI) [Lead]!",
        company="A|B_Company-(X)",
        location="Vilnius [LT] - Remote!",
        url="https://example.com/jobs/python_(ai)?x=1&y=[2]",
    )

    # Markdown wrappers must remain intact
    assert card.startswith("*")
    assert "\n_" in card

    # Reserved MarkdownV2 chars in user data must be escaped
    assert "\\(" in card
    assert "\\)" in card
    assert "\\[" in card
    assert "\\]" in card
    assert "\\_" in card
    assert "\\-" in card
    assert "\\!" in card
    assert "\\|" in card


def test_message_rendering_with_reserved_symbols_set():
    dirty = "|()_-![]"
    card = TelegramBotService._format_vacancy_card_md(
        title=f"Title {dirty}",
        company=f"Company {dirty}",
        location=f"Location {dirty}",
        url=f"https://example.com/{dirty}",
    )

    # Ensure each symbol from the required set is escaped.
    assert "\\|" in card
    assert "\\(" in card
    assert "\\)" in card
    assert "\\_" in card
    assert "\\-" in card
    assert "\\!" in card
    assert "\\[" in card
    assert "\\]" in card


def test_vacancy_card_escapes_template_pipe_separator():
    card = TelegramBotService._format_vacancy_card_md(
        title="Title",
        company="Company",
        location="Location",
        url="https://example.com",
    )
    assert " \\| " in card
