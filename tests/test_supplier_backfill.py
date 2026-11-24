"""Supplier backfill: plan rows and apply, on fakes. Nothing here touches real Odoo or Drive."""
import asyncio

import httpx
import pytest

from app.services import supplier_backfill as bf
from app.services.drive_folders import DriveFolders
from app.services.odoo_codes import OdooCodes
from tests.fake_odoo import FakeOdoo
from tests.test_drive_folders import FakeDrive

ROOT = "SROOT"


class World:
    def __init__(self):
        self.odoo, self.drive = FakeOdoo(), FakeDrive()
        self.codes = OdooCodes(self.odoo, 1, 1)
        self.cat = {}

    def folder(self, name, parent=ROOT):
        return self.drive.add(name, parent)

    def category(self, name):
        self.cat[name] = self.drive.add(name, ROOT)
        return self.cat[name]

    def partner(self, name, **kw):
        kw.setdefault("company_id", 1)
        kw.setdefault("is_company", True)
        return self.odoo.add("res.partner", name=name, **kw)

    def plan(self):
        async def go():
            client = httpx.AsyncClient(transport=httpx.MockTransport(self.drive.handler))
            async with DriveFolders(client, "tok") as d:
                return await bf.build_plan(self.codes, d, ROOT)
        return asyncio.run(go())

    def row(self, plan, **match):
        found = [r for r in plan.rows if all(getattr(r, k) == v for k, v in match.items())]
        assert len(found) == 1, (match, [(r.action, r.name, r.decision) for r in plan.rows])
        return found[0]


@pytest.fixture
def w():
    return World()


def test_code_of():
    assert bf.code_of("S059 エスポ化学") == "S059"
    assert bf.code_of("SC077 金斗雲工程行 台中") == "SC077"
    assert bf.code_of("sa002 x") == "SA002"
    assert bf.code_of("S100") == "S100"
    assert bf.code_of("Samsung 123") is None and bf.code_of("嘉義") is None
    assert bf._clean_name("SC077 金斗雲工程行 台中") == "金斗雲工程行 台中"


def test_code_match_sets_supplier_fields_and_clears_company_id(w):
    parts = w.category("Parts Suppliers")
    f = w.folder("S059 上億國際", parts)
    pid = w.partner("上億國際", company_registry="S059")
    plan = w.plan()
    r = w.row(plan, action="set_supplier", partner_id=pid)
    assert (r.rule, r.decision, r.code, r.category, r.status) == ("1", "apply", "S059", "parts", "active")
    assert r.folder_id == f

    results = bf.apply_plan(w.codes, plan)
    assert results[r.row_id] == "updated"
    p = w.odoo.data["res.partner"][pid]
    assert p["x_supplier_code"] == "S059" and p["x_supplier_status"] == "active"
    assert p["company_registry"] is False
    assert p["x_supplier_drive_url"].endswith(f)
    tags = {w.odoo.data["res.partner.category"][i]["name"] for i in p["category_id"]}
    assert tags == {"Supplier: Parts"}


def test_applying_twice_changes_nothing_and_a_rerun_plan_is_empty(w):
    parts = w.category("Parts Suppliers")
    w.folder("S059 上億國際", parts)
    w.partner("上億國際", company_registry="S059")
    plan = w.plan()
    bf.apply_plan(w.codes, plan)
    before = {k: dict(v) for k, v in w.odoo.data["res.partner"].items()}
    again = bf.apply_plan(w.codes, plan)                      # the same stale plan, second time
    assert set(again.values()) == {"unchanged"}
    assert w.odoo.data["res.partner"] == before
    assert w.plan().rows == [] and w.plan().counts["already_done"] == 1


def test_partner_of_another_company_is_counted_never_planned(w):
    parts = w.category("Parts Suppliers")
    w.folder("S059 上億國際", parts)
    other = w.partner("上億國際", company_registry="S059", company_id=2)
    plan = w.plan()
    assert plan.counts["odoo_partners_of_other_companies"] == 1
    assert other not in [r.partner_id for r in plan.rows]
    (row,) = [r for r in plan.rows if r.code == "S059"]
    assert row.action == "create_company"                     # NXT has no such company; folder stays coded


