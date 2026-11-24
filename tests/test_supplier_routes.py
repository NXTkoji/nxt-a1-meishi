"""Supplier and roles API routes, and the statuses the customer flow and card export keep."""
import asyncio

import httpx
import pytest
from sqlalchemy import select

from app.db.models import Organization
from app.main import app
from app.routers.v2 import projects as projects_router
from app.routers.v2 import suppliers as suppliers_router
from app.schemas.api import IssueRequest
from app.services import drive_folders as df
from app.services import issue_flow
from app.services.drive_folders import DriveFolders
from app.services.odoo_codes import OdooCodes
from tests.fake_odoo import FakeOdoo
from tests.test_drive_folders import FakeDrive
from tests.test_project_codes import _make_org

SROOT = "SROOT"
TAX = "22099131"
BODY = {"supplier_name": "新零件供應商", "category": "parts", "tax_id": TAX, "country": "TW"}


@pytest.fixture(autouse=True)
def _roots(monkeypatch):
    monkeypatch.setattr(df.settings, "google_drive_suppliers_folder_id", SROOT)
    monkeypatch.setattr(df.settings, "google_drive_customers_folder_id", "ROOT")


@pytest.fixture
def api(client_with_test_db):
    odoo, fdrive = FakeOdoo(), FakeDrive()
    codes = OdooCodes(odoo, 1, 1)
    parts = fdrive.add("Parts Suppliers", SROOT)

    def new_drive():
        return DriveFolders(httpx.AsyncClient(transport=httpx.MockTransport(fdrive.handler)), "tok")

    async def _odoo():
        return codes

    async def _drive():
        d = new_drive()
        try:
            yield d
        finally:
            await d.aclose()

    async def _drive_opener():
        async def open_drive():
            return new_drive()
        return open_drive

    state = {"odoo_up": True}

    async def _odoo_opener():
        async def open_odoo():
            return codes if state["odoo_up"] else None
        return open_odoo

    app.dependency_overrides[projects_router.get_odoo] = _odoo
    app.dependency_overrides[projects_router.get_drive] = _drive
    app.dependency_overrides[projects_router.get_drive_opener] = _drive_opener
    app.dependency_overrides[suppliers_router.get_odoo_opener] = _odoo_opener

    class Api:
        pass

    a = Api()
    a.client, a.odoo, a.fdrive, a.codes, a.parts, a.state = client_with_test_db, odoo, fdrive, codes, parts, state
    yield a
    for dep in (projects_router.get_odoo, projects_router.get_drive, projects_router.get_drive_opener,
                suppliers_router.get_odoo_opener):
        app.dependency_overrides.pop(dep, None)


def _db(client, fn):
    async def go():
        async with client.session_maker() as db:
            out = await fn(db)
            await db.commit()
            return out
    return asyncio.run(go())


def _org(api, **kw):
    async def make(db):
        oid = await _make_org(db, kw.pop("name", "某組織"), **kw)
        return (await db.get(Organization, oid)).external_id
    return _db(api.client, make)


def _row(api, ext_id):
    return _db(api.client, lambda db: db.scalar(select(Organization).where(Organization.external_id == ext_id)))


# ── supplier propose / commit / retry ─────────────────────────────────────────

def test_api_propose_then_commit(api):
    r = api.client.post("/api/v2/suppliers/propose", json=BODY)
    assert r.status_code == 200
    assert r.json()["suggested_supplier_code"] == "S100" and r.json()["category_folder"] == "Parts Suppliers"
    assert api.odoo.rows("res.partner") == []

    r = api.client.post("/api/v2/suppliers/commit", json=BODY)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "done" and body["kind"] == "supplier" and body["supplier_code"] == "S100"
    assert body["supplier_drive_url"].startswith("https://drive.google.com/drive/folders/")
    assert [f["name"] for f in api.fdrive.files if api.parts in f["parents"]] == ["S100 新零件供應商"]


def test_api_bad_input_is_422(api):
    assert api.client.post("/api/v2/suppliers/propose", json={**BODY, "category": "japan"}).status_code == 422
    assert api.client.post("/api/v2/suppliers/commit", json={**BODY, "tax_id": "22099132"}).status_code == 422
    assert api.client.post("/api/v2/suppliers/propose", json={**BODY, "supplier_name": " "}).status_code == 422


