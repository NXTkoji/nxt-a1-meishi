"""Drive folder creation, against a small fake of the Drive v3 REST API."""
import asyncio
import json
import re

import httpx
import pytest

from app.config import settings
from app.services import drive_folders as df
from app.services.drive_folders import (
    CUSTOMER_SUBFOLDERS, DriveError, DriveFolders, PROJECT_SUBFOLDERS,
)


class FakeDrive:
    def __init__(self):
        self.files: list[dict] = []
        self._n = 0
        self.fail_next: list[int] = []   # status codes to return before succeeding
        self.requests = 0

    def add(self, name, parent, owned=True):
        # owned=False: a folder shared from another Google account (e.g. "NXT Customers Old")
        self._n += 1
        f = {"id": f"id{self._n}", "name": name, "parents": [parent], "trashed": False,
             "ownedByMe": owned}
        self.files.append(f)
        return f["id"]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        if self.fail_next:
            return httpx.Response(self.fail_next.pop(0), json={"error": "x"})
        assert request.headers["authorization"] == "Bearer tok"
        if request.method == "GET":
            q = request.url.params["q"]
            if "in parents" in q:
                parent = re.search(r"'([^']+)' in parents", q).group(1)
                hits = [f for f in self.files if parent in f["parents"] and not f["trashed"]]
            else:   # global "name contains 'X'" search
                term = re.search(r"name contains '([^']+)'", q).group(1)
                hits = [f for f in self.files if term in f["name"] and not f["trashed"]]
            return httpx.Response(200, json={"files": [
                {"id": f["id"], "name": f["name"], "parents": f["parents"],
                 "ownedByMe": f["ownedByMe"]} for f in hits]})
        if request.method == "PATCH":
            params = request.url.params
            f = next(f for f in self.files if f["id"] == request.url.path.rsplit("/", 1)[1])
            remove = [x for x in params.get("removeParents", "").split(",") if x]
            f["parents"] = [x for x in f["parents"] if x not in remove] + [params["addParents"]]
            body = json.loads(request.content or b"{}")
            if "name" in body:
                f["name"] = body["name"]
            return httpx.Response(200, json={"id": f["id"], "parents": f["parents"]})
        body = json.loads(request.content)
        assert body["mimeType"] == df.FOLDER_MIME
        fid = self.add(body["name"], body["parents"][0])
        return httpx.Response(200, json={"id": fid, "name": body["name"]})


@pytest.fixture
def drive(monkeypatch):
    monkeypatch.setattr(df, "_RETRY_BACKOFF_SECONDS", 0)
    fake = FakeDrive()
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    return fake, DriveFolders(client, "tok")


def run(coro):
    return asyncio.run(coro)


def names_in(fake, parent):
    return sorted(f["name"] for f in fake.files if parent in f["parents"])


def test_sanitize_folder_name():
    assert df.sanitize_folder_name("A/B: C\\D  E ") == "A_B_ C_D E"
    assert df.FolderRef("abc", "x").url == "https://drive.google.com/drive/folders/abc"


def test_customer_and_project_tree_layout(drive):
    fake, d = drive

    async def go():
        cust = await d.ensure_customer_folder("C0200", "甜蜜蜜台北店", parent_id="ROOT")
        proj = await d.ensure_project_tree(cust.id, "C0200S01", "台北店")
        return cust, proj

    cust, proj = run(go())
    assert cust.name == "C0200 甜蜜蜜台北店"
    assert names_in(fake, "ROOT") == ["C0200 甜蜜蜜台北店"]
    # finance & logistics sits beside the project folders, once per customer
    assert names_in(fake, cust.id) == sorted(["C0200S01 台北店", *CUSTOMER_SUBFOLDERS])
    assert names_in(fake, proj.id) == sorted(PROJECT_SUBFOLDERS)
    assert PROJECT_SUBFOLDERS == ("1 requirement", "2 proposal", "3 construction", "4 maintenance")
    assert CUSTOMER_SUBFOLDERS == ("finance & logistics",)


def test_a_customer_can_have_several_project_folders(drive):
    fake, d = drive

    async def go():
        cust = await d.ensure_customer_folder("C0200", "甜蜜蜜", parent_id="ROOT")
        await d.ensure_project_tree(cust.id, "C0200S01", "台北店")
        await d.ensure_project_tree(cust.id, "C0200J01", "信義店")
        await d.ensure_customer_folder("C0200", "甜蜜蜜", parent_id="ROOT")
        return cust

    cust = run(go())
    assert names_in(fake, cust.id) == sorted(
        ["C0200S01 台北店", "C0200J01 信義店", "finance & logistics"])