def test_shared_partner_is_listed_for_confirmation_not_changed(w):
    parts = w.category("Marketing Suppliers")
    w.folder("SM025 奇桔數位行銷", parts)
    pid = w.partner("奇桔數位行銷", company_registry="SM025", company_id=False)
    plan = w.plan()
    r = w.row(plan, partner_id=pid)
    assert r.decision == bf.CONFIRM and "Shared" in r.note
    results = bf.apply_plan(w.codes, plan)
    assert results[r.row_id] == "not applied (confirm)"
    assert "x_supplier_code" not in w.odoo.data["res.partner"][pid]
    # the person edits the report to apply
    assert bf.apply_plan(w.codes, plan, {r.row_id: bf.APPLY})[r.row_id] == "updated"


def test_coded_folder_without_a_company_creates_a_minimal_one(w):
    c = w.category("Construction Suppliers")
    f = w.folder("SC070 銀創企業社", c)
    plan = w.plan()
    r = w.row(plan, action="create_company")
    assert (r.rule, r.decision, r.code, r.name, r.status) == ("2", "apply", "SC070", "銀創企業社", "active")
    bf.apply_plan(w.codes, plan)
    (p,) = w.odoo.rows("res.partner")
    assert p["name"] == "銀創企業社" and p["x_supplier_code"] == "SC070" and p["company_id"] == 1
    assert p["x_supplier_status"] == "active" and p["is_company"] is True and p["x_supplier_drive_url"].endswith(f)
    assert bf.apply_plan(w.codes, plan)[r.row_id].startswith("unchanged")
    assert len(w.odoo.rows("res.partner")) == 1               # not created twice


def test_regional_folders_are_containers_whose_coded_children_are_suppliers(w):
    c = w.category("Construction Suppliers")
    w.folder("SC054 鴻永水電行", c)
    region = w.folder("台中", c)
    w.folder("SC077 金斗雲工程行 台中", region)
    w.folder("SC078 榮昇企業社 台中", region)
    empty_region = w.folder("嘉義", c)                          # no coded folder inside: decide
    plan = w.plan()
    codes = {r.code for r in plan.rows if r.code}
    assert codes == {"SC054", "SC077", "SC078"}
    assert w.row(plan, code="SC077").folder_path == "Construction Suppliers/台中/SC077 金斗雲工程行 台中"
    assert w.row(plan, code="SC077").category == "construction"
    pending = w.row(plan, name="嘉義")
    assert (pending.rule, pending.decision, pending.status, pending.folder_id) == ("4", "confirm", "potential", empty_region)
    assert not any(r.name == "台中" for r in plan.rows)       # the container itself is not a supplier


def test_potential_locations_become_potential_without_a_code(w):
    w.drive.add("Potential Suppliers", ROOT)
    top = w.drive.files[-1]["id"]
    w.folder("新創零件", top)
    parts = w.category("Parts Suppliers")
    pot = w.folder("Potential", parts)
    w.folder("某工廠", pot)
    plan = w.plan()
    a, b = w.row(plan, name="新創零件"), w.row(plan, name="某工廠")
    assert (a.rule, a.decision, a.status, a.code, a.category) == ("3", "apply", "potential", "", "")
    assert (b.category, b.decision) == ("parts", "apply")
    bf.apply_plan(w.codes, plan)
    rows = {r["name"]: r for r in w.odoo.rows("res.partner")}
    assert rows["新創零件"]["x_supplier_status"] == "potential" and "x_supplier_code" not in rows["新創零件"]
    tags = {w.odoo.data["res.partner.category"][i]["name"] for i in rows["某工廠"]["category_id"]}
    assert tags == {"Supplier: Parts"}


def test_uncoded_japan_folders_are_listed_for_a_decision(w):
    jp = w.category("Japan Suppliers")
    w.folder("S001 コード付き", jp)
    w.folder("無番号商事", jp)
    plan = w.plan()
    r = w.row(plan, name="無番号商事")
    assert (r.rule, r.decision, r.status, r.category) == ("4", "confirm", "potential", "")
    assert w.row(plan, code="S001").category == ""             # Japan Suppliers has no category tag
    assert bf.apply_plan(w.codes, plan)[r.row_id] == "not applied (confirm)"
    assert [p["name"] for p in w.odoo.rows("res.partner")] == ["コード付き"]


def test_duplicate_code_on_two_folders_is_a_conflict_never_applied(w):
    parts = w.category("Parts Suppliers")
    jp = w.category("Japan Suppliers")
    w.folder("S058 上億國際 Aqua Aircon", parts)
    w.folder("S058 エスポ化学", jp)
    pid = w.partner("エスポ化学", company_registry="S058")
    plan = w.plan()
    conflicts = [r for r in plan.rows if r.action == "conflict"]
    assert len(conflicts) == 3 and all(r.decision == bf.SKIP and r.rule == "5" for r in conflicts)
    assert not [r for r in plan.rows if r.action in ("set_supplier", "create_company")]
    bf.apply_plan(w.codes, plan, {r.row_id: bf.APPLY for r in conflicts})   # even if "apply" is typed
    assert w.odoo.data["res.partner"][pid]["company_registry"] == "S058"


