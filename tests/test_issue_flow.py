"""Propose / commit / retry for NXT project codes, with fake Odoo and fake Drive."""
import asyncio

import httpx
import pytest
from sqlalchemy import select

from app.db.models import (
    Card, ContactDetail, Organization, OrganizationName, Person, PersonName,
    Position, Project,
)
from app.main import app
from app.routers.v2 import projects as projects_router
from app.schemas.api import IssueRequest
from app.services import drive_folders as df
from app.services import issue_flow
from app.services import odoo_sync
from app.services import project_codes as pc
from app.services.drive_folders import DriveError, DriveFolders, PROJECT_SUBFOLDERS
from app.services.odoo_codes import OdooCodes
from tests.fake_odoo import FakeOdoo
from tests.test_drive_folders import FakeDrive
from tests.test_project_codes import _make_org

ROOT = "ROOT"
TAX = "22099131"


class FlakyDrive(DriveFolders):
    """Fails the first N project-tree calls, like a Drive outage mid-run."""
    fails = 0

    async def ensure_project_tree(self, *a, **kw):
        if self.fails:
            self.fails -= 1
            raise DriveError("Drive is down")
        return await super().ensure_project_tree(*a, **kw)


@pytest.fixture(autouse=True)
def _drive_root(monkeypatch):
    monkeypatch.setattr(df.settings, "google_drive_customers_folder_id", ROOT)


@pytest.fixture
def env(client_with_test_db):
    fake_odoo = FakeOdoo()
    fake_drive = FakeDrive()
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake_drive.handler))
    drive = FlakyDrive(client, "tok")

    class Env:
        pass

    e = Env()
    e.client, e.odoo, e.fdrive, e.drive = client_with_test_db, fake_odoo, fake_drive, drive
    e.codes = OdooCodes(fake_odoo, 1)

    def run(fn):
        async def go():
            async with client_with_test_db.session_maker() as db:
                out = await fn(db)
                await db.commit()
                return out
        return asyncio.run(go())

    e.run = run
    e.propose = lambda **kw: run(lambda db: issue_flow.propose(db, e.codes, req(**kw)))
    e.commit = lambda **kw: run(lambda db: issue_flow.commit(db, e.codes, e.drive, req(**kw)))
    return e


def req(**kw):
    base = dict(customer_name="甜蜜蜜台北店", tax_id=TAX, product_line="DS", site_name="台北店")
    base.update(kw)
    return IssueRequest(**base)


def folders(fake, parent):
    return sorted(f["name"] for f in fake.files if parent in f["parents"])


# ── propose ───────────────────────────────────────────────────────────────────

def test_propose_new_customer_has_no_side_effects(env):
    out = env.propose()
    assert out.customer_mode == "new" and out.needs_choice is False
    assert out.suggested_customer_code == "C0200"
    assert out.project_code == "C0200-DS01" and out.project_code_tentative
    assert any("Odoo" in a for a in out.actions) and any("Drive" in a for a in out.actions)
    # nothing created, nothing consumed
    assert env.odoo.rows("res.partner") == [] and env.odoo.rows("project.project") == []
    assert env.fdrive.files == []
    assert env.codes.peek_next_customer_id() == "C0200"
    assert env.run(lambda db: db.scalar(select(Organization))) is None
    assert not any(m == "create" or me == "next_by_code" for m, me in env.odoo.calls)


@pytest.mark.parametrize("kw,fragment", [
    (dict(tax_id="22099132"), "checksum"),
    (dict(product_line="P"), "Product line"),
    (dict(site_name="  "), "Site name"),
    (dict(roles=["boss"]), "Unknown role"),
    (dict(customer_name=" "), "Customer name"),
])
def test_propose_rejects_bad_input(env, kw, fragment):
    with pytest.raises(pc.ValidationError, match=fragment):
        env.propose(**kw)


# ── commit: new customer ──────────────────────────────────────────────────────

