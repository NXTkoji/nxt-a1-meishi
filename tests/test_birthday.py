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


def test_person_update_schema_birthday():
    from app.schemas.api import PersonUpdate
    # explicit value
    assert PersonUpdate(birthday="--05-20").birthday == "--05-20"
    # empty string preserved in the set fields (endpoint maps "" -> NULL)
    body = PersonUpdate(birthday="")
    assert "birthday" in body.model_dump(exclude_unset=True)
    # omitted -> not in set fields (no accidental clobber)
    assert "birthday" not in PersonUpdate().model_dump(exclude_unset=True)


def test_person_out_has_birthday():
    from app.schemas.api import PersonOut
    from datetime import datetime
    p = PersonOut(id=1, external_id="x", notes=None, birthday="1990-05-20",
                  created_at=datetime.now(), updated_at=datetime.now())
    assert p.birthday == "1990-05-20"
    # default when omitted
    p2 = PersonOut(id=1, external_id="x", notes=None,
                   created_at=datetime.now(), updated_at=datetime.now())
    assert p2.birthday is None


def test_update_person_endpoint_exists():
    import inspect
    from app.routers.v2.persons import update_person
    sig = inspect.signature(update_person)
    assert "person_ext_id" in sig.parameters
    assert "body" in sig.parameters