def test_running_twice_creates_nothing_new(drive):
    fake, d = drive

    async def once():
        cust = await d.ensure_customer_folder("C0200", "甜蜜蜜", parent_id="ROOT")
        return await d.ensure_project_tree(cust.id, "C0200S01", "台北店")

    first = run(once())
    count = len(fake.files)
    second = run(once())
    assert len(fake.files) == count and first.id == second.id


def test_renamed_customer_or_site_does_not_get_a_second_folder(drive):
    fake, d = drive

    async def go():
        a = await d.ensure_customer_folder("C0200", "舊名稱", parent_id="ROOT")
        b = await d.ensure_customer_folder("C0200", "新名稱", parent_id="ROOT")
        return a, b

    a, b = run(go())
    assert a.id == b.id and len(names_in(fake, "ROOT")) == 1


def test_code_prefix_does_not_match_a_longer_code(drive):
    fake, d = drive
    fake.add("C02001 另一家", "ROOT")

    async def go():
        return await d.ensure_customer_folder("C0200", "甜蜜蜜", parent_id="ROOT")

    ref = run(go())
    assert ref.name == "C0200 甜蜜蜜"
    assert len(names_in(fake, "ROOT")) == 2


def test_missing_customers_folder_setting(drive, monkeypatch):
    _, d = drive
    monkeypatch.setattr(settings, "google_drive_customers_folder_id", "")
    with pytest.raises(DriveError):
        run(d.ensure_customer_folder("C0200", "X"))


def test_transient_errors_are_retried(drive):
    fake, d = drive
    fake.fail_next = [503, 429]
    ref = run(d.ensure_folder("ROOT", "1 requirement"))
    assert ref.name == "1 requirement" and fake.requests >= 3


def test_persistent_errors_raise_drive_error(drive):
    fake, d = drive
    fake.fail_next = [503, 503, 503]
    with pytest.raises(DriveError):
        run(d.ensure_folder("ROOT", "x"))


def test_client_errors_are_not_retried(drive):
    fake, d = drive
    fake.fail_next = [403]
    with pytest.raises(DriveError):
        run(d.ensure_folder("ROOT", "x"))
    assert fake.requests == 1


# ── old (legacy) folders ──────────────────────────────────────────────────────

def test_move_legacy_folder_keeps_its_name(drive, monkeypatch):
    fake, d = drive
    monkeypatch.setattr(settings, "google_drive_customers_folder_id", "ROOT")
    old = fake.add("DS176 甜蜜蜜台北店", "ROOT")

    async def go():
        cust = await d.ensure_customer_folder("C0200", "甜蜜蜜", parent_id="ROOT")
        return cust, await d.move_legacy_folder("DS176", cust.id)

    cust, (status, folder) = run(go())
    assert status == "moved" and folder.id == old
    assert names_in(fake, "ROOT") == ["C0200 甜蜜蜜"]
    assert names_in(fake, cust.id) == sorted(["DS176 甜蜜蜜台北店", *CUSTOMER_SUBFOLDERS])


def test_move_legacy_folder_is_idempotent(drive, monkeypatch):
    fake, d = drive
    monkeypatch.setattr(settings, "google_drive_customers_folder_id", "ROOT")
    fake.add("DS176 甜蜜蜜", "ROOT")

    async def go():
        cust = await d.ensure_customer_folder("C0200", "甜蜜蜜", parent_id="ROOT")
        first = await d.move_legacy_folder("DS176", cust.id)
        second = await d.move_legacy_folder("DS176", cust.id)
        return first[0], second[0]

    assert run(go()) == ("moved", "already_inside")


def test_legacy_folder_not_found_and_prefix_is_exact(drive, monkeypatch):
    fake, d = drive
    monkeypatch.setattr(settings, "google_drive_customers_folder_id", "ROOT")
    fake.add("DS1760 別家", "ROOT")          # longer number must not match DS176
    fake.add("notes about DS176", "ROOT")     # not a prefix match either

    async def go():
        cust = await d.ensure_customer_folder("C0200", "甜蜜蜜", parent_id="ROOT")
        return await d.move_legacy_folder("DS176", cust.id)

    status, folder = run(go())
    assert status == "not_found" and folder is None


def test_legacy_folder_with_several_matches_is_an_error(drive, monkeypatch):
    fake, d = drive
    monkeypatch.setattr(settings, "google_drive_customers_folder_id", "ROOT")
    fake.add("DS176 甜蜜蜜", "ROOT")
    fake.add("DS176 舊備份", "OTHER")

    async def go():
        cust = await d.ensure_customer_folder("C0200", "甜蜜蜜", parent_id="ROOT")
        return await d.move_legacy_folder("DS176", cust.id)

    with pytest.raises(DriveError, match="More than one"):
        run(go())
    assert names_in(fake, "OTHER") == ["DS176 舊備份"]   # nothing was moved