def test_commit_creates_everything(env):
    out = env.commit(roles=["end_user"])
    assert out.status == "done" and out.failed_step is None
    assert out.customer_code == "C0200" and out.project_code == "C0200-DS01"

    # Odoo: company first, with the code, 統編 and tags
    (company,) = env.odoo.rows("res.partner")
    assert company["is_company"] and company["ref"] == "C0200" and company["vat"] == TAX
    assert company["x_drive_url"] == out.customer_drive_url
    tag_names = {env.odoo.data["res.partner.category"][i]["name"] for i in company["category_id"]}
    assert tag_names == {"NXT Customer", "NXT End User"}
    (project,) = env.odoo.rows("project.project")
    assert project["x_project_code"] == "C0200-DS01" and project["x_product_line"] == "DS"
    assert project["partner_id"] == company["id"] and project["x_drive_url"] == out.project_drive_url

    # Drive: customer folder, project folder, five subfolders
    assert folders(env.fdrive, ROOT) == ["C0200 甜蜜蜜台北店"]
    cust_id = next(f["id"] for f in env.fdrive.files if f["name"].startswith("C0200 "))
    assert folders(env.fdrive, cust_id) == ["C0200-DS01 台北店"]
    proj_id = next(f["id"] for f in env.fdrive.files if f["name"].startswith("C0200-DS01"))
    assert folders(env.fdrive, proj_id) == sorted(PROJECT_SUBFOLDERS)

    # Local rows
    org = env.run(lambda db: db.scalar(select(Organization)))
    assert org.is_nxt_customer and org.customer_code == "C0200" and org.tax_id == TAX
    assert org.odoo_partner_id == company["id"]
    assert env.run(lambda db: db.scalar(select(Project.code))) == "C0200-DS01"


def test_second_project_for_same_customer_reuses_everything(env):
    first = env.commit()
    proposal = env.propose(product_line="DS", site_name="信義店")
    assert proposal.customer_mode == "existing" and proposal.customer_code == "C0200"
    assert proposal.project_code == "C0200-DS02" and not proposal.project_code_tentative

    second = env.commit(site_name="信義店")
    assert second.customer_code == "C0200" and second.project_code == "C0200-DS02"
    assert len(env.odoo.rows("res.partner")) == 1
    assert len(env.odoo.rows("project.project")) == 2
    assert env.codes.peek_next_customer_id() == "C0201"        # nothing extra drawn
    assert len([f for f in env.fdrive.files if f["name"].startswith("C0200 ")]) == 1
    assert second.customer_drive_url == first.customer_drive_url


def test_customer_buying_both_products_gets_two_lines(env):
    env.commit(product_line="DS", site_name="台北店")
    out = env.commit(product_line="DJ", site_name="廚房")
    assert out.customer_code == "C0200" and out.project_code == "C0200-DJ01"
    assert sorted(r["x_project_code"] for r in env.odoo.rows("project.project")) == [
        "C0200-DJ01", "C0200-DS01"]


# ── commit: failure and retry ─────────────────────────────────────────────────

def test_failed_run_is_resumable_without_duplicates(env):
    env.drive.fails = 1
    out = env.commit()
    assert out.status == "failed" and out.failed_step == "drive_project"
    assert "Drive is down" in out.error
    # Everything before the failing step happened, and only once
    assert out.customer_code == "C0200" and out.project_code == "C0200-DS01"
    assert len(env.odoo.rows("res.partner")) == 1 and len(env.odoo.rows("project.project")) == 1
    assert set(out.steps) == {"local_org", "customer_id", "odoo_company", "drive_customer",
                              "local_project", "odoo_project"}

    again = env.run(lambda db: issue_flow.retry(db, env.codes, env.drive, out.run_id))
    assert again.status == "done" and again.run_id == out.run_id
    assert again.customer_code == "C0200" and again.project_code == "C0200-DS01"
    assert len(env.odoo.rows("res.partner")) == 1 and len(env.odoo.rows("project.project")) == 1
    assert env.codes.peek_next_customer_id() == "C0201"        # drawn exactly once
    assert len([f for f in env.fdrive.files if f["name"].startswith("C0200 ")]) == 1
    assert again.project_drive_url

    # Retrying a finished run changes nothing
    count = len(env.fdrive.files)
    env.run(lambda db: issue_flow.retry(db, env.codes, env.drive, out.run_id))
    assert len(env.fdrive.files) == count


def test_retry_unknown_run(env):
    with pytest.raises(pc.ValidationError):
        env.run(lambda db: issue_flow.retry(db, env.codes, env.drive, "nope"))


# ── adopting what already exists ──────────────────────────────────────────────

