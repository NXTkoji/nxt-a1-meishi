"""
Confirm must point each work contact at the right organization of the person.

A work contact names its organization by list index in its label ("_pos:N",
none = index 0). In the draft the index is into ParsedCard.positions; on the
person it is into their positions ordered by id. The two lists differ when:
  - the card is saved onto an existing person who already has positions, or
  - a draft position has no organization name and is skipped on save.
"""
import asyncio
import io

import pytest
from PIL import Image
from sqlalchemy import select

from app.config import settings
from app.db.models import ContactDetail, Organization, Person, Position, ScanSession, ScanSessionImage

SID = "22222222-3333-4444-5555-666666666666"
TEMP_CARD = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (600, 360), (200, 200, 200)).save(buf, format="JPEG", quality=90)
    return buf.getvalue()


@pytest.fixture
def staged(client_with_test_db, tmp_path, monkeypatch):
    """A review-stage session with one single-sided card."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "api_key", "")
    # Don't reach out to Google People in the post-response background task.
    monkeypatch.setattr("app.routers.v2.sessions.auto_sync_card", lambda card_id: None)

    temp_dir = settings.temp_path / SID
    temp_dir.mkdir(parents=True)
    (temp_dir / "front.jpg").write_bytes(_jpeg())

    async def _seed():
        async with client_with_test_db.session_maker() as db:
            session = ScanSession(external_id=SID, status="review")
            db.add(session)
            await db.flush()
            db.add(ScanSessionImage(
                session_id=session.id, image_path=f"{SID}/front.jpg",
                image_filename="front.jpg", temp_card_id=TEMP_CARD, side_order=0,
            ))
            await db.commit()

    asyncio.run(_seed())
    return client_with_test_db


def _db(client, fn):
    async def _inner():
        async with client.session_maker() as db:
            result = await fn(db)
            await db.commit()
            return result
    return asyncio.run(_inner())


def _org(name: str) -> dict:
    return {"org_names": [{"language": "en", "name": {"value": name}}]}


def _contact(detail_type: str, value: str, label: str | None = None) -> dict:
    return {"detail_type": detail_type, "value": {"value": value}, "label": label}


def _confirm(client, positions, contacts, match_person_id=None):
    card = {
        "temp_card_id": TEMP_CARD,
        "parsed": {
            "names": [{"language": "en", "full_name": {"value": "Work Contact Test"}}],
            "positions": positions,
            "contact_details": contacts,
        },
    }
    if match_person_id is not None:
        card["match_person_id"] = match_person_id
    res = client.post(f"/api/v2/sessions/{SID}/confirm", json={"cards": [card]})
    assert res.status_code == 200, res.text


async def _labels(db):
    return {d.value: d.label for d in (await db.execute(select(ContactDetail))).scalars()}


def test_card_saved_onto_existing_person_points_at_its_own_org(staged):
    """The person already has org A at index 0. The card's only org B is index
    0 in the draft but index 1 on the person, so its contacts must say _pos:1."""
    async def _existing(db):
        person = Person(external_id="existing")
        db.add(person)
        await db.flush()
        org = Organization()
        db.add(org)
        await db.flush()
        db.add(Position(person_id=person.id, org_id=org.id))
        db.add(ContactDetail(person_id=person.id, detail_type="phone_work", value="A-phone"))
        return person.id
    person_id = _db(staged, _existing)

    _confirm(staged, [_org("B")], [
        _contact("phone_work", "B-phone"),
        _contact("phone_fax", "B-fax", label="FAX"),  # printed label at draft index 0
        _contact("phone_mobile", "mobile"),           # personal: never relabelled
    ], match_person_id=person_id)

    assert _db(staged, _labels) == {
        "A-phone": None,
        "B-phone": "_pos:1",
        "B-fax": "_pos:1",
        "mobile": None,
    }


def test_skipped_nameless_org_does_not_shift_contacts_out_of_range(staged):
    """Draft positions: [no org name, B]. The nameless one is skipped, so B is
    index 0 on the person; a contact labelled _pos:1 (B) must become index 0."""
    _confirm(staged, [{"org_names": [], "details": [{"language": "en", "title": {"value": "Advisor"}}]}, _org("B")], [
        _contact("phone_work", "B-phone", label="_pos:1"),
        # Pointed at the skipped position: falls back to index 0, printed label kept.
        _contact("phone_fax", "orphan-fax", label="FAX"),
        # Points past the draft's positions: falls back to index 0.
        _contact("email_work", "stray@x", label="_pos:5"),
    ])

    assert _db(staged, _labels) == {
        "B-phone": None,
        "orphan-fax": "FAX",
        "stray@x": None,
    }
