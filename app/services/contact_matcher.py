"""
Contact matcher: find existing Person records in the local SQLite DB that
match a freshly parsed card.

Match hierarchy (first hit above threshold wins):
  1. Email exact match          → confidence 0.97
  2. Phone normalized match     → confidence 0.90
  3. Name exact (normalized)    → confidence 0.85
  4. Name partial / CJK trigram → confidence 0.60–0.75

All matching is done against the local DB first. Google Contacts is NOT
queried here — it is only consulted at Confirm time to pull in the
google_resource ID if a new person is being created.

Returns MatchResult with the matched persons.id (DB primary key) so the
UI can show which existing record this new card would merge into.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import List, Optional, Tuple

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ContactDetail, Person, PersonName
from app.schemas.parsed_card import MatchResult, ParsedCard

logger = logging.getLogger(__name__)

# Confidence thresholds
THRESH_EMAIL   = 0.97
THRESH_PHONE   = 0.90
THRESH_NAME_EX = 0.85
THRESH_NAME_FZ = 0.60
REPORT_MINIMUM = 0.55   # below this we return no match


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def _normalize_phone(phone: str) -> str:
    """Keep only digits and leading +."""
    digits = re.sub(r"[^\d+]", "", phone)
    # Remove country code prefix variations for JP: 81 → 0
    if digits.startswith("+81"):
        digits = "0" + digits[3:]
    elif digits.startswith("0081"):
        digits = "0" + digits[4:]
    return digits


def _normalize_name(name: str) -> str:
    """Lowercase + NFKC normalize + strip whitespace."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", name).lower().strip())


def _cjk_trigrams(text: str) -> set:
    """Build character-level 2-grams from CJK text (spaces already stripped)."""
    cleaned = re.sub(r"[^\u3000-\u9fff\uac00-\ud7af]", "", text)
    if len(cleaned) < 2:
        return set()
    return {cleaned[i : i + 2] for i in range(len(cleaned) - 1)}


def _trigram_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    ta, tb = _cjk_trigrams(a), _cjk_trigrams(b)
    if not ta or not tb:
        # Fall back to substring containment for short CJK names
        return 0.70 if (a in b or b in a) else 0.0
    intersection = ta & tb
    union = ta | tb
    return len(intersection) / len(union) if union else 0.0


# ---------------------------------------------------------------------------
# DB query helpers
# ---------------------------------------------------------------------------

async def _find_by_email(db: AsyncSession, emails: List[str]) -> Optional[Tuple[int, str]]:
    """Return (person_id, matched_email) for first exact email match."""
    for email in emails:
        email_lower = email.lower().strip()
        if not email_lower:
            continue
        row = await db.scalar(
            select(ContactDetail.person_id).where(
                ContactDetail.detail_type.in_(["email_work", "email_personal"]),
                ContactDetail.value == email_lower,
            )
        )
        if row:
            return row, email_lower
    return None


async def _find_by_phone(db: AsyncSession, phones: List[str]) -> Optional[Tuple[int, str]]:
    """Return (person_id, matched_phone) for first normalized phone match."""
    for phone in phones:
        normalized = _normalize_phone(phone)
        if len(normalized) < 7:
            continue
        # Fetch all phone-type contact details and compare normalized
        rows = await db.execute(
            select(ContactDetail.person_id, ContactDetail.value).where(
                ContactDetail.detail_type.in_(
                    ["phone_work", "phone_mobile", "phone_fax"]
                )
            )
        )
        for person_id, stored_value in rows:
            if _normalize_phone(stored_value) == normalized:
                return person_id, phone
    return None


async def _find_by_name(
    db: AsyncSession, names: List[str]
) -> Optional[Tuple[int, str, float]]:
    """Return (person_id, matched_name, confidence) for best name match."""
    normalized_queries = [(_normalize_name(n), n) for n in names if n.strip()]
    if not normalized_queries:
        return None

    # Fetch all current person names
    rows = await db.execute(
        select(PersonName.person_id, PersonName.full_name).where(
            PersonName.is_current == True  # noqa: E712
        )
    )
    all_names: List[Tuple[int, str]] = list(rows)

    best: Optional[Tuple[int, str, float]] = None

    for norm_q, orig_q in normalized_queries:
        for person_id, stored_full in all_names:
            norm_s = _normalize_name(stored_full)

            # Exact normalized match
            if norm_q == norm_s:
                return person_id, orig_q, THRESH_NAME_EX

            # CJK trigram similarity
            sim = _trigram_similarity(norm_q, norm_s)
            if sim > 0:
                conf = THRESH_NAME_FZ + (THRESH_NAME_EX - THRESH_NAME_FZ) * sim
                if best is None or conf > best[2]:
                    best = (person_id, orig_q, conf)

            # Latin substring containment
            if norm_q and norm_s:
                if norm_q in norm_s or norm_s in norm_q:
                    conf = 0.70
                    if best is None or conf > best[2]:
                        best = (person_id, orig_q, conf)

    return best


async def _display_name(db: AsyncSession, person_id: int) -> str:
    name = await db.scalar(
        select(PersonName.full_name)
        .where(PersonName.person_id == person_id, PersonName.is_current == True)  # noqa: E712
        .order_by(PersonName.id.asc())
        .limit(1)
    )
    return name or f"person:{person_id}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def find_match(db: AsyncSession, card: ParsedCard) -> MatchResult:
    """
    Search the local DB for a Person matching the parsed card.

    Returns MatchResult.is_existing=False if no match above threshold.
    """
    # Collect candidate values from the parsed card
    emails = [
        cd.value.value
        for cd in card.contact_details
        if cd.detail_type.startswith("email") and cd.value.value
    ]
    phones = [
        cd.value.value
        for cd in card.contact_details
        if cd.detail_type.startswith("phone") and cd.value.value
    ]
    names = [n.full_name.value for n in card.names if n.full_name.value]

    # 1. Email
    hit = await _find_by_email(db, emails)
    if hit:
        person_id, matched = hit
        display = await _display_name(db, person_id)
        logger.info("Email match: %s → person %d", matched, person_id)
        return MatchResult(
            is_existing=True,
            person_id=person_id,
            match_confidence=THRESH_EMAIL,
            match_method="email",
            matched_name=display,
        )

    # 2. Phone
    hit = await _find_by_phone(db, phones)
    if hit:
        person_id, matched = hit
        display = await _display_name(db, person_id)
        logger.info("Phone match: %s → person %d", matched, person_id)
        return MatchResult(
            is_existing=True,
            person_id=person_id,
            match_confidence=THRESH_PHONE,
            match_method="phone",
            matched_name=display,
        )

    # 3. Name
    hit = await _find_by_name(db, names)
    if hit:
        person_id, matched, conf = hit
        if conf >= REPORT_MINIMUM:
            display = await _display_name(db, person_id)
            method = "name_exact" if conf >= THRESH_NAME_EX else "name_fuzzy"
            logger.info(
                "Name match: '%s' → person %d (%.0f%%, %s)",
                matched, person_id, conf * 100, method,
            )
            return MatchResult(
                is_existing=True,
                person_id=person_id,
                match_confidence=conf,
                match_method=method,
                matched_name=display,
            )

    return MatchResult()
