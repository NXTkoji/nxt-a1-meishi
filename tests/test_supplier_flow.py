"""Propose / commit / retry for supplier codes, with fake Odoo and fake Drive."""
import asyncio

import httpx
import pytest
from sqlalchemy import select

from app.db.models import IssueRun, Organization
from app.schemas.api import SupplierIssueRequest
from app.services import drive_folders as df
from app.services import project_codes as pc
from app.services import supplier_flow
from app.services.drive_folders import DriveError, DriveFolders
from app.services.odoo_codes import OdooCodes
from tests.fake_odoo import FakeOdoo
from tests.test_drive_folders import FakeDrive
from tests.test_project_codes import _make_org

SROOT = "SROOT"
TAX = "22099131"


class FlakyDrive(DriveFolders):
    """Fails the first N supplier-folder calls, like a Drive outage mid-run."""
    fails = 0

    async def ensure_supplier_folder(self, *a, **kw):
        if self.fails:
            self.fails -= 1
            raise DriveError("Drive is down")
        return await super().ensure_supplier_folder(*a, **kw)


@pytest.fixture(autouse=True)
def _drive_root(monkeypatch):
    monkeypatch.setattr(df.settings, "google_drive_suppliers_folder_id", SROOT)


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
    e.codes = OdooCodes(fake_odoo, 1, 1)
    e.cat = {name: fake_drive.add(name, SROOT) for name in
             ("Parts Suppliers", "Administration Suppliers", "Potential Suppliers")}

    def run(fn):
        async def go():
            async with client_with_test_db.session_maker() as db:
                out = await fn(db)
                await db.commit()
                return out
        return asyncio.run(go())

    e.run = run
    e.propose = lambda **kw: run(lambda db: supplier_flow.propose_supplier(db, e.codes, req(**kw)))
    e.propose_with_drive = lambda **kw: run(
        lambda db: supplier_flow.propose_supplier(db, e.codes, req(**kw), e.drive))
    e.commit = lambda **kw: run(lambda db: supplier_flow.commit_supplier(db, e.codes, e.drive, req(**kw)))

    def retry(run_ext_id):
        async def go(db):
            row = await db.scalar(select(IssueRun).where(IssueRun.external_id == run_ext_id))
            return await supplier_flow.retry_supplier(db, e.codes, e.drive, row)
        return run(go)

    e.retry = retry
    e.org = lambda **cond: run(lambda db: db.scalar(select(Organization).filter_by(**cond)))
    return e


def req(**kw):
    base = dict(supplier_name="新零件供應商", category="parts", tax_id=TAX, country="TW")
    base.update(kw)
    return SupplierIssueRequest(**base)


def names(fake, parent):
    return sorted(f["name"] for f in fake.files if parent in f["parents"])


# ── propose ───────────────────────────────────────────────────────────────────

def test_propose_new_supplier_has_no_side_effects(env):
    out = env.propose()
    assert out.supplier_mode == "new" and out.needs_choice is False
    assert out.suggested_supplier_code == "S100" and out.supplier_code is None
    assert out.category == "parts" and out.category_folder == "Parts Suppliers"
    assert any("S100" in a for a in out.actions) and any("Parts Suppliers" in a for a in out.actions)
    assert any("preview" in w for w in out.warnings)
    assert env.odoo.rows("res.partner") == []
    assert env.codes.peek_next_supplier_code() == "S100"
    assert env.run(lambda db: db.scalar(select(Organization))) is None
    assert not any(m == "create" or me == "next_by_code" for m, me in env.odoo.calls)


@pytest.mark.parametrize("kw,fragment", [
    (dict(tax_id="22099132"), "checksum"),
    (dict(category="japan"), "Category"),
    (dict(supplier_name=" "), "Supplier name"),
    (dict(country="JP", tax_id="12345"), "13 digits"),
])
def test_propose_rejects_bad_input(env, kw, fragment):
    with pytest.raises(pc.ValidationError, match=fragment):
        env.propose(**kw)


