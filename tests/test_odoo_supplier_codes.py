"""Odoo client: supplier code sequence, supplier fields, and company scoping (multi-company Odoo)."""
import pytest

from app.config import settings
from app.services import odoo_sync
from app.services.odoo_codes import (
    OdooCodes, OdooDataError, OdooSetupError, customer_id_of, old_code_of,
    supplier_code_in_company_id, supplier_code_of,
)
from tests.fake_odoo import FakeOdoo

NXT, OTHER = 1, 2


@pytest.fixture
def odoo():
    fake = FakeOdoo()
    return fake, OdooCodes(fake, 1, NXT)


# ── Supplier code sequence ───────────────────────────────────────────────────

def test_peek_supplier_code_does_not_consume(odoo):
    _, client = odoo
    assert client.peek_next_supplier_code() == "S100"
    assert client.peek_next_supplier_code() == "S100"


def test_draw_supplier_code_consumes_and_increments(odoo):
    _, client = odoo
    assert client.draw_supplier_code() == "S100"
    assert client.draw_supplier_code() == "S101"
    assert client.peek_next_supplier_code() == "S102"


def test_the_customer_sequence_is_not_touched_by_supplier_draws(odoo):
    _, client = odoo
    client.draw_supplier_code()
    assert client.peek_next_customer_id() == "C0200"


def test_s999_is_skipped_on_draw_and_preview(odoo):
    fake, client = odoo
    fake.data["ir.sequence"][78]["number_next_actual"] = 998
    assert client.draw_supplier_code() == "S998"
    assert client.peek_next_supplier_code() == "S1000"   # S999 is the "Others" folder
    assert client.draw_supplier_code() == "S1000"


def test_missing_supplier_sequence_is_a_setup_error(odoo):
    fake, client = odoo
    del fake.data["ir.sequence"][78]
    with pytest.raises(OdooSetupError):
        client.peek_next_supplier_code()
    with pytest.raises(OdooSetupError):
        client.draw_supplier_code()


def test_a_sequence_of_another_company_is_not_used():
    fake = FakeOdoo()
    fake.data["ir.sequence"][78]["company_id"] = OTHER
    client = OdooCodes(fake, 1, NXT)
    with pytest.raises(OdooSetupError):
        client.draw_supplier_code()


# ── Reading supplier fields ──────────────────────────────────────────────────

def test_find_company_by_supplier_code(odoo):
    fake, client = odoo
    a = fake.add("res.partner", name="某某事務所", is_company=True, x_supplier_code="SA002", company_id=NXT)
    assert client.find_company(supplier_code="sa002")["id"] == a
    assert client.find_company(supplier_code="S555") is None


def test_duplicate_supplier_code_is_reported(odoo):
    fake, client = odoo
    fake.add("res.partner", name="A", is_company=True, x_supplier_code="S058", company_id=NXT)
    fake.add("res.partner", name="B", is_company=True, x_supplier_code="S058", company_id=NXT)
    with pytest.raises(OdooDataError):
        client.find_company(supplier_code="S058")


def test_company_fields_include_the_supplier_fields(odoo):
    fake, client = odoo
    pid = fake.add("res.partner", name="X", is_company=True, company_id=NXT,
                   x_supplier_code="S100", x_supplier_status="active", x_customer_status="potential")
    row = client.read_company(pid)
    assert row["x_supplier_code"] == "S100" and row["x_supplier_status"] == "active"
    assert row["x_customer_status"] == "potential"


# ── Supplier-shaped values in Company ID ─────────────────────────────────────

@pytest.mark.parametrize("value", ["S059", "SA002", "SC070", "SP045", "sa002"])
def test_supplier_codes_in_company_id_are_not_customer_ids_or_old_codes(value):
    rec = {"company_registry": value}
    assert customer_id_of(rec) is None
    assert old_code_of(rec) is None
    assert supplier_code_in_company_id(rec) == value.upper()


@pytest.mark.parametrize("value", ["DS127", "P030", "JFE", "VGJ"])
def test_other_identifiers_stay_old_codes(value):
    rec = {"company_registry": value}
    assert old_code_of(rec) == value
    assert supplier_code_in_company_id(rec) is None