def test_adopts_odoo_company_created_earlier_by_a_card_export(env):
    existing = env.odoo.add("res.partner", name="甜蜜蜜台北店股份有限公司", is_company=True)
    proposal = env.propose()
    assert any("already has a company" in w for w in proposal.warnings)
    out = env.commit()
    assert out.status == "done"
    assert len(env.odoo.rows("res.partner")) == 1             # no duplicate company
    company = env.odoo.data["res.partner"][existing]
    assert company["ref"] == "C0200" and company["vat"] == TAX and out.odoo_partner_id == existing


def test_ambiguous_odoo_companies_stop_the_run(env):
    env.odoo.add("res.partner", name="甜蜜蜜台北店", is_company=True)
    env.odoo.add("res.partner", name="甜蜜蜜台北店有限公司", is_company=True)
    assert any("merge them in Odoo" in w for w in env.propose().warnings)   # warned up front
    out = env.commit()
    assert out.status == "failed" and out.failed_step == "odoo_company"
    assert "merge them in Odoo" in out.error
    assert len(env.odoo.rows("res.partner")) == 2             # nothing new created


def test_odoo_company_with_a_different_customer_id_is_a_conflict(env):
    env.odoo.add("res.partner", name="別名", is_company=True, ref="C0150", vat=TAX)
    proposal = env.propose()
    assert proposal.customer_mode == "existing" and proposal.customer_code == "C0150"
    out = env.commit()
    assert out.customer_code == "C0150" and out.project_code == "C0150-DS01"
    assert env.codes.peek_next_customer_id() == "C0200"        # adopted, not drawn


# ── choosing among candidates ─────────────────────────────────────────────────

def test_fuzzy_match_asks_the_user_to_choose(env):
    env.run(lambda db: _make_org(db, "允將餐飲集團"))
    out = env.propose(customer_name="允將餐飲", tax_id=None)
    assert out.needs_choice and out.candidates[0].reason == "name_fuzzy"
    assert out.project_code is None
    assert any("choose" in w.lower() for w in out.warnings)


def test_user_can_choose_a_candidate_or_create_new(env):
    org_id = env.run(lambda db: _make_org(db, "允將餐飲集團"))
    ext = env.run(lambda db: db.get(Organization, org_id)).external_id

    chosen = env.propose(customer_name="允將餐飲", tax_id=None, chosen_org_ext_id=ext)
    assert not chosen.needs_choice and chosen.org_ext_id == ext
    assert chosen.customer_name == "允將餐飲集團"

    fresh = env.propose(customer_name="允將餐飲", tax_id=None, create_new=True)
    assert not fresh.needs_choice and fresh.org_ext_id is None


def test_phone_match_is_reused_without_asking(env):
    org_id = env.run(lambda db: _make_org(db, "山崎金型", phones=["(02) 2345-6789"]))
    out = env.propose(customer_name="山崎", tax_id=None, phone="+886 2 2345 6789")
    assert not out.needs_choice and out.customer_name == "山崎金型"
    ext = env.run(lambda db: db.get(Organization, org_id)).external_id
    assert out.org_ext_id == ext


def test_commit_on_a_scanned_organization_links_it(env):
    org_id = env.run(lambda db: _make_org(db, "頂期"))
    ext = env.run(lambda db: db.get(Organization, org_id)).external_id
    out = env.commit(org_ext_id=ext, customer_name=None, site_name="本店")
    assert out.status == "done"
    org = env.run(lambda db: db.get(Organization, org_id))
    assert org.is_nxt_customer and org.customer_code == "C0200" and org.tax_id == TAX
    assert env.run(lambda db: db.scalar(select(func_count()))) == 1   # no second organization
    assert "C0200 頂期" in folders(env.fdrive, ROOT)


def func_count():
    from sqlalchemy import func
    return func.count(Organization.id)


# ── 統編 conflicts ────────────────────────────────────────────────────────────

def test_tax_id_owned_by_another_customer_is_refused(env):
    other = env.run(lambda db: _make_org(db, "山崎金型", tax_id=TAX, code="C0150"))
    with pytest.raises(pc.ValidationError, match="already belongs"):
        env.propose(customer_name="完全不同", create_new=True)
    org_id = env.run(lambda db: _make_org(db, "頂期"))
    ext = env.run(lambda db: db.get(Organization, org_id)).external_id
    with pytest.raises(pc.ValidationError, match="already belongs"):
        env.propose(org_ext_id=ext, customer_name=None)


