"""
Project code issuing for NXT customers: validation, code formats, and matching an
entered customer against organizations already in the local database.

See docs/superpowers/specs/2026-11-11-project-code-issuing-design.md.

Code formats
  Customer ID   C0200            (permanent, drawn from Odoo's `nxt.customer` sequence)
  Project code  C0200-DS01       (<customer>-<product line><nn>)

Matching is local-DB only. Looking up customers that exist only in Odoo is done by
the Odoo client; the propose step merges both.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ContactDetail, Organization, OrganizationName, Position
from app.services.contact_matcher import (
    _normalize_name,
    _normalize_phone,
    _trigram_similarity,
)

# DS = SH200 deodorization, DJ = NXT-JM oil removal. "P" is retired as a prefix:
# resellers are an Odoo tag plus "sold via" on the project.
PRODUCT_LINES = ("DS", "DJ")

CUSTOMER_CODE_RE = re.compile(r"^C\d{4,}$")
# Old codes: DS###, DJ###, P###, and reseller sites P###S##
LEGACY_CODE_RE = re.compile(r"^(?:DS|DJ|P)\d{3}(?:S\d{2})?$")
PROJECT_CODE_RE = re.compile(r"^(C\d{4,})-([A-Z]{2})(\d{2,})$")

# Confidence for each kind of match. Only "auto" matches may be reused without the
# user choosing; fuzzy name matches are shown as candidates and never auto-merged.
CONF_TAX_ID = 1.0
CONF_PHONE = 0.9
CONF_NAME_EXACT = 0.85
CONF_NAME_FUZZY_MIN = 0.6


class ValidationError(ValueError):
    """Raised for input the user can correct (bad 統編, unknown product line)."""


# ---------------------------------------------------------------------------
# Validation and normalization
# ---------------------------------------------------------------------------

_TAX_ID_WEIGHTS = (1, 2, 1, 2, 1, 2, 4, 1)


def validate_tax_id(value: str) -> str:
    """Return the cleaned 8-digit 統一編號, or raise ValidationError.

    Checksum: multiply the digits by 1,2,1,2,1,2,4,1, add the digits of each
    product, and the total must be a multiple of 5 (the Ministry of Finance
    widened this from 10 to 5 in 2023; every older number is also a multiple of
    5). When the 7th digit is 7, a total that is one short of a multiple of 5 is
    also accepted, because 7 x 4 = 28 can count as 10 or 1.
    """
    digits = re.sub(r"\D", "", value or "")
    if len(digits) != 8:
        raise ValidationError("統一編號 must be 8 digits")
    total = 0
    for d, w in zip(digits, _TAX_ID_WEIGHTS):
        product = int(d) * w
        total += product // 10 + product % 10
    if total % 5 == 0 or (digits[6] == "7" and (total + 1) % 5 == 0):
        return digits
    raise ValidationError("統一編號 failed the checksum; check for a typo")


def normalize_phone(phone: str) -> str:
    """Normalize a phone number for matching.

    Taiwan numbers (+886 / 00886) become the domestic form with a leading 0,
    dropping an optional "(0)" after the country code. Anything else goes through
    the existing card-matcher normalizer (which handles Japan).
    """
    raw = re.sub(r"[^\d+]", "", phone or "")
    if not raw:
        return ""
    for prefix in ("+886", "00886"):
        if raw.startswith(prefix):
            rest = raw[len(prefix):].lstrip("0")
            return "0" + rest if rest else ""
    return _normalize_phone(phone)


def validate_product_line(line: str) -> str:
    code = (line or "").strip().upper()
    if code not in PRODUCT_LINES:
        raise ValidationError(f"Product line must be one of {', '.join(PRODUCT_LINES)}")
    return code


def parse_legacy_codes(value: Optional[str]) -> List[str]:
    """Split "DS176, P065S50" into validated, upper-cased, de-duplicated old codes."""
    codes: List[str] = []
    for raw in re.split(r"[,\s、，;；]+", value or ""):
        code = raw.strip().upper()
        if not code:
            continue
        if not LEGACY_CODE_RE.match(code):
            raise ValidationError(
                f"{raw!r} is not an old code (expected like DS176, DJ005, P065 or P065S50)")
        if code not in codes:
            codes.append(code)
    return codes


def merge_legacy_codes(existing: Optional[str], new: List[str]) -> str:
    """Add codes to a stored ", "-separated list, keeping order and no duplicates."""
    merged = parse_legacy_codes(existing)
    for code in new:
        if code not in merged:
            merged.append(code)
    return ", ".join(merged)


# ---------------------------------------------------------------------------
# Code formats
# ---------------------------------------------------------------------------

def format_project_code(customer_code: str, product_line: str, number: int) -> str:
    if not CUSTOMER_CODE_RE.match(customer_code or ""):
        raise ValidationError(f"Invalid customer code: {customer_code!r}")
    return f"{customer_code}-{validate_product_line(product_line)}{number:02d}"


def parse_project_code(code: str) -> Optional[tuple[str, str, int]]:
    """Split "C0200-DS01" into ("C0200", "DS", 1); None if it does not match."""
    m = PROJECT_CODE_RE.match(code or "")
    if not m:
        return None
    return m.group(1), m.group(2), int(m.group(3))


def next_project_number(existing_codes: List[str], customer_code: str, product_line: str) -> int:
    """Highest existing number for this customer and line, plus 1 (1 if none)."""
    highest = 0
    for code in existing_codes:
        parsed = parse_project_code(code)
        if parsed and parsed[0] == customer_code and parsed[1] == product_line:
            highest = max(highest, parsed[2])
    return highest + 1


# ---------------------------------------------------------------------------
# Matching an entered customer against local organizations
# ---------------------------------------------------------------------------

# Legal-form words that differ between how a company is written on a card and how
# it is registered. They are not part of the identity, so they are ignored when
# comparing names. Longest first so "股份有限公司" is removed before "有限公司".
_CJK_LEGAL_FORMS = (
    "股份有限公司", "有限公司", "股份有限", "企業社", "合同会社", "株式会社",
    "有限会社", "有限會社", "(股)", "(有)",
)
# English forms tolerate spacing and punctuation ("Co., Ltd." / "Co.,Ltd" / "CO LTD").
_EN_LEGAL_FORMS = (
    r"\b(?:co\.?\s*,?\s*ltd\.?|ltd\.?|inc\.?|corp\.?|corporation|limited)(?![a-z0-9])"
)
_LEGAL_FORM_RE = re.compile(
    "|".join(re.escape(f) for f in _CJK_LEGAL_FORMS) + "|" + _EN_LEGAL_FORMS,
    re.IGNORECASE,
)


def strip_legal_forms(name: str) -> str:
    """Remove legal-form words from a name, keeping its spacing (for Odoo searches)."""
    text = unicodedata.normalize("NFKC", name or "")
    return _LEGAL_FORM_RE.sub("", text).strip(" ,.-")


def normalize_org_name(name: str) -> str:
    """Normalize an organization name for comparison (case, width, spaces, legal form)."""
    return _normalize_name(strip_legal_forms(name))


@dataclass
class OrgCandidate:
    org_id: int
    name: str
    customer_code: Optional[str]
    tax_id: Optional[str]
    reason: str          # "tax_id", "phone", "name_exact", "name_fuzzy"
    confidence: float

    @property
    def auto(self) -> bool:
        """True when the match is certain enough to reuse without asking."""
        return self.reason in ("tax_id", "phone")


async def _org_display_name(db: AsyncSession, org_id: int) -> str:
    name = await db.scalar(
        select(OrganizationName.name)
        .where(OrganizationName.org_id == org_id, OrganizationName.is_current == True)  # noqa: E712
        .order_by(OrganizationName.id.asc())
        .limit(1)
    )
    return name or ""


async def match_organization(
    db: AsyncSession,
    *,
    tax_id: Optional[str] = None,
    phone: Optional[str] = None,
    name: Optional[str] = None,
) -> List[OrgCandidate]:
    """Find organizations that may be the same customer, best match first.

    Order of evidence: 統編 exact, phone exact (any person's phone at the
    organization), name exact, name similar. One entry per organization, keeping
    its strongest reason. `tax_id` must already be validated by the caller.
    """
    best: dict[int, OrgCandidate] = {}
    orgs: dict[int, Organization] = {}

    async def _remember(org_id: int, reason: str, confidence: float) -> None:
        current = best.get(org_id)
        if current and current.confidence >= confidence:
            return
        org = orgs.get(org_id) or await db.get(Organization, org_id)
        orgs[org_id] = org
        best[org_id] = OrgCandidate(
            org_id=org_id,
            name=await _org_display_name(db, org_id),
            customer_code=org.customer_code,
            tax_id=org.tax_id,
            reason=reason,
            confidence=confidence,
        )

    if tax_id:
        org = await db.scalar(select(Organization).where(Organization.tax_id == tax_id))
        if org:
            orgs[org.id] = org
            await _remember(org.id, "tax_id", CONF_TAX_ID)

    wanted_phone = normalize_phone(phone) if phone else ""
    if wanted_phone:
        rows = (await db.execute(
            select(Position.org_id, ContactDetail.value)
            .join(ContactDetail, ContactDetail.person_id == Position.person_id)
            .where(ContactDetail.detail_type.like("phone%"))
        )).all()
        for org_id, value in rows:
            if normalize_phone(value) == wanted_phone:
                await _remember(org_id, "phone", CONF_PHONE)

    wanted_name = normalize_org_name(name) if name else ""
    if wanted_name:
        rows = (await db.execute(
            select(OrganizationName.org_id, OrganizationName.name)
        )).all()
        for org_id, org_name in rows:
            normalized = normalize_org_name(org_name)
            if normalized == wanted_name:
                await _remember(org_id, "name_exact", CONF_NAME_EXACT)
                continue
            sim = _trigram_similarity(wanted_name, normalized)
            if sim >= CONF_NAME_FUZZY_MIN:
                await _remember(org_id, "name_fuzzy", min(sim, CONF_NAME_EXACT - 0.01))

    return sorted(best.values(), key=lambda c: c.confidence, reverse=True)
