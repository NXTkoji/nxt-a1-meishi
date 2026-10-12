"""Unit tests for merge_persons endpoint signature and logic."""
import inspect
import pytest


def test_merge_endpoint_exists():
    from app.routers.v2.persons import merge_persons
    sig = inspect.signature(merge_persons)
    assert 'primary_ext_id' in sig.parameters
    assert 'body' in sig.parameters


def test_merge_request_schema():
    from app.schemas.api import MergeRequest
    req = MergeRequest(source_ids=["abc", "def"])
    assert req.source_ids == ["abc", "def"]


def test_merge_result_schema():
    from app.schemas.api import MergeResult
    import inspect
    sig = inspect.signature(MergeResult)
    assert 'duplicate_contact_count' in sig.parameters


# ── Work contacts keep pointing at their own organization after a merge ──────
#
# A work contact names its organization by list index in its label ("_pos:N",
# no/other label = index 0), and the list is the person's positions by id.
# Merging moves positions from the source onto the primary, so every index
# must be recomputed against the merged list.

import asyncio

from sqlalchemy import select

from app.db.models import ContactDetail, Organization, Person, PersonName, Position
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
    """Merges queue a Google push and a delete; make both no-ops here."""
    from app.routers.v2 import persons
    async def noop(*args, **kwargs):
        pass
    monkeypatch.setattr(persons, "auto_sync_person", noop)
    monkeypatch.setattr(persons, "auto_delete_google_contacts", noop)


async def _add_position(db, person):
    org = Organization()
    db.add(org)
    await db.flush()
    pos = Position(person_id=person.id, org_id=org.id)
    db.add(pos)
    await db.flush()
    return pos.id


async def _two_people_interleaved(db):
    """Primary p1 with org A; source p2 with orgs X and Y. X is created before
    A and Y after it, so the merged id order is X, A, Y — the primary's own
    org moves from index 0 to index 1, not just the source's."""
    p1 = Person(external_id="p1")
    p2 = Person(external_id="p2")
    db.add_all([p1, p2])
    await db.flush()
    for p in (p1, p2):
        db.add(PersonName(person_id=p.id, language="en", full_name=p.external_id,
                          is_current=True, source="manual"))
    x = await _add_position(db, p2)
    a = await _add_position(db, p1)
    y = await _add_position(db, p2)
    db.add_all([
        ContactDetail(person_id=p1.id, detail_type="phone_work", value="A-phone"),
        # Printed label, index 0 on p2 → X, which stays at index 0.
        ContactDetail(person_id=p2.id, detail_type="phone_fax", value="X-fax", label="傳真"),
        ContactDetail(person_id=p2.id, detail_type="email_work", value="Y-mail", label="_pos:1"),
        # Personal contacts carry no org index and must be left alone.
        ContactDetail(person_id=p2.id, detail_type="phone_mobile", value="mobile"),
    ])
    await db.flush()
    return x, a, y


async def _labels(db):
    return {d.value: d.label for d in (await db.execute(select(ContactDetail))).scalars()}


def test_merge_relabels_work_contacts_to_their_org(client_with_test_db, monkeypatch):
    _no_google(monkeypatch)
    _run_db(_two_people_interleaved)

    resp = client_with_test_db.post("/api/v2/persons/p1/merge", json={"source_ids": ["p2"]})
    assert resp.status_code == 200

    # Merged order X(0), A(1), Y(2).
    assert _run_db(_labels) == {
        "A-phone": "_pos:1",
        "X-fax": "傳真",       # index unchanged → printed label kept
        "Y-mail": "_pos:2",
        "mobile": None,
    }
