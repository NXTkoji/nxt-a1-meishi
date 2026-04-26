"""
SQLAlchemy ORM models for NXT-A1 名片整理器.

Design principles:
- One record per real-world entity (person, organization).
- All multilingual fields use separate *_names tables (append-only history).
- Images are never stored in SQLite — only paths on disk.
- Nothing is written to persons/cards until the user confirms a scan.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.utcnow()


# ---------------------------------------------------------------------------
# My Companies  (the user's own companies — used to tag which company a card
#               was received on behalf of)
# ---------------------------------------------------------------------------

class MyCompany(Base):
    __tablename__ = "my_companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    # Odoo credentials (nullable — shared account uses top-level .env creds)
    odoo_url: Mapped[Optional[str]] = mapped_column(String(512))
    odoo_db: Mapped[Optional[str]] = mapped_column(String(128))
    odoo_username: Mapped[Optional[str]] = mapped_column(String(256))
    # Google Contacts label applied to contacts from this company
    google_label: Mapped[Optional[str]] = mapped_column(String(128))
    # CSV column config for Odoo import (JSON text)
    odoo_csv_config: Mapped[Optional[str]] = mapped_column(Text)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    # Relationships
    card_links: Mapped[List["CardMyCompany"]] = relationship(
        back_populates="my_company", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# Persons
# ---------------------------------------------------------------------------

class Person(Base):
    __tablename__ = "persons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(
        String(36), default=_uuid, unique=True, nullable=False
    )
    google_resource: Mapped[Optional[str]] = mapped_column(String(128))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    # Relationships
    names: Mapped[List["PersonName"]] = relationship(
        back_populates="person", cascade="all, delete-orphan", order_by="PersonName.id"
    )
    positions: Mapped[List["Position"]] = relationship(
        back_populates="person", cascade="all, delete-orphan"
    )
    contact_details: Mapped[List["ContactDetail"]] = relationship(
        back_populates="person", cascade="all, delete-orphan"
    )
    cards: Mapped[List["Card"]] = relationship(back_populates="person")
    # Relationships FROM this person to others
    relationships_from: Mapped[List["PersonRelationship"]] = relationship(
        foreign_keys="PersonRelationship.from_person_id",
        back_populates="from_person",
        cascade="all, delete-orphan",
    )
    # Relationships TO this person from others
    relationships_to: Mapped[List["PersonRelationship"]] = relationship(
        foreign_keys="PersonRelationship.to_person_id",
        back_populates="to_person",
    )


class PersonName(Base):
    """
    Append-only name history for a person.
    valid_to=None means this is the current name for that language/type.
    Multiple languages can be current simultaneously.
    """
    __tablename__ = "person_names"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False)   # ja, zh, zh-TW, en, ko
    # primary=most prominent on card, romanized=phonetic/romaji, nickname, former
    name_type: Mapped[str] = mapped_column(String(32), nullable=False, default="primary")
    family_name: Mapped[Optional[str]] = mapped_column(String(128))
    given_name: Mapped[Optional[str]] = mapped_column(String(128))
    honorific: Mapped[Optional[str]] = mapped_column(String(64))
    full_name: Mapped[str] = mapped_column(String(256), nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False, default=date.today)
    valid_to: Mapped[Optional[date]] = mapped_column(Date)
    # card = from OCR, manual = entered by user, google_sync = from Google
    source: Mapped[str] = mapped_column(String(32), default="card")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    person: Mapped["Person"] = relationship(back_populates="names")


# ---------------------------------------------------------------------------
# Organizations
# ---------------------------------------------------------------------------

class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(
        String(36), default=_uuid, unique=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    names: Mapped[List["OrganizationName"]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
        order_by="OrganizationName.id",
    )
    positions: Mapped[List["Position"]] = relationship(back_populates="organization")


class OrganizationName(Base):
    """Append-only multilingual name history for an organization."""
    __tablename__ = "organization_names"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False, default=date.today)
    valid_to: Mapped[Optional[date]] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(32), default="card")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    organization: Mapped["Organization"] = relationship(back_populates="names")


# ---------------------------------------------------------------------------
# Positions  (Person ↔ Organization, many-to-many with attributes)
# ---------------------------------------------------------------------------

class Position(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), nullable=False)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    # current or historical
    status: Mapped[str] = mapped_column(String(16), default="current", nullable=False)
    started_on: Mapped[Optional[date]] = mapped_column(Date)
    ended_on: Mapped[Optional[date]] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    person: Mapped["Person"] = relationship(back_populates="positions")
    organization: Mapped["Organization"] = relationship(back_populates="positions")
    details: Mapped[List["PositionDetail"]] = relationship(
        back_populates="position", cascade="all, delete-orphan"
    )


class PositionDetail(Base):
    """Multilingual title and department for a Position."""
    __tablename__ = "position_details"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String(256))
    department: Mapped[Optional[str]] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    __table_args__ = (
        UniqueConstraint("position_id", "language", name="uq_position_detail_lang"),
    )

    position: Mapped["Position"] = relationship(back_populates="details")


# ---------------------------------------------------------------------------
# Person → Person relationships  (directional)
# ---------------------------------------------------------------------------

class RelationshipType(Base):
    """
    Predefined types: introduced_by, colleague, reports_to, referred_by,
    friend, family_member, mentor, investor, advisor, client, supplier.
    Users can add custom types (is_predefined=False).
    """
    __tablename__ = "relationship_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Machine-readable key, e.g. "introduced_by"
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # Human-readable label, e.g. "Introduced by"
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    is_predefined: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    relationships: Mapped[List["PersonRelationship"]] = relationship(
        back_populates="relationship_type"
    )


class PersonRelationship(Base):
    """
    Directional: from_person_id --[relationship]--> to_person_id
    e.g. "田中 was introduced_by 山田"
         from=田中, to=山田, relationship=introduced_by
    """
    __tablename__ = "person_relationships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    from_person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), nullable=False)
    to_person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), nullable=False)
    relationship_type_id: Mapped[int] = mapped_column(
        ForeignKey("relationship_types.id"), nullable=False
    )
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    __table_args__ = (
        UniqueConstraint(
            "from_person_id", "to_person_id", "relationship_type_id",
            name="uq_person_relationship",
        ),
    )

    from_person: Mapped["Person"] = relationship(
        foreign_keys=[from_person_id], back_populates="relationships_from"
    )
    to_person: Mapped["Person"] = relationship(
        foreign_keys=[to_person_id], back_populates="relationships_to"
    )
    relationship_type: Mapped["RelationshipType"] = relationship(
        back_populates="relationships"
    )


# ---------------------------------------------------------------------------
# Countries  (managed list for consistent naming)
# ---------------------------------------------------------------------------

class Country(Base):
    __tablename__ = "countries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(4), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ---------------------------------------------------------------------------
# Occasions  (groups 2+ cards from the same event)
# ---------------------------------------------------------------------------

class Occasion(Base):
    __tablename__ = "occasions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    event_date: Mapped[Optional[date]] = mapped_column(Date)
    location: Mapped[Optional[str]] = mapped_column(String(512))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    cards: Mapped[List["Card"]] = relationship(back_populates="occasion")


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------

class Card(Base):
    __tablename__ = "cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Used as the image folder name on disk and OneDrive
    external_id: Mapped[str] = mapped_column(
        String(36), default=_uuid, unique=True, nullable=False
    )
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), nullable=False)
    occasion_id: Mapped[Optional[int]] = mapped_column(ForeignKey("occasions.id"))
    received_date: Mapped[Optional[date]] = mapped_column(Date)
    received_location: Mapped[Optional[str]] = mapped_column(String(512))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    # Odoo partner ID after sync (per nxta.co account)
    odoo_partner_id: Mapped[Optional[int]] = mapped_column(Integer)
    # pending, synced, error
    sync_status: Mapped[str] = mapped_column(String(16), default="pending")
    google_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    odoo_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # Preferred language for the person name shown on the collection thumbnail (en, ja, zh…)
    # None = auto (first available name)
    display_name_language: Mapped[Optional[str]] = mapped_column(String(16))
    # Soft delete
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    person: Mapped["Person"] = relationship(back_populates="cards")
    occasion: Mapped[Optional["Occasion"]] = relationship(back_populates="cards")
    sides: Mapped[List["CardSide"]] = relationship(
        back_populates="card",
        cascade="all, delete-orphan",
        order_by="CardSide.side_order",
    )
    my_company_links: Mapped[List["CardMyCompany"]] = relationship(
        back_populates="card", cascade="all, delete-orphan"
    )
    sync_history: Mapped[List["CardSyncHistory"]] = relationship(
        back_populates="card",
        cascade="all, delete-orphan",
        order_by="CardSyncHistory.synced_at.desc()",
    )


class CardSide(Base):
    """One physical side of a business card — one image file."""
    __tablename__ = "card_sides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id"), nullable=False)
    # 0=front, 1=back, 2+ for folded multi-page cards
    side_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Relative path under settings.images_path, e.g. "{card_external_id}/0.jpg"
    image_path: Mapped[str] = mapped_column(String(512), nullable=False)
    image_filename: Mapped[str] = mapped_column(String(256), nullable=False)
    onedrive_url: Mapped[Optional[str]] = mapped_column(String(1024))
    width_px: Mapped[Optional[int]] = mapped_column(Integer)
    height_px: Mapped[Optional[int]] = mapped_column(Integer)
    # SHA-256 of original image bytes (used to deduplicate corrections)
    image_hash: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    __table_args__ = (
        UniqueConstraint("card_id", "side_order", name="uq_card_side_order"),
    )

    card: Mapped["Card"] = relationship(back_populates="sides")


class CardMyCompany(Base):
    """Which of the user's companies a card was received on behalf of (many-to-many)."""
    __tablename__ = "card_my_companies"

    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id"), primary_key=True)
    my_company_id: Mapped[int] = mapped_column(
        ForeignKey("my_companies.id"), primary_key=True
    )

    card: Mapped["Card"] = relationship(back_populates="my_company_links")
    my_company: Mapped["MyCompany"] = relationship(back_populates="card_links")


