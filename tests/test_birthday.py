"""Tests for the birthday field across schema, parser, and Google sync."""


def test_parsedcard_accepts_birthday():
    from app.schemas.parsed_card import ParsedCard
    card = ParsedCard(birthday="1990-05-20")
    assert card.birthday == "1990-05-20"


def test_parsedcard_birthday_defaults_none():
    from app.schemas.parsed_card import ParsedCard
    assert ParsedCard().birthday is None
