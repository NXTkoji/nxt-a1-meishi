"""
Pydantic schemas for Claude's parsed card output.

These are the intermediate representation between OCR/AI output and the ORM
models. Each value carries a confidence score (0.0–1.0) so the UI can highlight
low-confidence fields for user review.

Confidence thresholds:
  >= 0.90  → high (auto-accept)
  0.70–0.89 → medium (show but don't flag)
  < 0.70   → low (highlight for user review)
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------

class CF(BaseModel):
    """A string value with an associated confidence score."""
    value: str
    confidence: float = 1.0  # 0.0–1.0


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

class ParsedName(BaseModel):
    language: str                       # ja, zh, zh-TW, en, ko
    name_type: str = "primary"          # primary, romanized, nickname, former
    family_name: Optional[CF] = None
    given_name: Optional[CF] = None
    full_name: CF


# ---------------------------------------------------------------------------
# Organization names (one per language)
# ---------------------------------------------------------------------------

class ParsedOrgName(BaseModel):
    language: str
    name: CF


# ---------------------------------------------------------------------------
# Position (links a person to an org, with multilingual title/dept)
# ---------------------------------------------------------------------------

class ParsedPositionDetail(BaseModel):
    language: str
    title: Optional[CF] = None
    department: Optional[CF] = None


class ParsedPosition(BaseModel):
    org_names: List[ParsedOrgName] = Field(default_factory=list)
    details: List[ParsedPositionDetail] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Contact details
# ---------------------------------------------------------------------------

class ParsedContactDetail(BaseModel):
    # phone_work, phone_mobile, phone_fax, email_work, email_personal,
    # address_work, address_home, url_website, social_wechat, social_line,
    # social_linkedin, social_other
    detail_type: str
    value: CF
    label: Optional[str] = None         # original label printed on card (e.g. "携帯", "TEL")
    country_code: Optional[str] = None  # ISO 3166-1 alpha-2 for address_* types


# ---------------------------------------------------------------------------
# Top-level parsed card
# ---------------------------------------------------------------------------

class ParsedCard(BaseModel):
    names: List[ParsedName] = Field(default_factory=list)
    positions: List[ParsedPosition] = Field(default_factory=list)
    contact_details: List[ParsedContactDetail] = Field(default_factory=list)
    # Date printed on the card (e.g. exchange date stamp), YYYY-MM-DD or None
    card_date: Optional[str] = None
    notes: Optional[str] = None
    # Languages detected on the card
    languages_detected: List[str] = Field(default_factory=list)
    # Average confidence across all extracted fields
    overall_confidence: float = 1.0


# ---------------------------------------------------------------------------
# Match result (from contact_matcher)
# ---------------------------------------------------------------------------

class MatchResult(BaseModel):
    is_existing: bool = False
    person_id: Optional[int] = None           # local DB persons.id (internal)
    person_external_id: Optional[str] = None  # local DB persons.external_id (UUID)
    match_confidence: float = 0.0
    match_method: Optional[str] = None    # email, phone, name_exact, name_fuzzy
    matched_name: Optional[str] = None