def test_propose_allows_no_tax_id_and_foreign_ids(env):
    assert env.propose(tax_id=None).suggested_supplier_code == "S100"
    assert env.propose(tax_id="SE556677889901", country="SE").tax_id == "SE556677889901"


def test_propose_reuses_a_code_already_in_odoo(env):
    env.odoo.add("res.partner", name="新零件供應商", is_company=True, vat=TAX,
                 x_supplier_code="SA002", company_id=1)
    out = env.propose()
    assert out.supplier_mode == "existing" and out.supplier_code == "SA002"
    assert out.suggested_supplier_code is None
    assert env.codes.peek_next_supplier_code() == "S100"


def test_propose_reuses_a_supplier_code_still_in_company_id(env):
    env.odoo.add("res.partner", name="新零件供應商", is_company=True, vat=TAX,
                 company_registry="S059", company_id=1)
    assert env.propose().supplier_code == "S059"


def test_propose_says_when_the_party_is_already_a_customer(env):
    env.run(lambda db: _make_org(db, "新零件供應商", tax_id=TAX, code="C0200"))
    env.odoo.add("res.partner", name="新零件供應商", is_company=True, vat=TAX,
                 company_registry="C0200", company_id=1)
    out = env.propose()
    assert out.supplier_mode == "new" and out.suggested_supplier_code == "S100"
    assert any("already a customer" in a for a in out.actions)


def test_propose_with_several_candidates_asks_for_a_choice(env):
    env.run(lambda db: _make_org(db, "新零件供應商股份有限公司"))
    env.run(lambda db: _make_org(db, "新零件供應商集團"))
    out = env.propose(tax_id=None)
    assert out.needs_choice and len(out.candidates) == 2
    assert out.suggested_supplier_code is None


def test_propose_describes_the_folder_plan(env):
    env.fdrive.add("新零件供應商有限公司", env.cat["Potential Suppliers"])
    out = env.propose_with_drive()
    assert any("Move the prospect folder" in a for a in out.actions)
    assert env.fdrive.files[-1]["parents"] == [env.cat["Potential Suppliers"]]   # nothing moved yet


def test_propose_warns_when_several_prospect_folders_match(env):
    env.fdrive.add("新零件供應商", env.cat["Potential Suppliers"])
    pot = env.fdrive.add("Potential", env.cat["Parts Suppliers"])
    env.fdrive.add("新零件供應商有限公司", pot)
    out = env.propose_with_drive()
    assert any("none will be moved" in w for w in out.warnings)


# ── commit: new supplier ──────────────────────────────────────────────────────

def test_commit_creates_everything(env):
    out = env.commit()
    assert out.status == "done" and out.failed_step is None and out.kind == "supplier"
    assert out.supplier_code == "S100"

    (company,) = env.odoo.rows("res.partner")
    assert company["is_company"] and company["name"] == "新零件供應商" and company["vat"] == TAX
    assert company["x_supplier_code"] == "S100" and company["x_supplier_status"] == "active"
    assert company["company_id"] == 1                         # belongs to the app's Odoo company
    assert "company_registry" not in company                  # Company ID stays for Customer IDs
    assert company["x_supplier_drive_url"] == out.supplier_drive_url
    tags = {env.odoo.data["res.partner.category"][i]["name"] for i in company["category_id"]}
    assert tags == {"Supplier: Parts"}

    assert names(env.fdrive, env.cat["Parts Suppliers"]) == ["S100 新零件供應商"]

    org = env.org(supplier_code="S100")
    assert org.supplier_status == "active" and org.supplier_category == "parts"
    assert org.tax_id == TAX and org.odoo_partner_id == company["id"]
    assert org.customer_status is None and org.is_nxt_customer is False
    assert org.supplier_drive_url == out.supplier_drive_url
    assert env.codes.peek_next_supplier_code() == "S101"
    run = env.run(lambda db: db.scalar(select(IssueRun)))
    assert run.kind == "supplier" and run.status == "done"


