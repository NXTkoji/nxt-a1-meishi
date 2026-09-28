"""Deleting a person's name variants and organizations (positions)."""
import asyncio

from sqlalchemy import func, select

from app.db.models import ContactDetail, Organization, Person, PersonName, Position, PositionDetail
from app.db.session import get_db
from app.main import app


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
    async def noop(person_id):
        pass
    monkeypatch.setattr(persons, "auto_sync_person", noop)


async def _person_with_names(db, full_names):
    person = Person(external_id="p1")
    db.add(person)
    await db.flush()
    names = [PersonName(person_id=person.id, language="en", full_name=n, is_current=True, source="manual")
             for n in full_names]
    db.add_all(names)
    await db.flush()
    return [n.id for n in names]


def test_delete_name(client_with_test_db, monkeypatch):
    _no_google(monkeypatch)
    ids = _run_db(lambda db: _person_with_names(db, ["Sue", "Sue Chen"]))

    resp = client_with_test_db.delete(f"/api/v2/persons/p1/names/{ids[1]}")
    assert resp.status_code == 204

    remaining = _run_db(lambda db: _names(db))
    assert remaining == ["Sue"]


async def _names(db):
    return [n.full_name for n in (await db.execute(select(PersonName).order_by(PersonName.id))).scalars()]


def test_cannot_delete_only_name(client_with_test_db, monkeypatch):
    _no_google(monkeypatch)
    ids = _run_db(lambda db: _person_with_names(db, ["Sue"]))

    resp = client_with_test_db.delete(f"/api/v2/persons/p1/names/{ids[0]}")
    assert resp.status_code == 409
    assert _run_db(lambda db: _names(db)) == ["Sue"]


def test_delete_name_of_other_person_is_404(client_with_test_db, monkeypatch):
    _no_google(monkeypatch)
    ids = _run_db(lambda db: _person_with_names(db, ["Sue", "Sue Chen"]))

    async def _other(db):
        db.add(Person(external_id="p2"))
    _run_db(_other)

    resp = client_with_test_db.delete(f"/api/v2/persons/p2/names/{ids[1]}")
    assert resp.status_code == 404


async def _person_with_three_orgs(db):
    """Orgs A, B, C at indexes 0, 1, 2, each with work contacts, plus a
    personal contact that must never be touched."""
    person = Person(external_id="p1")
    db.add(person)
    await db.flush()
    position_ids = []
    for name in ["A", "B", "C"]:
        org = Organization()
        db.add(org)
        await db.flush()
        pos = Position(person_id=person.id, org_id=org.id)
        db.add(pos)
        await db.flush()
        db.add(PositionDetail(position_id=pos.id, language="en", title=f"title {name}"))
        position_ids.append(pos.id)
    db.add_all([
        # Index 0: no label, or a printed label that isn't "_pos:N".
        ContactDetail(person_id=person.id, detail_type="url_website", value="a.example", label="官方網站"),
        ContactDetail(person_id=person.id, detail_type="phone_work", value="A-phone"),
        ContactDetail(person_id=person.id, detail_type="email_work", value="b@x", label="_pos:1"),
        ContactDetail(person_id=person.id, detail_type="phone_work", value="C-phone", label="_pos:2"),
        ContactDetail(person_id=person.id, detail_type="phone_mobile", value="mobile"),
    ])
    await db.flush()
    return position_ids


async def _state(db):
    positions = (await db.execute(select(Position.id).order_by(Position.id))).scalars().all()
    contacts = {d.value: d.label for d in (await db.execute(select(ContactDetail))).scalars()}
    details = (await db.execute(select(PositionDetail.title).order_by(PositionDetail.id))).scalars().all()
    orgs = await db.scalar(select(func.count()).select_from(Organization))
    return positions, contacts, details, orgs


def test_delete_first_org_removes_its_contacts_and_shifts_later_ones(client_with_test_db, monkeypatch):
    _no_google(monkeypatch)
    a, b, c = _run_db(_person_with_three_orgs)

    resp = client_with_test_db.delete(f"/api/v2/persons/p1/positions/{a}")
    assert resp.status_code == 204

    positions, contacts, details, orgs = _run_db(_state)
    assert positions == [b, c]
    assert details == ["title B", "title C"]
    # A's contacts are gone; B's move to index 0 (no label), C's to index 1.
    assert contacts == {"b@x": None, "C-phone": "_pos:1", "mobile": None}
    assert orgs == 3  # shared Organization rows stay


def test_delete_middle_org_leaves_earlier_contacts_alone(client_with_test_db, monkeypatch):
    _no_google(monkeypatch)
    a, b, c = _run_db(_person_with_three_orgs)

    resp = client_with_test_db.delete(f"/api/v2/persons/p1/positions/{b}")
    assert resp.status_code == 204

    positions, contacts, _, _ = _run_db(_state)
    assert positions == [a, c]
    assert contacts == {"a.example": "官方網站", "A-phone": None, "C-phone": "_pos:1", "mobile": None}


def test_delete_position_queues_google_sync(client_with_test_db, monkeypatch):
    from app.routers.v2 import persons
    synced = []
    async def record(person_id):
        synced.append(person_id)
    monkeypatch.setattr(persons, "auto_sync_person", record)
    a, _, _ = _run_db(_person_with_three_orgs)

    client_with_test_db.delete(f"/api/v2/persons/p1/positions/{a}")
    assert len(synced) == 1
