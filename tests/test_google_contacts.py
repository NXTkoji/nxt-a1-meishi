from app.models.card import Card as LegacyCard, Person as LegacyPerson
from app.services.google_contacts import _build_person_body


def test_build_person_body_adds_met_as_custom_field():
    card = LegacyCard(
        person=LegacyPerson(),
        my_company_labels=["NXT", "正康有限公司"],
    )

    body = _build_person_body(card)

    assert body["userDefined"] == [{"key": "Met As", "value": "NXT, 正康有限公司"}]


def test_build_person_body_omits_met_as_when_empty():
    card = LegacyCard(person=LegacyPerson())

    body = _build_person_body(card)

    assert "userDefined" not in body


def test_build_person_body_dedupes_and_sorts_met_as():
    card = LegacyCard(
        person=LegacyPerson(),
        my_company_labels=["NXT", "NXT", "Personal"],
    )

    body = _build_person_body(card)

    assert body["userDefined"] == [{"key": "Met As", "value": "NXT, Personal"}]
