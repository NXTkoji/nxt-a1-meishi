"""Memo fields: one per person (Person.notes) and one shared per organization
(Organization.notes). The person memo also goes to the Google contact's Notes."""
import asyncio

from app.db.models import Organization, OrganizationName, Person, PersonName, Position
from app.db.session import get_db
from app.main import app
from app.models.card import Card as LegacyCard, Person as LegacyPerson
from app.services.google_contacts import _build_person_body


def _run_db(fn):
    async def _inner():
        async for db in app.dependency_overrides[get_db]():
            result = await fn(db)
            await db.commit()
            return result
    return asyncio.run(_inner())


def _no_google(monkeypatch):
    """Person edits queue a Google push; make it a no-op here."""
    from app.routers.v2 import persons
    async def noop(*args, **kwargs):
        pass
    monkeypatch.setattr(persons, "auto_sync_person", noop)


async def _two_people_at_one_org(db):
    """p1 and p2 both work at the same shared Organization."""
    org = Organization()
    db.add(org)
    await db.flush()
    db.add(OrganizationName(org_id=org.id, language="en", name="Shared Co", is_current=True))
    position_ids = []
    for ext_id in ("p1", "p2"):
        person = Person(external_id=ext_id)
        db.add(person)
        await db.flush()
        db.add(PersonName(person_id=person.id, language="en", full_name=ext_id,
                          is_current=True, source="manual"))
        pos = Position(person_id=person.id, org_id=org.id)
        db.add(pos)
        await db.flush()
        position_ids.append(pos.id)
    return position_ids


# ── Person memo ──────────────────────────────────────────────────────────────

def test_person_memo_is_saved_and_cleared(client_with_test_db, monkeypatch):
    _no_google(monkeypatch)
    _run_db(_two_people_at_one_org)

    resp = client_with_test_db.patch("/api/v2/persons/p1", json={"notes": "Met at Rotary\nlikes golf"})
    assert resp.status_code == 200
    assert resp.json()["notes"] == "Met at Rotary\nlikes golf"
    assert client_with_test_db.get("/api/v2/persons/p1").json()["notes"] == "Met at Rotary\nlikes golf"

    # Empty string clears it, like birthday.
    resp = client_with_test_db.patch("/api/v2/persons/p1", json={"notes": ""})
    assert resp.json()["notes"] is None


def test_person_memo_patch_leaves_birthday_alone(client_with_test_db, monkeypatch):
    _no_google(monkeypatch)
    _run_db(_two_people_at_one_org)
    client_with_test_db.patch("/api/v2/persons/p1", json={"birthday": "--05-20"})

    client_with_test_db.patch("/api/v2/persons/p1", json={"notes": "memo"})

    assert client_with_test_db.get("/api/v2/persons/p1").json()["birthday"] == "--05-20"


# ── Organization memo (shared) ───────────────────────────────────────────────

def test_org_memo_is_shared_by_everyone_at_the_org(client_with_test_db, monkeypatch):
    _no_google(monkeypatch)
    p1_pos, _ = _run_db(_two_people_at_one_org)

    resp = client_with_test_db.patch(
        f"/api/v2/persons/p1/positions/{p1_pos}/org-notes", json={"notes": "Client since 2024"})
    assert resp.status_code == 200

    # Visible on both people's position for that org.
    for ext_id in ("p1", "p2"):
        positions = client_with_test_db.get(f"/api/v2/persons/{ext_id}").json()["positions"]
        assert positions[0]["org_notes"] == "Client since 2024"

    client_with_test_db.patch(f"/api/v2/persons/p1/positions/{p1_pos}/org-notes", json={"notes": ""})
    positions = client_with_test_db.get("/api/v2/persons/p2").json()["positions"]
    assert positions[0]["org_notes"] is None


def test_org_memo_rejects_another_persons_position(client_with_test_db, monkeypatch):
    _no_google(monkeypatch)
    _, p2_pos = _run_db(_two_people_at_one_org)

    resp = client_with_test_db.patch(
        f"/api/v2/persons/p1/positions/{p2_pos}/org-notes", json={"notes": "x"})
    assert resp.status_code == 404


# ── Google Contacts ──────────────────────────────────────────────────────────

def test_google_notes_include_person_memo_after_card_notes():
    card = LegacyCard(person=LegacyPerson(notes="Person memo"), notes="Card note")
    body = _build_person_body(card)
    assert body["biographies"][0]["value"] == "Card note\nPerson memo"


def test_google_notes_with_only_person_memo():
    body = _build_person_body(LegacyCard(person=LegacyPerson(notes="Person memo")))
    assert body["biographies"][0]["value"] == "Person memo"
