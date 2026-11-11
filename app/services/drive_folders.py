"""
Google Drive folder creation for NXT customers and projects.

Layout (see the project-code issuing spec):

    NXT Customers/
      C0200 <customer>/
        finance & logistics/          one per customer, shared by all its projects
        C0200-DS01 <site>/            a customer can have several project folders
          1 requirement / 2 proposal / 3 construction / 4 maintenance
        C0200-DJ01 <site>/

Photos are not a folder of their own; they go into whichever numbered folder they belong to.

Every `ensure_*` method looks for an existing folder first, so re-running a failed
issue never creates a second copy. Customer and project folders are found by their
code prefix, so a later rename of the customer or site does not cause a duplicate.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import List, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_API = "https://www.googleapis.com/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"

PROJECT_SUBFOLDERS = (
    "1 requirement",
    "2 proposal",
    "3 construction",
    "4 maintenance",
)
# Directly inside the customer folder, next to the project folders
CUSTOMER_SUBFOLDERS = (
    "finance & logistics",
)

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 1.0


class DriveError(RuntimeError):
    """A Drive call failed or Drive is not configured."""


@dataclass
class FolderRef:
    id: str
    name: str
    parents: tuple = ()

    @property
    def url(self) -> str:
        return f"https://drive.google.com/drive/folders/{self.id}"


def sanitize_folder_name(name: str) -> str:
    """Make a name safe to use as a folder name (no slashes or colons, tidy spaces)."""
    cleaned = re.sub(r"[\\/:]", "_", name or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:120]


def _q(value: str) -> str:
    """Escape a value for use inside single quotes in a Drive query."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