def test_organization_with_a_different_tax_id_is_refused(env):
    org_id = env.run(lambda db: _make_org(db, "頂期", tax_id="10458574"))
    ext = env.run(lambda db: db.get(Organization, org_id)).external_id
    with pytest.raises(pc.ValidationError, match="does not match"):
        env.propose(org_ext_id=ext, customer_name=None)


# ── resellers ─────────────────────────────────────────────────────────────────

def test_sold_via_requires_an_issued_reseller(env):
    reseller_id = env.run(lambda db: _make_org(db, "普印通科技"))
    ext = env.run(lambda db: db.get(Organization, reseller_id)).external_id
    with pytest.raises(pc.ValidationError, match="reseller"):
        env.propose(sold_via_org_ext_id=ext)


def test_sold_via_is_recorded_on_the_project(env):
    reseller = env.commit(customer_name="普印通科技", tax_id=None, roles=["reseller"],
                          phone="0287654321", site_name="HQ")
    reseller_ext = env.run(lambda db: db.scalar(
        select(Organization.external_id).where(Organization.customer_code == reseller.customer_code)))
    out = env.commit(customer_name="青沐餐廳", tax_id=None, phone="0299990000",
                     site_name="敦化北路店", sold_via_org_ext_id=reseller_ext, roles=["end_user"])
    assert out.customer_code == "C0201" and out.project_code == "C0201-DS01"
    project = next(p for p in env.odoo.rows("project.project") if p["x_project_code"] == "C0201-DS01")
    assert project["x_sold_via"] == reseller.odoo_partner_id
    # the reseller stays a reseller; the end user is a separate customer
    tags = {p["ref"]: len(p["category_id"]) for p in env.odoo.rows("res.partner")}
    assert tags == {"C0200": 2, "C0201": 2}


# ── the person on the card goes under the company ─────────────────────────────

def _make_card(db, org_id):
    person = Person()
    db.add(person)

    async def go():
        await db.flush()
        db.add(PersonName(person_id=person.id, language="zh", full_name="王小明",
                          name_type="primary", family_name="王", given_name="小明"))
        db.add(Position(person_id=person.id, org_id=org_id))
        db.add(ContactDetail(person_id=person.id, detail_type="email_work", value="ming@example.com"))
        card = Card(person_id=person.id)
        db.add(card)
        await db.flush()
        return card.external_id
    return go()


def test_person_is_created_under_the_company(env, monkeypatch):
    monkeypatch.setattr(odoo_sync, "_get_odoo_clients", lambda: (None, env.odoo, 1))
    org_id = env.run(lambda db: _make_org(db, "頂期"))
    card_ext = env.run(lambda db: _make_card(db, org_id))
    out = env.commit(org_ext_id=env.run(lambda db: db.get(Organization, org_id)).external_id,
                     customer_name=None, card_external_id=card_ext)
    assert out.status == "done", out.error
    people = [p for p in env.odoo.rows("res.partner") if not p.get("is_company")]
    company = next(p for p in env.odoo.rows("res.partner") if p.get("is_company"))
    assert len(people) == 1 and people[0]["name"] == "王小明"
    assert people[0]["parent_id"] == company["id"]
    assert len([p for p in env.odoo.rows("res.partner") if p.get("is_company")]) == 1


def test_person_step_can_be_skipped(env, monkeypatch):
    monkeypatch.setattr(odoo_sync, "_get_odoo_clients", lambda: (None, env.odoo, 1))
    org_id = env.run(lambda db: _make_org(db, "頂期"))
    card_ext = env.run(lambda db: _make_card(db, org_id))
    out = env.commit(org_ext_id=env.run(lambda db: db.get(Organization, org_id)).external_id,
                     customer_name=None, card_external_id=card_ext, include_person=False)
    assert out.status == "done" and out.steps["person"] == {"skipped": True}
    assert [p for p in env.odoo.rows("res.partner") if not p.get("is_company")] == []


