from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from app.models.card import Card


class ScanResponse(BaseModel):
    status: str = "ok"
    card: Card


class ConfirmResponse(BaseModel):
    status: str = "ok"
    odoo_id: Optional[int] = None
    google_id: Optional[str] = None
    onedrive_urls: dict[str, str] = {}
    errors: list[str] = []


class ContactSearchResult(BaseModel):
    id: str
    name: str
    company: str = ""
    email: str = ""
    phone: str = ""
    source: str  # "odoo" or "google"


class SearchResponse(BaseModel):
    results: list[ContactSearchResult] = []


class ErrorResponse(BaseModel):
    status: str = "error"
    detail: str