# ---------------------------------------------------------------------------
# Contact Details  (phones, emails, addresses, websites, social — per person)
# ---------------------------------------------------------------------------

class ContactDetail(Base):
    """
    All contact information for a person, accumulated across cards.
    detail_type values:
      phone_work, phone_mobile, phone_fax
      email_work, email_personal
      address_work, address_home
      url_website
      social_wechat, social_line, social_linkedin, social_other
    """
    __tablename__ = "contact_details"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), nullable=False)
    # Which card introduced this detail (nullable for manually entered details)
    card_id: Mapped[Optional[int]] = mapped_column(ForeignKey("cards.id"))
    detail_type: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[str] = mapped_column(String(1024), nullable=False)
    # Original label printed on the card (e.g., "携帯", "TEL", "FAX")
    label: Mapped[Optional[str]] = mapped_column(String(64))
    # ISO 3166-1 alpha-2 country code for address_* details (e.g. "JP", "US")
    country_code: Mapped[Optional[str]] = mapped_column(String(4))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    person: Mapped["Person"] = relationship(back_populates="contact_details")


# ---------------------------------------------------------------------------
# Scan Sessions  (transient — exists only during the scan+review flow)
# ---------------------------------------------------------------------------

class ScanSession(Base):
    """
    A batch scan session. Created when the user opens the Scan page and starts
    dropping images. Committed (cards created) or abandoned.
    """
    __tablename__ = "scan_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str] = mapped_column(
        String(36), default=_uuid, unique=True, nullable=False
    )
    # uploading → grouping → analyzing → review → done / abandoned
    status: Mapped[str] = mapped_column(String(16), default="uploading")
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    images: Mapped[List["ScanSessionImage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class ScanSessionImage(Base):
    """
    Raw uploaded image before the user groups it into a card.
    Stored in settings.temp_path / session.external_id / filename.
    """
    __tablename__ = "scan_session_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("scan_sessions.id"), nullable=False
    )
    # Relative path under settings.temp_path
    image_path: Mapped[str] = mapped_column(String(512), nullable=False)
    image_filename: Mapped[str] = mapped_column(String(256), nullable=False)
    # UUID assigned by user during grouping; all images with the same temp_card_id
    # form one card. NULL = not yet grouped.
    temp_card_id: Mapped[Optional[str]] = mapped_column(String(36))
    side_order: Mapped[Optional[int]] = mapped_column(Integer)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    session: Mapped["ScanSession"] = relationship(back_populates="images")


# ---------------------------------------------------------------------------
# Field Corrections  (few-shot learning log)
# ---------------------------------------------------------------------------

class FieldCorrection(Base):
    """
    Logged whenever the user corrects Claude's field assignment.
    These are fed back as few-shot examples in future prompts.
    """
    __tablename__ = "field_corrections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    card_id: Mapped[Optional[int]] = mapped_column(ForeignKey("cards.id"))
    # Dot-path to the field, e.g. "names[0].language", "positions[0].title"
    field_path: Mapped[str] = mapped_column(String(128), nullable=False)
    claude_value: Mapped[Optional[str]] = mapped_column(Text)
    user_value: Mapped[str] = mapped_column(Text, nullable=False)
    # field_value, language_detection, field_assignment, merge_decision
    correction_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # SHA-256 of card front image — prevents duplicate corrections for same card
    card_image_hash: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ---------------------------------------------------------------------------
# Card Sync History  (one row per export event per card per destination)
# ---------------------------------------------------------------------------

class CardSyncHistory(Base):
    """
    Records every manual export event.
    destination values: odoo, google_contacts, onedrive
    result values: created, updated, error
    """
    __tablename__ = "card_sync_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id"), nullable=False)
    destination: Mapped[str] = mapped_column(String(32), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    result: Mapped[str] = mapped_column(String(16), nullable=False)  # created, updated, error
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    card: Mapped["Card"] = relationship(back_populates="sync_history")
