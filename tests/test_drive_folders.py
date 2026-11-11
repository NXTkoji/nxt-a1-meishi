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
            parent = re.search(r"'([^']+)' in parents", q).group(1)
            hits = [f for f in self.files if parent in f["parents"] and not f["trashed"]]
            return httpx.Response(200, json={"files": [{"id": f["id"], "name": f["name"]} for f in hits]})
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