def test_customer_id_is_unchanged():
    rec = {"company_registry": "C0201"}
    assert customer_id_of(rec) == "C0201"
    assert old_code_of(rec) is None and supplier_code_in_company_id(rec) is None


def test_supplier_code_of_reads_the_field_and_ignores_junk():
    assert supplier_code_of({"x_supplier_code": " s100 "}) == "S100"
    assert supplier_code_of({"x_supplier_code": "C0201"}) is None
    assert supplier_code_of({"x_supplier_code": False}) is None
    assert supplier_code_of({}) is None


# ── Company scoping ──────────────────────────────────────────────────────────

def test_a_partner_of_another_company_never_matches(odoo):
    fake, client = odoo
    fake.add("res.partner", name="甜蜜蜜", is_company=True, company_registry="C0200",
             vat="22099131", x_supplier_code="S100", company_id=OTHER)
    assert client.find_company(customer_id="C0200") is None
    assert client.find_company(vat="22099131") is None
    assert client.find_company(supplier_code="S100") is None
    assert client.find_companies_by_name("甜蜜蜜") == []


def test_own_and_shared_partners_match(odoo):
    fake, client = odoo
    own = fake.add("res.partner", name="甲公司", is_company=True, vat="11111111", company_id=NXT)
    shared = fake.add("res.partner", name="乙公司", is_company=True, vat="22222222")  # no company
    assert client.find_company(vat="11111111")["id"] == own
    assert client.find_company(vat="22222222")["id"] == shared
    assert [r["id"] for r in client.find_companies_by_name("乙公司")] == [shared]


def test_scoping_off_sees_every_company():
    fake = FakeOdoo()
    other = fake.add("res.partner", name="丙公司", is_company=True, vat="33333333", company_id=OTHER)
    assert OdooCodes(fake, 1).find_company(vat="33333333")["id"] == other
    assert OdooCodes(fake, 1, 0).find_company(vat="33333333")["id"] == other


def test_created_company_belongs_to_the_app_company(odoo):
    fake, client = odoo
    pid = client.create_company({"name": "新公司"}, ["Supplier: Parts"])
    assert fake.data["res.partner"][pid]["company_id"] == NXT


def test_created_company_keeps_an_explicit_company(odoo):
    fake, client = odoo
    pid = client.create_company({"name": "新公司", "company_id": OTHER})
    assert fake.data["res.partner"][pid]["company_id"] == OTHER


def test_every_call_carries_the_company_context(odoo):
    fake, client = odoo
    client.peek_next_supplier_code()
    client.draw_supplier_code()
    client.find_company(vat="22099131")
    client.project_codes_for("C0200")        # a call that already passes its own context
    client.country_id("TW")                  # a call without any keyword arguments
    assert fake.contexts and all(c and c["allowed_company_ids"] == [NXT] for c in fake.contexts)


def test_existing_context_keys_are_kept(odoo):
    fake, client = odoo
    client.find_company(vat="22099131")      # passes active_test=False
    assert fake.contexts[-1] == {"active_test": False, "allowed_company_ids": [NXT]}


def test_no_context_is_added_when_scoping_is_off():
    fake = FakeOdoo()
    OdooCodes(fake, 1).peek_next_customer_id()
    assert fake.contexts == [None]


# ── Card export (odoo_sync) scoping ──────────────────────────────────────────

def test_export_does_not_reuse_another_companys_partner():
    fake = FakeOdoo()
    theirs = fake.add("res.partner", name="允將餐飲", is_company=True, company_id=OTHER)
    new_id = odoo_sync._find_or_create_company(fake, 1, "允將餐飲")
    assert new_id != theirs
    assert fake.data["res.partner"][new_id]["company_id"] == settings.odoo_company_id


def test_export_reuses_a_shared_partner():
    fake = FakeOdoo()
    shared = fake.add("res.partner", name="允將餐飲", is_company=True)
    assert odoo_sync._find_or_create_company(fake, 1, "允將餐飲") == shared