def test_peek_legacy_folder_changes_nothing(drive, monkeypatch):
    fake, d = drive
    monkeypatch.setattr(settings, "google_drive_customers_folder_id", "ROOT")
    fake.add("DS176 甜蜜蜜", "ROOT")
    before = [dict(f) for f in fake.files]
    status, folder = run(d.peek_legacy_folder("DS176", None))
    assert status == "movable" and folder.name == "DS176 甜蜜蜜"
    assert fake.files == before


def test_old_folders_of_another_account_are_ignored(drive):
    """Folders shared from another account (e.g. "NXT Customers Old") are never moved,
    and do not make an owned folder with the same code ambiguous."""
    fake, d = drive
    other = fake.add("DS999", "OLD_DS", owned=False)
    assert run(d.peek_legacy_folder("DS999")) == ("not_found", None)
    mine = fake.add("DS999 ZZ old", "ROOT")
    status, folder = run(d.peek_legacy_folder("DS999"))
    assert status == "movable" and folder.id == mine
    assert other not in [f.id for f in run(d.find_folders_by_code("DS999"))]


# ── Supplier folders ─────────────────────────────────────────────────────────

def _suppliers_tree(fake):
    """A small NXT Suppliers tree: category folders, Potential Suppliers, one coded folder."""
    ids = {"root": "SROOT"}
    for key in ("Parts Suppliers", "Administration Suppliers", "S999 Others"):
        ids[key] = fake.add(key, "SROOT")
    ids["Potential Suppliers"] = fake.add("Potential Suppliers", "SROOT")
    return ids


def test_new_supplier_folder_is_created_under_its_category(drive, monkeypatch):
    fake, d = drive
    monkeypatch.setattr(settings, "google_drive_suppliers_folder_id", "SROOT")
    ids = _suppliers_tree(fake)
    res = run(d.ensure_supplier_folder("parts", "S100", "新零件供應商"))
    assert res.status == "created" and res.warnings == []
    assert res.folder.name == "S100 新零件供應商"
    assert names_in(fake, ids["Parts Suppliers"]) == ["S100 新零件供應商"]
    assert names_in(fake, res.folder.id) == []      # created empty


def test_missing_category_folder_is_created(drive, monkeypatch):
    fake, d = drive
    monkeypatch.setattr(settings, "google_drive_suppliers_folder_id", "SROOT")
    res = run(d.ensure_supplier_folder("marketing", "S101", "某廣告"))
    assert names_in(fake, "SROOT") == ["Marketing Suppliers"]
    assert res.folder.name == "S101 某廣告"


def test_other_category_goes_to_the_s999_folder(drive, monkeypatch):
    fake, d = drive
    monkeypatch.setattr(settings, "google_drive_suppliers_folder_id", "SROOT")
    ids = _suppliers_tree(fake)
    run(d.ensure_supplier_folder("other", "S102", "雜項"))
    assert names_in(fake, ids["S999 Others"]) == ["S102 雜項"]


def test_running_twice_creates_nothing_new(drive, monkeypatch):
    fake, d = drive
    monkeypatch.setattr(settings, "google_drive_suppliers_folder_id", "SROOT")
    _suppliers_tree(fake)
    first = run(d.ensure_supplier_folder("parts", "S100", "新零件供應商"))
    count = len(fake.files)
    second = run(d.ensure_supplier_folder("parts", "S100", "新零件供應商"))
    assert second.folder.id == first.folder.id and second.status == "existing"
    assert len(fake.files) == count


def test_a_prospect_folder_is_moved_and_prefixed(drive, monkeypatch):
    fake, d = drive
    monkeypatch.setattr(settings, "google_drive_suppliers_folder_id", "SROOT")
    ids = _suppliers_tree(fake)
    prospect = fake.add("新零件供應商有限公司", ids["Potential Suppliers"])   # legal form differs
    res = run(d.ensure_supplier_folder("parts", "S100", "新零件供應商"))
    assert res.status == "promoted" and res.folder.id == prospect
    assert res.folder.name == "S100 新零件供應商有限公司"
    assert names_in(fake, ids["Parts Suppliers"]) == ["S100 新零件供應商有限公司"]
    assert names_in(fake, ids["Potential Suppliers"]) == []