def test_the_projects_retry_route_resumes_a_supplier_run(api, monkeypatch):
    real = DriveFolders.ensure_supplier_folder
    calls = {"n": 0}

    async def flaky(self, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise df.DriveError("Drive is down")
        return await real(self, *a, **kw)

    monkeypatch.setattr(DriveFolders, "ensure_supplier_folder", flaky)
    failed = api.client.post("/api/v2/suppliers/commit", json=BODY).json()
    assert failed["status"] == "failed" and failed["failed_step"] == "drive_supplier"
    done = api.client.post(f"/api/v2/projects/runs/{failed['run_id']}/retry").json()
    assert done["status"] == "done" and done["kind"] == "supplier" and done["supplier_code"] == "S100"
    assert api.codes.peek_next_supplier_code() == "S101"
    assert len(api.odoo.rows("res.partner")) == 1


def test_the_retry_route_still_handles_project_runs(api):
    api.fdrive.add("NXT Customers", "ROOT")
    r = api.client.post("/api/v2/projects/commit", json={
        "customer_name": "甜蜜蜜台北店", "tax_id": TAX, "product_line": "S", "site_name": "台北店"})
    assert r.status_code == 200 and r.json()["status"] == "done"
    again = api.client.post(f"/api/v2/projects/runs/{r.json()['run_id']}/retry")
    assert again.status_code == 200 and again.json()["customer_code"] == "C0200"
    assert api.client.post("/api/v2/projects/runs/nope/retry").status_code == 422


# ── roles route ───────────────────────────────────────────────────────────────

def test_set_potential_roles_without_an_odoo_company(api):
    ext = _org(api)
    r = api.client.put(f"/api/v2/organizations/{ext}/roles",
                       json={"customer_status": "potential", "supplier_status": "potential",
                             "supplier_category": "marketing"})
    assert r.status_code == 200
    out = r.json()
    assert out["customer_status"] == "potential" and out["supplier_status"] == "potential"
    assert out["supplier_category"] == "marketing" and out["supplier_code"] is None
    assert out["is_nxt_customer"] is True
    org = _row(api, ext)
    assert org.customer_status == "potential" and org.supplier_status == "potential"
    assert org.supplier_code is None and org.customer_code is None      # no code for potential
    assert api.odoo.rows("res.partner") == []                            # and Odoo untouched
    assert api.codes.peek_next_supplier_code() == "S100"


def test_none_clears_a_status_and_the_derived_switch(api):
    ext = _org(api)
    api.client.put(f"/api/v2/organizations/{ext}/roles", json={"customer_status": "potential"})
    r = api.client.put(f"/api/v2/organizations/{ext}/roles", json={"customer_status": "none"})
    assert r.json()["customer_status"] is None and r.json()["is_nxt_customer"] is False


def test_only_the_fields_sent_change(api):
    ext = _org(api)
    api.client.put(f"/api/v2/organizations/{ext}/roles", json={"supplier_status": "potential"})
    r = api.client.put(f"/api/v2/organizations/{ext}/roles", json={"customer_status": "potential"})
    assert r.json()["supplier_status"] == "potential" and r.json()["customer_status"] == "potential"


def test_status_goes_to_the_odoo_company_too(api):
    pid = api.odoo.add("res.partner", name="某組織", is_company=True, company_id=1)
    ext = _org(api)
    _db(api.client, lambda db: _link(db, ext, pid))
    r = api.client.put(f"/api/v2/organizations/{ext}/roles",
                       json={"supplier_status": "potential", "supplier_category": "parts"})
    assert r.status_code == 200
    co = api.odoo.data["res.partner"][pid]
    assert co["x_supplier_status"] == "potential"
    tags = {api.odoo.data["res.partner.category"][i]["name"] for i in co["category_id"]}
    assert tags == {"Supplier: Parts"}
    assert "x_supplier_code" not in co                                   # still no code


def test_nothing_changes_when_odoo_is_down(api):
    pid = api.odoo.add("res.partner", name="某組織", is_company=True, company_id=1)
    ext = _org(api)
    _db(api.client, lambda db: _link(db, ext, pid))
    api.state["odoo_up"] = False
    r = api.client.put(f"/api/v2/organizations/{ext}/roles", json={"supplier_status": "potential"})
    assert r.status_code == 503
    assert _row(api, ext).supplier_status is None


@pytest.mark.parametrize("body,status", [
    ({"customer_status": "active"}, 422),          # a code is needed first
    ({"supplier_status": "active"}, 422),
    ({"supplier_status": "inactive"}, 409),        # nothing to deactivate
    ({"customer_status": "pending"}, 422),
    ({"supplier_category": "japan"}, 422),
])
def test_roles_route_refuses_what_needs_a_code_or_is_invalid(api, body, status):
    ext = _org(api)
    assert api.client.put(f"/api/v2/organizations/{ext}/roles", json=body).status_code == status


def test_a_role_with_a_code_can_only_become_inactive_and_back(api):
    ext = _org(api, code="C0200")
    _db(api.client, lambda db: _set_status(db, ext, customer_status="active"))
    c = api.client
    assert c.put(f"/api/v2/organizations/{ext}/roles", json={"customer_status": "potential"}).status_code == 409
    assert c.put(f"/api/v2/organizations/{ext}/roles", json={"customer_status": "none"}).status_code == 409
    r = c.put(f"/api/v2/organizations/{ext}/roles", json={"customer_status": "inactive"})
    assert r.status_code == 200 and r.json()["customer_status"] == "inactive"
    assert r.json()["is_nxt_customer"] is True                          # derived: status is not none
    r = c.put(f"/api/v2/organizations/{ext}/roles", json={"customer_status": "active"})
    assert r.status_code == 200 and r.json()["customer_status"] == "active"


def test_the_category_of_a_coded_supplier_cannot_change(api):
    ext = _org(api)
    api.client.post("/api/v2/suppliers/commit", json={**BODY, "org_ext_id": ext, "supplier_name": None})
    org = _row(api, ext)
    assert org.supplier_status == "active" and org.supplier_category == "parts"
    assert api.client.put(f"/api/v2/organizations/{ext}/roles",
                          json={"supplier_category": "marketing"}).status_code == 409
    assert api.client.put(f"/api/v2/organizations/{ext}/roles",
                          json={"supplier_category": "parts"}).status_code == 200
    assert api.client.put(f"/api/v2/organizations/{ext}/roles",
                          json={"supplier_status": "potential"}).status_code == 409


def test_unknown_organization_is_404(api):
    assert api.client.put("/api/v2/organizations/nope/roles", json={}).status_code == 404


def test_organization_endpoints_expose_the_roles(api):
    ext = _org(api)
    api.client.post("/api/v2/suppliers/commit", json={**BODY, "org_ext_id": ext, "supplier_name": None})
    one = api.client.get(f"/api/v2/organizations/{ext}").json()
    assert one["supplier_code"] == "S100" and one["supplier_status"] == "active"
    assert one["supplier_category"] == "parts" and one["supplier_drive_url"]
    listed = api.client.get("/api/v2/organizations").json()
    assert any(i["supplier_code"] == "S100" for i in listed)


# ── the customer side keeps its status ────────────────────────────────────────

def test_the_nxt_customer_switch_sets_a_potential_customer(api):
    ext = _org(api)
    r = api.client.put(f"/api/v2/organizations/{ext}/nxt-customer", json={"enabled": True})
    assert r.json()["customer_status"] == "potential" and r.json()["is_nxt_customer"] is True
    r = api.client.put(f"/api/v2/organizations/{ext}/nxt-customer", json={"enabled": False})
    assert r.json()["customer_status"] is None and r.json()["is_nxt_customer"] is False


def test_issuing_a_customer_id_makes_the_customer_active(api):
    api.fdrive.add("NXT Customers", "ROOT")
    out = api.client.post("/api/v2/projects/commit", json={
        "customer_name": "甜蜜蜜台北店", "tax_id": TAX, "product_line": "S", "site_name": "台北店"}).json()
    assert out["status"] == "done"
    org = _db(api.client, lambda db: db.scalar(select(Organization).where(Organization.customer_code == "C0200")))
    assert org.customer_status == "active" and org.is_nxt_customer is True
    (company,) = api.odoo.rows("res.partner")
    assert company["x_customer_status"] == "active" and company["company_id"] == 1
    assert "x_supplier_status" not in company


async def _link(db, ext, pid):
    org = await db.scalar(select(Organization).where(Organization.external_id == ext))
    org.odoo_partner_id = pid


async def _set_status(db, ext, **vals):
    org = await db.scalar(select(Organization).where(Organization.external_id == ext))
    for k, v in vals.items():
        setattr(org, k, v)