def test_branch_with_its_parents_code_is_not_a_conflict(w):
    adm = w.category("Administration Suppliers")
    w.folder("SA034 交通部", adm)
    parent = w.partner("交通部", company_registry="SA034")
    w.partner("公路總局", company_registry="SA034", parent_id=[parent, "交通部"])
    plan = w.plan()
    assert not [r for r in plan.rows if r.action == "conflict"]
    assert w.row(plan, action="set_supplier").partner_id == parent


def test_customer_with_a_supplier_looking_company_id_waits_for_a_person(w):
    parts = w.category("Parts Suppliers")
    w.folder("S059 甲公司", parts)
    pid = w.partner("甲公司", company_registry="S059", x_customer_status="active")
    r = w.row(w.plan(), partner_id=pid)
    assert r.decision == bf.CONFIRM and "Customer" in r.note


def test_name_only_match_is_a_candidate_and_can_make_a_dual_role_party(w):
    """光殿貿易 is a customer P030 and a supplier SM009: same company, both codes."""
    mk = w.category("Marketing Suppliers")
    f = w.folder("SM009 光殿貿易", mk)
    pid = w.partner("光殿貿易", company_registry="P030", x_customer_status="active")
    plan = w.plan()
    r = w.row(plan, partner_id=pid)
    assert (r.action, r.decision, r.rule) == ("link_name_match", "confirm", "1")
    assert not [x for x in plan.rows if x.action == "create_company"]
    assert bf.apply_plan(w.codes, plan)[r.row_id] == "not applied (confirm)"

    assert bf.apply_plan(w.codes, plan, {r.row_id: bf.APPLY})[r.row_id] == "updated"
    p = w.odoo.data["res.partner"][pid]
    assert p["company_registry"] == "P030"                    # the customer side is untouched
    assert p["x_supplier_code"] == "SM009" and p["x_supplier_status"] == "active"
    assert p["x_customer_status"] == "active" and p["x_supplier_drive_url"].endswith(f)


def test_name_match_row_can_be_turned_into_a_new_company(w):
    mk = w.category("Marketing Suppliers")
    w.folder("SM009 光殿貿易", mk)
    pid = w.partner("光殿貿易", company_registry="P030")
    r = w.row(w.plan(), partner_id=pid)
    bf.apply_plan(w.codes, w.plan(), {r.row_id: bf.CREATE})
    assert len(w.odoo.rows("res.partner")) == 2
    assert "x_supplier_code" not in w.odoo.data["res.partner"][pid]


def test_apply_refuses_a_code_that_is_already_elsewhere(w):
    parts = w.category("Parts Suppliers")
    w.folder("S059 甲", parts)
    pid = w.partner("甲", company_registry="S059")
    plan = w.plan()
    w.partner("乙", x_supplier_code="S059")                    # appears after the dry run
    assert bf.apply_plan(w.codes, plan)[plan.rows[0].row_id].startswith("failed")
    assert w.odoo.data["res.partner"][pid]["company_registry"] == "S059"


def test_supplier_company_id_with_no_folder_still_moves(w):
    pid = w.partner("孤兒公司", company_registry="SF012")
    plan = w.plan()
    r = w.row(plan, partner_id=pid)
    assert (r.decision, r.category, r.folder_id) == ("apply", "", "")
    bf.apply_plan(w.codes, plan)
    assert w.odoo.data["res.partner"][pid]["x_supplier_code"] == "SF012"


def test_report_round_trip_keeps_decisions(w, tmp_path):
    mk = w.category("Marketing Suppliers")
    w.folder("SM009 光殿貿易", mk)
    w.partner("光殿貿易", company_registry="P030")
    plan = w.plan()
    path = tmp_path / "r.csv"
    bf.write_report(plan, str(path))
    text = path.read_text(encoding="utf-8-sig")
    assert "link_name_match" in text and "row_id" in text.splitlines()[0]
    edited = text.replace(",confirm,", ",apply,")
    path.write_text(edited, encoding="utf-8-sig")
    decisions = bf.read_decisions(str(path))
    assert list(decisions.values()) == ["apply"]
    path.write_text(text.replace(",confirm,", ",maybe,"), encoding="utf-8-sig")
    with pytest.raises(ValueError):
        bf.read_decisions(str(path))