def test_commit_for_a_party_without_tax_id(env):
    out = env.commit(tax_id=None)
    assert out.status == "done" and out.supplier_code == "S100"
    assert "vat" not in env.odoo.rows("res.partner")[0]


def test_commit_for_an_existing_local_organization(env):
    oid = env.run(lambda db: _make_org(db, "既有組織", tax_id=TAX))
    org = env.run(lambda db: db.get(Organization, oid))
    out = env.commit(org_ext_id=org.external_id, supplier_name=None, tax_id=None)
    assert out.status == "done" and out.supplier_code == "S100"
    assert env.org(id=oid).supplier_code == "S100"
    assert env.run(_count_orgs) == 1


def test_a_second_commit_for_the_same_supplier_reuses_the_code(env):
    first = env.commit()
    org = env.org(supplier_code="S100")
    second = env.commit(org_ext_id=org.external_id, supplier_name=None, tax_id=None)
    assert second.status == "done" and second.supplier_code == first.supplier_code == "S100"
    assert second.steps["supplier_code"]["reused"] is True
    assert env.codes.peek_next_supplier_code() == "S101"
    assert len(env.odoo.rows("res.partner")) == 1
    assert names(env.fdrive, env.cat["Parts Suppliers"]) == ["S100 新零件供應商"]


def test_commit_never_issues_s999(env):
    env.odoo.data["ir.sequence"][78]["number_next_actual"] = 999
    out = env.commit()
    assert out.supplier_code == "S1000"


# ── commit: failures and retry ────────────────────────────────────────────────

def test_drive_failure_then_retry_creates_nothing_twice(env):
    env.drive.fails = 1
    out = env.commit()
    assert out.status == "failed" and out.failed_step == "drive_supplier"
    assert "Drive is down" in out.error
    assert out.supplier_code == "S100"                       # drawn and kept
    assert env.codes.peek_next_supplier_code() == "S101"
    (company,) = env.odoo.rows("res.partner")
    assert company["x_supplier_code"] == "S100" and "x_supplier_drive_url" not in company

    done = env.retry(out.run_id)
    assert done.status == "done" and done.supplier_code == "S100"
    assert env.codes.peek_next_supplier_code() == "S101"     # no second draw
    assert len(env.odoo.rows("res.partner")) == 1            # no second company
    assert names(env.fdrive, env.cat["Parts Suppliers"]) == ["S100 新零件供應商"]
    assert env.odoo.rows("res.partner")[0]["x_supplier_drive_url"] == done.supplier_drive_url
    assert env.retry(out.run_id).status == "done"             # retrying a finished run is a no-op


def test_a_code_in_odoo_from_a_stopped_run_is_not_drawn_again(env):
    # a run stopped after Odoo had the code but before the local row kept it
    env.odoo.add("res.partner", name="新零件供應商", is_company=True, vat=TAX,
                 x_supplier_code="S100", company_id=1)
    out = env.commit()
    assert out.status == "done" and out.supplier_code == "S100"
    assert out.steps["supplier_code"]["source"] == "odoo"
    assert env.codes.peek_next_supplier_code() == "S100"      # nothing consumed
    assert len(env.odoo.rows("res.partner")) == 1


def test_a_supplier_code_owned_by_another_organization_is_refused(env):
    env.commit()                                              # S100 now belongs to the first
    env.odoo.add("res.partner", name="另一家", is_company=True, x_supplier_code="S100", company_id=1)
    out = env.commit(supplier_name="另一家", tax_id=None)
    assert out.status == "failed" and out.failed_step == "supplier_code"
    assert "already belongs" in out.error


def test_tax_id_of_another_organization_is_refused(env):
    env.run(lambda db: _make_org(db, "別人", tax_id=TAX))
    with pytest.raises(pc.ValidationError, match="already belongs"):
        env.propose(create_new=True)
    out = env.commit(create_new=True)
    assert out.status == "failed" and out.failed_step == "local_org"
    assert "already belongs" in out.error
    assert env.odoo.rows("res.partner") == []
    assert env.codes.peek_next_supplier_code() == "S100"      # nothing drawn