class DriveFolders:
    def __init__(self, client: httpx.AsyncClient, access_token: str, *, owns_client: bool = False):
        self._client = client
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self._owns_client = owns_client

    @classmethod
    async def from_settings(cls) -> "DriveFolders":
        if not (settings.google_client_id and settings.google_refresh_token):
            raise DriveError("Google Drive is not configured (client id / refresh token missing)")
        client = httpx.AsyncClient(timeout=30.0)
        try:
            resp = await client.post(TOKEN_URL, data={
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "refresh_token": settings.google_refresh_token,
                "grant_type": "refresh_token",
            })
            resp.raise_for_status()
            token = resp.json()["access_token"]
        except Exception:
            await client.aclose()
            raise
        return cls(client, token, owns_client=True)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "DriveFolders":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    # -- low level ----------------------------------------------------------

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        last: Optional[Exception] = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                resp = await self._client.request(
                    method, f"{DRIVE_API}{path}", headers=self._headers, **kwargs)
            except httpx.TransportError as exc:
                last = exc
            else:
                if resp.status_code in _RETRYABLE_STATUS:
                    last = DriveError(f"Drive returned {resp.status_code}")
                elif resp.status_code >= 400:
                    raise DriveError(f"Drive {method} {path} failed: {resp.status_code} {resp.text[:200]}")
                else:
                    return resp.json()
            if attempt < _MAX_ATTEMPTS:
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS * attempt)
        raise DriveError(f"Drive {method} {path} failed after {_MAX_ATTEMPTS} attempts: {last}")

    async def _list_children(self, parent_id: str) -> List[FolderRef]:
        """All non-trashed folders directly inside parent_id."""
        found: List[FolderRef] = []
        page_token: Optional[str] = None
        while True:
            params = {
                "q": f"'{_q(parent_id)}' in parents and mimeType = '{FOLDER_MIME}' and trashed = false",
                "fields": "nextPageToken, files(id, name)",
                "pageSize": 1000,
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if page_token:
                params["pageToken"] = page_token
            data = await self._request("GET", "/files", params=params)
            found.extend(FolderRef(f["id"], f["name"]) for f in data.get("files", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                return found

    async def find_folders_by_code(self, code: str) -> List[FolderRef]:
        """Folders anywhere in this Drive whose name is `code` or starts with "<code> "
        (an old customer or site folder such as "DS176 甜蜜蜜台北店")."""
        found: List[FolderRef] = []
        page_token: Optional[str] = None
        while True:
            params = {
                "q": f"name contains '{_q(code)}' and mimeType = '{FOLDER_MIME}' and trashed = false",
                "fields": "nextPageToken, files(id, name, parents)",
                "pageSize": 100,
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if page_token:
                params["pageToken"] = page_token
            data = await self._request("GET", "/files", params=params)
            found.extend(FolderRef(f["id"], f["name"], tuple(f.get("parents", [])))
                         for f in data.get("files", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return [f for f in found if f.name == code or f.name.startswith(code + " ")]

    async def move_folder(self, folder: FolderRef, new_parent_id: str) -> None:
        await self._request(
            "PATCH", f"/files/{folder.id}",
            params={"addParents": new_parent_id, "removeParents": ",".join(folder.parents),
                    "supportsAllDrives": "true", "fields": "id, parents"},
            json={},
        )
        logger.info("Moved Drive folder %r into %s", folder.name, new_parent_id)

    async def peek_legacy_folder(self, code: str, customer_folder_id: Optional[str] = None) -> tuple:
        """Look up the old folder for `code` without changing anything.
        Returns (status, folder): not_found, already_inside, or movable."""
        matches = [m for m in await self.find_folders_by_code(code)
                   if m.id not in (customer_folder_id, settings.google_drive_customers_folder_id)]
        if not matches:
            return "not_found", None
        if customer_folder_id:
            inside = next((m for m in matches if customer_folder_id in m.parents), None)
            if inside:
                return "already_inside", inside
        if len(matches) > 1:
            names = ", ".join(repr(m.name) for m in matches)
            raise DriveError(f"More than one folder matches {code}: {names}; "
                             "rename or move the extra one, then retry")
        return "movable", matches[0]

    async def move_legacy_folder(self, code: str, customer_folder_id: str) -> tuple:
        """Move the old folder for `code` into the customer folder, keeping its name.
        Returns (status, folder) with status moved, already_inside or not_found."""
        status, folder = await self.peek_legacy_folder(code, customer_folder_id)
        if status == "movable":
            await self.move_folder(folder, customer_folder_id)
            return "moved", folder
        return status, folder

    async def _create_folder(self, parent_id: str, name: str) -> FolderRef:
        data = await self._request(
            "POST", "/files",
            params={"supportsAllDrives": "true", "fields": "id, name"},
            json={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
        )
        logger.info("Created Drive folder %r (%s)", name, data["id"])
        return FolderRef(data["id"], data["name"])

    # -- public -------------------------------------------------------------

    async def ensure_folder(self, parent_id: str, name: str) -> FolderRef:
        """Return the folder called `name` inside parent_id, creating it if missing."""
        name = sanitize_folder_name(name)
        for child in await self._list_children(parent_id):
            if child.name == name:
                return child
        return await self._create_folder(parent_id, name)

    async def ensure_prefixed_folder(self, parent_id: str, code: str, name: str) -> FolderRef:
        """Return the folder whose name starts with `code` (a customer or project code),
        creating "<code> <name>" if there is none. Matching on the code alone keeps a
        renamed customer or site from getting a second folder."""
        for child in await self._list_children(parent_id):
            if child.name == code or child.name.startswith(code + " "):
                return child
        label = sanitize_folder_name(f"{code} {name}".strip())
        return await self._create_folder(parent_id, label)

    async def ensure_customer_folder(self, customer_code: str, name: str,
                                     parent_id: Optional[str] = None) -> FolderRef:
        parent = parent_id or settings.google_drive_customers_folder_id
        if not parent:
            raise DriveError("GOOGLE_DRIVE_CUSTOMERS_FOLDER_ID is not set")
        customer = await self.ensure_prefixed_folder(parent, customer_code, name)
        for sub in CUSTOMER_SUBFOLDERS:
            await self.ensure_folder(customer.id, sub)
        return customer

    async def ensure_project_tree(self, customer_folder_id: str, project_code: str,
                                  site_name: str) -> FolderRef:
        """Create the project folder and its standard subfolders; return the project folder."""
        project = await self.ensure_prefixed_folder(customer_folder_id, project_code, site_name)
        for sub in PROJECT_SUBFOLDERS:
            await self.ensure_folder(project.id, sub)
        return project
