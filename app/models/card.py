from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class PersonName(BaseModel):
    value: str = ""
    language: str = ""  # ja, zh, zh-TW, en, ko
    type: str = ""  # primary, romanized, chinese, nickname


class Position(BaseModel):
    company: str = ""
    company_english: str = ""
    department: str = ""
    title: str = ""
    title_english: str = ""


class Phone(BaseModel):
    value: str = ""
    type: str = ""  # work, mobile, fax
    label: str = ""  # TEL, 携帯, FAX, etc.


class Email(BaseModel):
    value: str = ""
    type: str = "work"


class Address(BaseModel):
    type: str = "work"
    full: str = ""
    postal_code: str = ""
    country: str = ""
    country_code: str = ""
    state: str = ""
    city: str = ""
    street: str = ""


class Social(BaseModel):
    linkedin: str = ""
    wechat: str = ""
    line: str = ""


class Person(BaseModel):
    names: list[PersonName] = Field(default_factory=list)
    positions: list[Position] = Field(default_factory=list)
    phones: list[Phone] = Field(default_factory=list)
    emails: list[Email] = Field(default_factory=list)
    addresses: list[Address] = Field(default_factory=list)
    website: str = ""
    social: Social = Field(default_factory=Social)


class MatchResult(BaseModel):
    is_existing: bool = False
    matched_contact_id: Optional[str] = None
    match_confidence: float = 0.0
    match_source: Optional[str] = None  # "odoo" or "google"
    matched_name: Optional[str] = None


class CardImages(BaseModel):
    card_front: Optional[str] = None  # base64
    card_back: Optional[str] = None
    person_photo: Optional[str] = None


class Card(BaseModel):
    id: str = ""
    scanned_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    received_date: str = ""  # date card was received (user can override)
    notes: str = ""
    images: CardImages = Field(default_factory=CardImages)
    person: Person = Field(default_factory=Person)
    match: MatchResult = Field(default_factory=MatchResult)