def test_later_card_export_lands_under_the_customer(env, monkeypatch):
    """After issuing, a plain card export uses the customer company, not a name lookup."""
    monkeypatch.setattr(odoo_sync, "_get_odoo_clients", lambda: (None, env.odoo, 1))
    org_id = env.run(lambda db: _make_org(db, "頂期"))
    env.commit(org_ext_id=env.run(lambda db: db.get(Organization, org_id)).external_id,
               customer_name=None)
    card_ext = env.run(lambda db: _make_card(db, org_id))

    from app.routers.v2.export import _export_one, _load_full_card

    async def export(db):
        card = await _load_full_card(db, card_ext)
        return await _export_one(db, card, "odoo")
    item = env.run(export)
    assert item.result == "created"
    company = next(p for p in env.odoo.rows("res.partner") if p.get("is_company"))
    (person,) = [p for p in env.odoo.rows("res.partner") if not p.get("is_company")]
    assert person["parent_id"] == company["id"]
    assert len([p for p in env.odoo.rows("res.partner") if p.get("is_company")]) == 1


# ── HTTP routes ───────────────────────────────────────────────────────────────

@pytest.fixture
def api(env):
    async def _odoo():
        return env.codes

    async def _drive():
        yield env.drive

    app.dependency_overrides[projects_router.get_odoo] = _odoo
    app.dependency_overrides[projects_router.get_drive] = _drive
    yield env.client, env
    app.dependency_overrides.pop(projects_router.get_odoo, None)
    app.dependency_overrides.pop(projects_router.get_drive, None)


BODY = {"customer_name": "甜蜜蜜台北店", "tax_id": TAX, "product_line": "DS", "site_name": "台北店"}


def test_api_propose_commit_list(api):
    client, env = api
    r = client.post("/api/v2/projects/propose", json=BODY)
    assert r.status_code == 200 and r.json()["project_code"] == "C0200-DS01"

    r = client.post("/api/v2/projects/commit", json=BODY)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "done" and data["customer_code"] == "C0200"
    assert data["project_drive_url"].startswith("https://drive.google.com/drive/folders/")

    listing = client.get("/api/v2/projects").json()
    assert [p["code"] for p in listing] == ["C0200-DS01"]

    orgs = client.get("/api/v2/organizations").json()
    assert orgs[0]["is_nxt_customer"] is True and orgs[0]["customer_code"] == "C0200"


def test_api_validation_errors_are_422(api):
    client, _ = api
    assert client.post("/api/v2/projects/propose", json={**BODY, "tax_id": "22099132"}).status_code == 422
    assert client.post("/api/v2/projects/commit", json={**BODY, "product_line": "P"}).status_code == 422
    assert client.post("/api/v2/projects/runs/unknown/retry").status_code == 422


def test_api_failed_run_then_retry(api):
    client, env = api
    env.drive.fails = 1
    first = client.post("/api/v2/projects/commit", json=BODY).json()
    assert first["status"] == "failed" and first["failed_step"] == "drive_project"
    second = client.post(f"/api/v2/projects/runs/{first['run_id']}/retry").json()
    assert second["status"] == "done" and second["project_code"] == "C0200-DS01"


def test_api_resolve(api):
    client, env = api
    env.odoo.add("res.partner", name="山崎金型", is_company=True, ref="C0150", vat=TAX)
    r = client.post("/api/v2/customers/resolve", json={"tax_id": TAX}).json()
    assert r["odoo_customer_code"] == "C0150" and r["odoo_company_name"] == "山崎金型"
    assert client.post("/api/v2/customers/resolve", json={"tax_id": "1"}).status_code == 422


def test_api_nxt_customer_switch(api):
    client, env = api
    org_id = env.run(lambda db: _make_org(db, "頂期"))
    ext = env.run(lambda db: db.get(Organization, org_id)).external_id
    on = client.put(f"/api/v2/organizations/{ext}/nxt-customer", json={"enabled": True}).json()
    assert on["is_nxt_customer"] is True and on["customer_code"] is None
    off = client.put(f"/api/v2/organizations/{ext}/nxt-customer", json={"enabled": False}).json()
    assert off["is_nxt_customer"] is False
    assert client.put("/api/v2/organizations/missing/nxt-customer", json={"enabled": True}).status_code == 404

    # once a code is issued, the switch cannot be turned off
    env.commit(org_ext_id=ext, customer_name=None)
    r = client.put(f"/api/v2/organizations/{ext}/nxt-customer", json={"enabled": False})
    assert r.status_code == 409 and "C0200" in r.json()["detail"]
