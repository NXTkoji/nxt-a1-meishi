"""Supplier codes: validation, categories, tax IDs by country and matching by supplier code."""
import asyncio

import pytest

from app.db.models import Organization, OrganizationName
from app.services import project_codes as pc
from app.services import supplier_codes as sc


# ── Code shapes ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("code", ["S059", "SA002", "SC070", "SF003", "SI001", "SM013", "SP045",
                                  "S100", "S1000", "s100"])
def test_supplier_code_shapes_accepted(code):
    assert sc.is_supplier_code(code)
    assert sc.validate_supplier_code(code) == code.upper()


@pytest.mark.parametrize("code", ["C0201", "C0201S01", "S12", "SX002", "DS164", "VGJ", "", None, "S"])
def test_other_shapes_are_not_supplier_codes(code):
    assert not sc.is_supplier_code(code)
    with pytest.raises(pc.ValidationError):
        sc.validate_supplier_code(code or "")


def test_s999_is_never_a_supplier_code_or_issued():
    assert sc.is_supplier_code("S999")  # the shape matches, which is why the guard exists
    with pytest.raises(pc.ValidationError):
        sc.validate_supplier_code("S999")
    with pytest.raises(pc.ValidationError):
        sc.format_supplier_code(999)


def test_format_and_number():
    assert sc.format_supplier_code(100) == "S100"
    assert sc.format_supplier_code(1000) == "S1000"
    with pytest.raises(pc.ValidationError):
        sc.format_supplier_code(99)
    assert sc.supplier_number("S100") == 100
    assert sc.supplier_number("SA002") is None
    assert sc.supplier_number("C0201") is None


# ── Status and category ──────────────────────────────────────────────────────

def test_role_status():
    assert sc.validate_role_status(None) is None
    assert sc.validate_role_status("") is None
    assert sc.validate_role_status("none") is None
    assert sc.validate_role_status(" Active ") == "active"
    with pytest.raises(pc.ValidationError):
        sc.validate_role_status("pending")


def test_categories_cover_the_odoo_tags_and_folders():
    assert [c.key for c in sc.CATEGORIES] == [
        "parts", "administration", "construction", "forwarding", "inspection",
        "marketing", "packaging", "other"]
    assert sc.category("Packaging").drive_folder == "Package Suppliers"
    assert sc.category("other").drive_folder == "S999 Others"
    assert sc.category("parts").odoo_tag == "Supplier: Parts"
    assert sc.category_for_tag("Supplier: Marketing").key == "marketing"
    assert sc.category_for_tag("Reseller") is None
    with pytest.raises(pc.ValidationError):
        sc.category("japan")
    with pytest.raises(pc.ValidationError):
        sc.category("")


# ── Tax ID by country ────────────────────────────────────────────────────────

def test_blank_tax_id_is_none_for_any_country():
    assert sc.validate_tax_id_for_country("", "TW") is None
    assert sc.validate_tax_id_for_country("  ", None) is None
    assert sc.validate_tax_id_for_country(None, "JP") is None


@pytest.mark.parametrize("country", ["TW", "tw", "Taiwan", "台灣"])
def test_taiwan_uses_the_checksum(country):
    assert sc.validate_tax_id_for_country("2209-9131", country) == "22099131"
    with pytest.raises(pc.ValidationError):
        sc.validate_tax_id_for_country("22099132", country)


@pytest.mark.parametrize("value", ["T1234567890123", "1234567890123", "t 1234-5678-90123"])
def test_japan_accepts_13_digits_with_or_without_t(value):
    assert sc.validate_tax_id_for_country(value, "JP") == "T1234567890123"
    assert sc.validate_tax_id_for_country(value, "日本") == "T1234567890123"


@pytest.mark.parametrize("value", ["T123", "12345678901234", "ABCDEFGHIJKLM"])
def test_japan_rejects_other_shapes(value):
    with pytest.raises(pc.ValidationError):
        sc.validate_tax_id_for_country(value, "JP")


def test_other_countries_keep_free_text():
    assert sc.validate_tax_id_for_country(" SE556677889901 ", "SE") == "SE556677889901"
    assert sc.validate_tax_id_for_country("GB 123 4567 89", None) == "GB 123 4567 89"


# ── Matching by supplier code ────────────────────────────────────────────────

def _run(client, fn):
    async def go():
        async with client.session_maker() as db:
            result = await fn(db)
            await db.commit()
            return result
    return asyncio.run(go())


def test_match_by_supplier_code(client_with_test_db):
    async def go(db):
        org = Organization(supplier_code="SA002", customer_code=None)
        db.add(org)
        await db.flush()
        db.add(OrganizationName(org_id=org.id, language="zh", name="某某事務所"))
        other = Organization(supplier_code="S100")
        db.add(other)
        await db.flush()
        db.add(OrganizationName(org_id=other.id, language="zh", name="別家"))
        await db.flush()
        found = await pc.match_organization(db, supplier_code="sa002", name="完全不同的名字")
        return org.id, found
    oid, found = _run(client_with_test_db, go)
    assert [c.org_id for c in found] == [oid]
    assert found[0].reason == "supplier_code" and found[0].auto
    assert found[0].supplier_code == "SA002"


def test_unknown_supplier_code_matches_nothing(client_with_test_db):
    async def go(db):
        return await pc.match_organization(db, supplier_code="S555")
    assert _run(client_with_test_db, go) == []
