"""Project code issuing: validation, code formats and organization matching."""
import asyncio

import pytest

from app.db.models import (
    ContactDetail, Organization, OrganizationName, Person, PersonName, Position,
)
from app.services import project_codes as pc


# ── 統一編號 ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["22099131", "2209-9131", " 22099131 "])
def test_valid_tax_id(value):
    assert pc.validate_tax_id(value) == "22099131"


@pytest.mark.parametrize("value", ["", "1234567", "123456789", "22099132", "abcdefgh"])
def test_invalid_tax_id(value):
    with pytest.raises(pc.ValidationError):
        pc.validate_tax_id(value)


def test_tax_id_seventh_digit_seven_rule():
    # 7th digit is 7 (7 x 4 = 28 can count as 10 or 1). 10458574 has a weighted
    # total of 29 (4 mod 5), so only the alternative reading makes it valid.
    assert pc.validate_tax_id("10458574") == "10458574"
    # Same shape, total 28 (3 mod 5): invalid under either reading.
    with pytest.raises(pc.ValidationError):
        pc.validate_tax_id("10458573")
    # Without a 7 in the 7th digit the alternative reading does not apply.
    with pytest.raises(pc.ValidationError):
        pc.validate_tax_id("10458544")


# ── Phone normalization ───────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("+886-2-2345-6789", "0223456789"),
    ("+886 (0)2 2345 6789", "0223456789"),
    ("00886 912 345 678", "0912345678"),
    ("0912-345-678", "0912345678"),
    ("(02) 2345-6789", "0223456789"),
    ("", ""),
])
def test_normalize_phone_taiwan(raw, expected):
    assert pc.normalize_phone(raw) == expected


# ── Code formats ──────────────────────────────────────────────────────────────

def test_format_and_parse_project_code():
    assert pc.format_project_code("C0200", "ds", 1) == "C0200-DS01"
    assert pc.format_project_code("C0200", "DJ", 12) == "C0200-DJ12"
    assert pc.parse_project_code("C0200-DS01") == ("C0200", "DS", 1)
    assert pc.parse_project_code("DS176") is None
    assert pc.parse_project_code("P065S50") is None


def test_format_rejects_bad_input():
    with pytest.raises(pc.ValidationError):
        pc.format_project_code("DS176", "DS", 1)
    with pytest.raises(pc.ValidationError):
        pc.format_project_code("C0200", "P", 1)


def test_next_project_number_is_per_customer_and_line():
    existing = ["C0200-DS01", "C0200-DS03", "C0200-DJ01", "C0201-DS09", "junk"]
    assert pc.next_project_number(existing, "C0200", "DS") == 4
    assert pc.next_project_number(existing, "C0200", "DJ") == 2
    assert pc.next_project_number(existing, "C0202", "DS") == 1
    assert pc.next_project_number([], "C0200", "DS") == 1


# ── Organization matching ─────────────────────────────────────────────────────

def _run(client, fn):
    async def go():
        async with client.session_maker() as db:
            result = await fn(db)
            await db.commit()
            return result
    return asyncio.run(go())


async def _make_org(db, name, *, tax_id=None, code=None, phones=()):
    org = Organization(tax_id=tax_id, customer_code=code)
    db.add(org)
    await db.flush()
    db.add(OrganizationName(org_id=org.id, language="zh", name=name))
    if phones:
        person = Person()
        db.add(person)
        await db.flush()
        db.add(PersonName(person_id=person.id, language="zh", full_name=name + "窗口", name_type="primary"))
        db.add(Position(person_id=person.id, org_id=org.id))
        for ph in phones:
            db.add(ContactDetail(person_id=person.id, detail_type="phone_work", value=ph))
    await db.flush()
    return org.id


def test_match_by_tax_id(client_with_test_db):
    async def go(db):
        oid = await _make_org(db, "甜蜜蜜", tax_id="22099131", code="C0200")
        found = await pc.match_organization(db, tax_id="22099131", name="完全不同")
        return oid, found
    oid, found = _run(client_with_test_db, go)
    assert [c.org_id for c in found] == [oid]
    assert found[0].reason == "tax_id" and found[0].auto
    assert found[0].customer_code == "C0200"


def test_match_by_phone_across_formats(client_with_test_db):
    async def go(db):
        oid = await _make_org(db, "義美食品", phones=["(02) 2345-6789"])
        return oid, await pc.match_organization(db, phone="+886 2 2345 6789")
    oid, found = _run(client_with_test_db, go)
    assert found[0].org_id == oid and found[0].reason == "phone" and found[0].auto


def test_name_matches_are_candidates_only(client_with_test_db):
    async def go(db):
        exact = await _make_org(db, "允將餐飲股份有限公司")  # same name, legal form differs
        fuzzy = await _make_org(db, "允將餐飲集團")
        other = await _make_org(db, "山崎金型")
        return exact, fuzzy, other, await pc.match_organization(db, name="允將餐飲")
    exact, fuzzy, other, found = _run(client_with_test_db, go)
    by_id = {c.org_id: c for c in found}
    assert by_id[exact].reason == "name_exact"
    assert by_id[fuzzy].reason == "name_fuzzy"
    assert other not in by_id
    assert not by_id[exact].auto and not by_id[fuzzy].auto
    assert found[0].org_id == exact  # strongest first


def test_normalize_org_name_ignores_legal_form_and_width():
    assert pc.normalize_org_name("允將餐飲股份有限公司") == pc.normalize_org_name("允將餐飲")
    assert pc.normalize_org_name("ＡＢＣ Co., Ltd.") == pc.normalize_org_name("abc")


def test_one_candidate_per_org_keeps_strongest_reason(client_with_test_db):
    async def go(db):
        oid = await _make_org(db, "頂期", tax_id="22099131", phones=["0223456789"])
        return oid, await pc.match_organization(
            db, tax_id="22099131", phone="0223456789", name="頂期")
    oid, found = _run(client_with_test_db, go)
    assert len(found) == 1 and found[0].reason == "tax_id"


def test_no_match(client_with_test_db):
    async def go(db):
        await _make_org(db, "山崎金型", tax_id="22099131")
        return await pc.match_organization(db, tax_id="12345675", phone="0900000000", name="XYZ")
    assert _run(client_with_test_db, go) == []
