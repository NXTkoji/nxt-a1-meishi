"""Tests for the birthday field across schema, parser, and Google sync."""


def test_parsedcard_accepts_birthday():
    from app.schemas.parsed_card import ParsedCard
    card = ParsedCard(birthday="1990-05-20")
    assert card.birthday == "1990-05-20"


def test_parsedcard_birthday_defaults_none():
    from app.schemas.parsed_card import ParsedCard
    assert ParsedCard().birthday is None


def test_parser_maps_birthday_from_json():
    from app.services.claude_parser import _build_parsed_card
    data = {
        "names": [],
        "positions": [],
        "contact_details": [],
        "birthday": "--03-14",
    }
    card = _build_parsed_card(data)
    assert card.birthday == "--03-14"


def test_parser_birthday_absent_is_none():
    from app.services.claude_parser import _build_parsed_card
    card = _build_parsed_card({"names": [], "positions": [], "contact_details": []})
    assert card.birthday is None
