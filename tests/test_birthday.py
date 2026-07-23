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


def test_export_person_has_birthday():
    from app.models.card import Person
    p = Person(birthday="1988-12-01")
    assert p.birthday == "1988-12-01"
    assert Person().birthday == ""


def test_parse_birthday_full_date():
    from app.services.google_contacts import _parse_birthday
    assert _parse_birthday("1990-05-20") == {"year": 1990, "month": 5, "day": 20}


def test_parse_birthday_year_optional():
    from app.services.google_contacts import _parse_birthday
    assert _parse_birthday("--05-20") == {"month": 5, "day": 20}


def test_parse_birthday_invalid_returns_none():
    from app.services.google_contacts import _parse_birthday
    assert _parse_birthday("") is None
    assert _parse_birthday("nonsense") is None


def test_build_person_body_includes_birthday():
    from app.models.card import Card, Person, PersonName
    from app.services.google_contacts import _build_person_body
    card = Card(person=Person(
        names=[PersonName(value="Test Person", type="primary", language="en")],
        birthday="--05-20",
    ))
    body = _build_person_body(card)
    assert body["birthdays"] == [{"date": {"month": 5, "day": 20}, "text": "--05-20"}]


def test_build_person_body_omits_blank_birthday():
    from app.models.card import Card, Person, PersonName
    from app.services.google_contacts import _build_person_body
    card = Card(person=Person(
        names=[PersonName(value="Test Person", type="primary", language="en")],
    ))
    assert "birthdays" not in _build_person_body(card)
