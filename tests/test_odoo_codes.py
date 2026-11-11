"""Odoo client for project codes, and the company lookup used by the card export."""
import pytest

from app.services import odoo_codes, odoo_sync
from app.services.odoo_codes import OdooCodes, OdooDataError, OdooSetupError
from tests.fake_odoo import FakeOdoo


@pytest.fixture
def odoo():
    fake = FakeOdoo()
    return fake, OdooCodes(fake, 1)


# ── Customer ID sequence ──────────────────────────────────────────────────────

def test_peek_does_not_consume(odoo):
    fake, client = odoo
    assert client.peek_next_customer_id() == "C0200"
    assert client.peek_next_customer_id() == "C0200"


def test_draw_consumes_and_increments(odoo):
    _, client = odoo
    assert client.draw_customer_id() == "C0200"
    assert client.draw_customer_id() == "C0201"
    assert client.peek_next_customer_id() == "C0202"


def test_missing_sequence_is_a_setup_error():
    fake = FakeOdoo()
    fake.data["ir.sequence"].clear()
    client = OdooCodes(fake, 1)
    with pytest.raises(OdooSetupError):
        client.peek_next_customer_id()
    with pytest.raises(OdooSetupError):
        client.draw_customer_id()


# ── Company partners ──────────────────────────────────────────────────────────

def test_find_company_by_ref_then_vat(odoo):
    fake, client = odoo
    a = fake.add("res.partner", name="甜蜜蜜", is_company=True, ref="C0200", vat="22099131")
    fake.add("res.partner", name="某人", is_company=False, ref="C0200")  # a person, ignored
    assert client.find_company(ref="C0200")["id"] == a
    assert client.find_company(vat="22099131")["id"] == a
    assert client.find_company(ref="C9999", vat="22099131")["id"] == a  # falls back to vat
    assert client.find_company(ref="C9999") is None
    assert client.find_company() is None


def test_find_company_accepts_tw_prefixed_vat(odoo):
    fake, client = odoo
    a = fake.add("res.partner", name="X", is_company=True, vat="TW22099131")
    assert client.find_company(vat="22099131")["id"] == a


def test_duplicate_ref_is_reported(odoo):
    fake, client = odoo
    fake.add("res.partner", name="A", is_company=True, ref="C0200")
    fake.add("res.partner", name="B", is_company=True, ref="C0200")
    with pytest.raises(OdooDataError):
        client.find_company(ref="C0200")


def test_create_company_adds_tags_once(odoo):
    fake, client = odoo
    pid = client.create_company({"name": "甜蜜蜜", "ref": "C0200", "vat": "22099131"},
                                tag_names=[odoo_codes.TAG_CUSTOMER, odoo_codes.TAG_RESELLER])
    client.create_company({"name": "別家", "ref": "C0201"}, tag_names=[odoo_codes.TAG_CUSTOMER])
    names = {r["name"] for r in fake.rows("res.partner.category")}
    assert names == {"NXT Customer", "NXT Reseller"}  # not duplicated
    partner = fake.data["res.partner"][pid]
    assert partner["is_company"] is True and len(partner["category_id"]) == 2


def test_update_company_adds_tag_without_dropping_others(odoo):
    fake, client = odoo
    pid = client.create_company({"name": "X", "ref": "C0200"}, tag_names=[odoo_codes.TAG_CUSTOMER])
    client.update_company(pid, {"x_drive_url": "https://drive/x"}, add_tag_names=[odoo_codes.TAG_END_USER])
    partner = fake.data["res.partner"][pid]
    assert partner["x_drive_url"] == "https://drive/x"
    assert len(partner["category_id"]) == 2


# ── Projects ──────────────────────────────────────────────────────────────────

def test_project_codes_for_customer(odoo):
    fake, client = odoo
    fake.add("project.project", name="a", x_project_code="C0200-DS01")
    fake.add("project.project", name="b", x_project_code="C0200-DJ01")
    fake.add("project.project", name="c", x_project_code="C0201-DS01")
    fake.add("project.project", name="d")  # no code
    assert sorted(client.project_codes_for("C0200")) == ["C0200-DJ01", "C0200-DS01"]


def test_find_and_create_project(odoo):
    fake, client = odoo
    assert client.find_project("C0200-DS01") is None
    pid = client.create_project({"name": "C0200-DS01 台北店", "x_project_code": "C0200-DS01",
                                 "x_product_line": "DS"})
    assert client.find_project("C0200-DS01")["id"] == pid


# ── Card export: company lookup (the duplicate-company fix) ───────────────────

def _find(fake, name):
    return odoo_sync._find_or_create_company(fake, 1, name)


def test_export_reuses_company_despite_legal_form(odoo):
    fake, _ = odoo
    existing = fake.add("res.partner", name="允將餐飲股份有限公司", is_company=True)
    assert _find(fake, "允將餐飲") == existing
    assert len(fake.rows("res.partner")) == 1


def test_export_prefers_the_company_with_a_customer_id(odoo):
    fake, _ = odoo
    fake.add("res.partner", name="允將餐飲", is_company=True)
    customer = fake.add("res.partner", name="允將餐飲有限公司", is_company=True, ref="C0200")
    assert _find(fake, "允將餐飲") == customer


def test_export_keeps_the_exact_name_behaviour_with_spaces(odoo):
    fake, _ = odoo
    existing = fake.add("res.partner", name="ABC Trading", is_company=True)
    assert _find(fake, "ABC Trading") == existing


def test_export_does_not_merge_different_companies(odoo):
    fake, _ = odoo
    fake.add("res.partner", name="允將餐飲集團", is_company=True)
    new_id = _find(fake, "允將餐飲")
    assert len(fake.rows("res.partner")) == 2 and new_id == 2


def test_export_creates_when_missing_and_ignores_people(odoo):
    fake, _ = odoo
    fake.add("res.partner", name="山崎金型", is_company=False)
    new_id = _find(fake, "山崎金型")
    assert fake.data["res.partner"][new_id]["is_company"] is True
    assert _find(fake, "") is None
