"""Drive folder creation, against a small fake of the Drive v3 REST API."""
import asyncio
import json
import re

import httpx
import pytest

from app.config import settings
from app.services import drive_folders as df
from app.services.drive_folders import DriveError, DriveFolders, PROJECT_SUBFOLDERS


class FakeDrive:
    def __init__(self):
        self.files: list[dict] = []
        self._n = 0
        self.fail_next: list[int] = []   # status codes to return before succeeding
        self.requests = 0

    def add(self, name, parent):
        self._n += 1
        f = {"id": f"id{self._n}", "name": name, "parents": [parent], "trashed": False}
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
                {"id": f["id"], "name": f["name"], "parents": f["parents"]} for f in hits]})
        if request.method == "PATCH":
            params = request.url.params
            f = next(f for f in self.files if f["id"] == request.url.path.rsplit("/", 1)[1])
            remove = [x for x in params.get("removeParents", "").split(",") if x]
            f["parents"] = [x for x in f["parents"] if x not in remove] + [params["addParents"]]
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
        proj = await d.ensure_project_tree(cust.id, "C0200-DS01", "台北店")
        return cust, proj

    cust, proj = run(go())
    assert cust.name == "C0200 甜蜜蜜台北店"
    assert names_in(fake, "ROOT") == ["C0200 甜蜜蜜台北店"]
    assert names_in(fake, cust.id) == ["C0200-DS01 台北店"]
    assert names_in(fake, proj.id) == sorted(PROJECT_SUBFOLDERS)


def test_running_twice_creates_nothing_new(drive):
    fake, d = drive

    async def once():
        cust = await d.ensure_customer_folder("C0200", "甜蜜蜜", parent_id="ROOT")
        return await d.ensure_project_tree(cust.id, "C0200-DS01", "台北店")

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
    ref = run(d.ensure_folder("ROOT", "1 photos"))
    assert ref.name == "1 photos" and fake.requests >= 3


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
    assert names_in(fake, cust.id) == ["DS176 甜蜜蜜台北店"]


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