# ── existing parties ──────────────────────────────────────────────────────────

def test_an_existing_supplier_keeps_its_code_and_folder(env):
    env.odoo.add("res.partner", name="某事務所", is_company=True, vat=TAX,
                 company_registry="SA002", company_id=1)
    old = env.fdrive.add("SA002 某事務所", env.cat["Administration Suppliers"])
    out = env.commit(supplier_name="某事務所", category="administration")
    assert out.status == "done" and out.supplier_code == "SA002"
    assert out.steps["drive_supplier"]["status"] == "existing"
    assert out.supplier_drive_url.endswith(old)
    assert names(env.fdrive, env.cat["Administration Suppliers"]) == ["SA002 某事務所"]
    (company,) = env.odoo.rows("res.partner")
    assert company["x_supplier_code"] == "SA002" and company["company_registry"] == "SA002"
    assert env.codes.peek_next_supplier_code() == "S100"


def test_a_customer_becomes_a_supplier_without_touching_customer_data(env):
    oid = env.run(lambda db: _make_org(db, "甜蜜蜜", tax_id=TAX, code="C0200"))
    org = env.run(lambda db: db.get(Organization, oid))
    cust_folder = "CUSTFOLDER"
    env.run(lambda db: _set(db, oid, drive_folder_id=cust_folder, drive_url="https://x/cust",
                            odoo_partner_id=None))
    pid = env.odoo.add("res.partner", name="甜蜜蜜", is_company=True, vat=TAX,
                       company_registry="C0200", x_drive_url="https://x/cust",
                       x_legacy_codes="DS127, P030", company_id=1)
    out = env.commit(org_ext_id=org.external_id, supplier_name=None, tax_id=None)
    assert out.status == "done" and out.supplier_code == "S100"
    assert out.customer_code == "C0200"
    assert len(env.odoo.rows("res.partner")) == 1
    co = env.odoo.data["res.partner"][pid]
    assert co["company_registry"] == "C0200" and co["x_drive_url"] == "https://x/cust"
    assert co["x_legacy_codes"] == "DS127, P030"
    assert co["x_supplier_code"] == "S100" and co["x_supplier_status"] == "active"
    org = env.org(id=oid)
    assert org.customer_code == "C0200" and org.drive_folder_id == cust_folder
    assert org.supplier_code == "S100"
    # the supplier folder is in the suppliers tree; the customer tree is not touched
    assert names(env.fdrive, env.cat["Parts Suppliers"]) == ["S100 甜蜜蜜"]


def test_a_prospect_folder_is_promoted_on_commit(env):
    prospect = env.fdrive.add("新零件供應商有限公司", env.cat["Potential Suppliers"])
    out = env.commit()
    assert out.status == "done" and out.steps["drive_supplier"]["status"] == "promoted"
    assert out.supplier_drive_url.endswith(prospect)
    assert names(env.fdrive, env.cat["Potential Suppliers"]) == []
    assert names(env.fdrive, env.cat["Parts Suppliers"]) == ["S100 新零件供應商有限公司"]


# ── multi-company ─────────────────────────────────────────────────────────────

def test_another_companys_partner_is_never_reused(env):
    theirs = env.odoo.add("res.partner", name="新零件供應商", is_company=True, vat=TAX,
                          x_supplier_code="VGJ", company_id=2)
    out = env.commit()
    assert out.status == "done" and out.supplier_code == "S100"
    assert len(env.odoo.rows("res.partner")) == 2
    assert env.odoo.data["res.partner"][theirs]["x_supplier_code"] == "VGJ"
    assert env.odoo.data["res.partner"][theirs].get("x_supplier_status") is None


def _set(db, org_id, **vals):
    async def go():
        org = await db.get(Organization, org_id)
        for k, v in vals.items():
            setattr(org, k, v)
    return go()


async def _count_orgs(db):
    return len((await db.scalars(select(Organization))).all())
