"""
Request / Response Pydantic models for the v2 API layer.
Separate from app/schemas/parsed_card.py (Claude output) and
app/db/models.py (ORM) — these are the wire-format contracts.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.schemas.parsed_card import MatchResult, ParsedCard


# ---------------------------------------------------------------------------
# Scan Sessions
# ---------------------------------------------------------------------------

class SessionCreate(BaseModel):
    notes: Optional[str] = None


class SessionImageOut(BaseModel):
    id: int
    image_filename: str
    temp_card_id: Optional[str]
    side_order: Optional[int]
    uploaded_at: datetime

    model_config = {"from_attributes": True}


class SessionOut(BaseModel):
    id: int
    external_id: str
    status: str
    notes: Optional[str]
    created_at: datetime
    images: List[SessionImageOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class ImageGroupUpdate(BaseModel):
    temp_card_id: str           # UUID string assigned by frontend
    side_order: int             # 0=front, 1=back, 2+


# ---------------------------------------------------------------------------
# Confirm (session → permanent records)
# ---------------------------------------------------------------------------

class CardDraft(BaseModel):
    """One card to be confirmed from a session."""
    temp_card_id: str
    parsed: ParsedCard          # ParsedCard after user review/edits
    match_person_id: Optional[int] = None   # existing persons.id to merge into
    my_company_ids: List[int] = Field(default_factory=list)
    occasion_id: Optional[int] = None
    received_date: Optional[date] = None
    notes: Optional[str] = None


class ConfirmRequest(BaseModel):
    cards: List[CardDraft]


class ConfirmedCardOut(BaseModel):
    temp_card_id: str
    card_id: int
    card_external_id: str
    person_id: int
    person_external_id: str


class ConfirmResponse(BaseModel):
    confirmed: List[ConfirmedCardOut]


# ---------------------------------------------------------------------------
# Analysis (SSE payload types)
# ---------------------------------------------------------------------------

class AnalysisProgress(BaseModel):
    type: str = "progress"      # "progress" | "result" | "error" | "done"
    temp_card_id: Optional[str] = None
    message: Optional[str] = None
    parsed: Optional[ParsedCard] = None
    match: Optional[MatchResult] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

class CardSideOut(BaseModel):
    side_order: int
    image_path: str
    image_filename: str
    width_px: Optional[int]
    height_px: Optional[int]

    model_config = {"from_attributes": True}


class CardOut(BaseModel):
    id: int
    external_id: str
    person_id: int
    person_external_id: Optional[str] = None
    occasion_id: Optional[int]
    received_date: Optional[date]
    received_location: Optional[str]
    notes: Optional[str]
    display_name_language: Optional[str] = None
    sync_status: str
    created_at: datetime
    sides: List[CardSideOut] = Field(default_factory=list)
    my_company_ids: List[int] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class CardListItem(BaseModel):
    id: int
    external_id: str
    person_id: int
    received_date: Optional[date]
    sync_status: str
    created_at: datetime
    # Denormalized for list view
    person_name: Optional[str] = None
    front_image_path: Optional[str] = None
    # Destinations that have a successful sync record (most recent per destination)
    synced_destinations: List[str] = Field(default_factory=list)

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Persons
# ---------------------------------------------------------------------------

class PersonNameOut(BaseModel):
    id: int
    language: str
    name_type: str
    family_name: Optional[str]
    given_name: Optional[str]
    honorific: Optional[str]
    full_name: str
    is_current: bool
    valid_from: date
    valid_to: Optional[date]
    source: str

    model_config = {"from_attributes": True}


class ContactDetailOut(BaseModel):
    id: int
    detail_type: str
    value: str
    label: Optional[str]
    country_code: Optional[str] = None
    is_primary: bool

    model_config = {"from_attributes": True}


class PositionDetailOut(BaseModel):
    id: int
    language: str
    title: Optional[str]
    department: Optional[str]

    model_config = {"from_attributes": True}


class OrgNameOut(BaseModel):
    id: int
    language: str
    name: str
    is_current: bool

    model_config = {"from_attributes": True}


class PositionOut(BaseModel):
    id: int
    org_id: int
    status: str
    org_names: List[OrgNameOut] = Field(default_factory=list)
    details: List[PositionDetailOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class PersonOut(BaseModel):
    id: int
    external_id: str
    notes: Optional[str]
    birthday: Optional[str] = None  # "YYYY-MM-DD" or "--MM-DD" (year unknown)
    created_at: datetime
    updated_at: datetime
    names: List[PersonNameOut] = Field(default_factory=list)
    contact_details: List[ContactDetailOut] = Field(default_factory=list)
    positions: List[PositionOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class PersonListItem(BaseModel):
    id: int
    external_id: str
    primary_name: Optional[str] = None
    family_name: Optional[str] = None
    country_code: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class PersonCreate(BaseModel):
    names: List[dict]           # [{language, name_type, family_name, given_name, full_name}]
    notes: Optional[str] = None


class PersonUpdate(BaseModel):
    # Person-level fields editable after creation. birthday: "YYYY-MM-DD",
    # "--MM-DD" (year unknown), or "" to clear.
    birthday: Optional[str] = None


# ---------------------------------------------------------------------------
# Organizations
# ---------------------------------------------------------------------------

class OrgOut(BaseModel):
    id: int
    external_id: str
    created_at: datetime
    names: List[OrgNameOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class OrgListItem(BaseModel):
    id: int
    external_id: str
    primary_name: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Countries
# ---------------------------------------------------------------------------

class CountryCreate(BaseModel):
    code: str
    name: str


class CountryUpdate(BaseModel):
    name: Optional[str] = None


class CountryOut(BaseModel):
    id: int
    code: str
    name: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Occasions
# ---------------------------------------------------------------------------

class OccasionCreate(BaseModel):
    name: str
    event_date: Optional[date] = None
    location: Optional[str] = None
    notes: Optional[str] = None


class OccasionUpdate(BaseModel):
    name: Optional[str] = None
    event_date: Optional[date] = None
    location: Optional[str] = None
    notes: Optional[str] = None


class OccasionOut(BaseModel):
    id: int
    name: str
    event_date: Optional[date]
    location: Optional[str]
    notes: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Corrections
# ---------------------------------------------------------------------------

class CorrectionCreate(BaseModel):
    card_id: Optional[int] = None
    field_path: str
    claude_value: Optional[str] = None
    user_value: str
    correction_type: str
    card_image_hash: Optional[str] = None


class CorrectionOut(BaseModel):
    id: int
    field_path: str
    correction_type: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# My Companies
# ---------------------------------------------------------------------------

class MyCompanyOut(BaseModel):
    id: int
    name: str
    google_label: Optional[str]
    notes: Optional[str]

    model_config = {"from_attributes": True}


class MyCompanyCreate(BaseModel):
    name: str
    notes: Optional[str] = None


class MyCompanyUpdate(BaseModel):
    name: Optional[str] = None
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

class ExportRequest(BaseModel):
    card_external_ids: List[str]
    destinations: List[str]  # e.g. ["odoo", "google_contacts"]


class ExportResultItem(BaseModel):
    card_external_id: str
    destination: str
    result: str          # created, updated, error
    error_message: Optional[str] = None


class ExportResponse(BaseModel):
    results: List[ExportResultItem]


# ---------------------------------------------------------------------------
# Person Merge
# ---------------------------------------------------------------------------

class MergeRequest(BaseModel):
    source_ids: List[str]  # external_ids of persons to be merged INTO primary


class MergeResult(BaseModel):
    person: PersonOut
    duplicate_contact_count: int