def test_a_prospect_in_a_category_potential_subfolder_is_found(drive, monkeypatch):
    fake, d = drive
    monkeypatch.setattr(settings, "google_drive_suppliers_folder_id", "SROOT")
    ids = _suppliers_tree(fake)
    pot = fake.add("Potential", ids["Administration Suppliers"])
    prospect = fake.add("某會計師事務所", pot)
    res = run(d.ensure_supplier_folder("administration", "S103", "某會計師事務所"))
    assert res.status == "promoted" and res.folder.id == prospect
    assert names_in(fake, ids["Administration Suppliers"]) == ["Potential", "S103 某會計師事務所"]


def test_a_promoted_folder_is_found_again_on_retry(drive, monkeypatch):
    fake, d = drive
    monkeypatch.setattr(settings, "google_drive_suppliers_folder_id", "SROOT")
    ids = _suppliers_tree(fake)
    fake.add("新零件供應商", ids["Potential Suppliers"])
    first = run(d.ensure_supplier_folder("parts", "S100", "新零件供應商"))
    second = run(d.ensure_supplier_folder("parts", "S100", "新零件供應商"))
    assert second.status == "existing" and second.folder.id == first.folder.id


def test_several_matching_prospects_move_nothing(drive, monkeypatch):
    fake, d = drive
    monkeypatch.setattr(settings, "google_drive_suppliers_folder_id", "SROOT")
    ids = _suppliers_tree(fake)
    pot = fake.add("Potential", ids["Parts Suppliers"])
    fake.add("甲供應商", ids["Potential Suppliers"])
    fake.add("甲供應商有限公司", pot)
    res = run(d.ensure_supplier_folder("parts", "S100", "甲供應商"))
    assert res.status == "created" and len(res.warnings) == 1 and "none was moved" in res.warnings[0]
    assert names_in(fake, ids["Potential Suppliers"]) == ["甲供應商"]
    assert names_in(fake, pot) == ["甲供應商有限公司"]


def test_coded_folders_are_never_prospects(drive, monkeypatch):
    fake, d = drive
    monkeypatch.setattr(settings, "google_drive_suppliers_folder_id", "SROOT")
    ids = _suppliers_tree(fake)
    fake.add("S059 甲供應商", ids["Potential Suppliers"])   # coded: not a prospect
    res = run(d.ensure_supplier_folder("parts", "S100", "甲供應商"))
    assert res.status == "created"
    assert names_in(fake, ids["Potential Suppliers"]) == ["S059 甲供應商"]


def test_an_existing_coded_folder_is_reused_untouched(drive, monkeypatch):
    fake, d = drive
    monkeypatch.setattr(settings, "google_drive_suppliers_folder_id", "SROOT")
    ids = _suppliers_tree(fake)
    old = fake.add("SA002 某事務所", ids["Administration Suppliers"])
    res = run(d.ensure_supplier_folder("parts", "SA002", "某事務所新名"))   # other category, new name
    assert res.status == "existing" and res.folder.id == old and res.folder.name == "SA002 某事務所"
    assert names_in(fake, ids["Administration Suppliers"]) == ["SA002 某事務所"]


def test_two_folders_with_the_same_code_are_an_error(drive, monkeypatch):
    fake, d = drive
    monkeypatch.setattr(settings, "google_drive_suppliers_folder_id", "SROOT")
    ids = _suppliers_tree(fake)
    fake.add("S058 上億國際", ids["Parts Suppliers"])
    fake.add("S058 エスポ化学", ids["Parts Suppliers"])
    with pytest.raises(DriveError, match="More than one folder matches S058"):
        run(d.ensure_supplier_folder("parts", "S058", "上億國際"))


def test_unknown_category_and_missing_root(drive, monkeypatch):
    fake, d = drive
    monkeypatch.setattr(settings, "google_drive_suppliers_folder_id", "SROOT")
    with pytest.raises(Exception, match="Category must be one of"):
        run(d.ensure_supplier_folder("japan", "S100", "X"))
    monkeypatch.setattr(settings, "google_drive_suppliers_folder_id", "")
    with pytest.raises(DriveError, match="GOOGLE_DRIVE_SUPPLIERS_FOLDER_ID"):
        run(d.ensure_supplier_folder("parts", "S100", "X"))


def test_names_with_chinese_and_ampersand_are_kept(drive, monkeypatch):
    fake, d = drive
    monkeypatch.setattr(settings, "google_drive_suppliers_folder_id", "SROOT")
    ids = _suppliers_tree(fake)
    res = run(d.ensure_supplier_folder("parts", "S104", "R&D 研發/測試"))
    assert res.folder.name == "S104 R&D 研發_測試"      # "/" is replaced, "&" and 中文 kept
