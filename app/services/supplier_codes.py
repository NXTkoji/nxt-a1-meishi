"""
Supplier codes: validation, categories and tax-ID rules for the supplier role.

See docs/superpowers/specs/2026-11-24-party-codes-design.md.

Code formats
  Supplier code (new)   S100, S101, ... S999 is reserved, then S1000 (drawn from Odoo's
                        `nxt.supplier` sequence; the category is a tag and a Drive folder, not part of the code)
  Supplier code (kept)  S059, SA002, SC070 ... existing codes stay as they are

Matching an entered supplier against local organizations reuses
`project_codes.match_organization` (tax ID, phone, name); a supplier code is an extra,
certain reason.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from app.services.project_codes import ValidationError, validate_tax_id

# S###, or the old category forms SA### SC### SF### SI### SM### SP###
SUPPLIER_CODE_RE = re.compile(r"^S[ACFIMP]?\d{3,}$")
# New codes are the bare form: S + digits, no category letter
_NEW_CODE_RE = re.compile(r"^S(\d{3,})$")
# S999 is the name of the "Others" folder; it is never issued as a supplier code.
RESERVED_SUPPLIER_CODES = frozenset({"S999"})
FIRST_SUPPLIER_NUMBER = 100

ROLE_STATUSES = ("potential", "active", "inactive")


@dataclass(frozen=True)
class SupplierCategory:
    key: str
    drive_folder: str   # folder name directly inside the suppliers root
    odoo_tag: str       # Odoo partner tag name


CATEGORIES = (
    SupplierCategory("parts", "Parts Suppliers", "Supplier: Parts"),
    SupplierCategory("administration", "Administration Suppliers", "Supplier: Administration"),
    SupplierCategory("construction", "Construction Suppliers", "Supplier: Construction"),
    SupplierCategory("forwarding", "Forwarding Suppliers", "Supplier: Forwarding"),
    SupplierCategory("inspection", "Inspection Suppliers", "Supplier: Inspection"),
    SupplierCategory("marketing", "Marketing Suppliers", "Supplier: Marketing"),
    SupplierCategory("packaging", "Package Suppliers", "Supplier: Packaging"),
    SupplierCategory("other", "S999 Others", "Supplier: Other"),
)
_BY_KEY = {c.key: c for c in CATEGORIES}
# The folder that holds prospects, directly inside the suppliers root
POTENTIAL_FOLDER = "Potential Suppliers"
POTENTIAL_SUBFOLDER = "Potential"   # inside a category folder


def category(key: Optional[str]) -> SupplierCategory:
    """The category for a key, or ValidationError."""
    found = _BY_KEY.get((key or "").strip().lower())
    if not found:
        raise ValidationError(f"Category must be one of {', '.join(_BY_KEY)}")
    return found


def category_for_tag(tag_name: str) -> Optional[SupplierCategory]:
    return next((c for c in CATEGORIES if c.odoo_tag == tag_name), None)


# ---------------------------------------------------------------------------
# Codes and statuses
# ---------------------------------------------------------------------------

def is_supplier_code(value: Optional[str]) -> bool:
    """True for anything shaped like a supplier code (S059, SA002, S100), issued or kept."""
    return bool(SUPPLIER_CODE_RE.match((value or "").strip().upper()))


def validate_supplier_code(value: str) -> str:
    """Return the upper-cased supplier code, or raise ValidationError. S999 is rejected
    because it is the "Others" folder, not a supplier."""
    code = (value or "").strip().upper()
    if not SUPPLIER_CODE_RE.match(code):
        raise ValidationError(f"{value!r} is not a supplier code (expected like S100, S059 or SA002)")
    if code in RESERVED_SUPPLIER_CODES:
        raise ValidationError(f"{code} is reserved and cannot be a supplier code")
    return code


def format_supplier_code(number: int) -> str:
    """S100 for 100; four digits from 1000. 999 is reserved and refused."""
    code = f"S{number:03d}"
    if number < FIRST_SUPPLIER_NUMBER or code in RESERVED_SUPPLIER_CODES:
        raise ValidationError(f"{code} cannot be issued")
    return code


def supplier_number(code: str) -> Optional[int]:
    """The number in a new-style code (S100 -> 100); None for the old category forms."""
    m = _NEW_CODE_RE.match((code or "").strip().upper())
    return int(m.group(1)) if m else None


def validate_role_status(value: Optional[str]) -> Optional[str]:
    """A role status: None / "" mean none; otherwise potential, active or inactive."""
    status = (value or "").strip().lower()
    if not status or status == "none":
        return None
    if status not in ROLE_STATUSES:
        raise ValidationError(f"Status must be one of none, {', '.join(ROLE_STATUSES)}")
    return status


# ---------------------------------------------------------------------------
# Tax ID by country
# ---------------------------------------------------------------------------

_TAIWAN = {"TW", "TWN", "TAIWAN", "台灣", "台湾", "臺灣"}
_JAPAN = {"JP", "JPN", "JAPAN", "日本"}
_JP_TAX_ID = re.compile(r"^T?(\d{13})$")


def validate_tax_id_for_country(value: Optional[str], country: Optional[str]) -> Optional[str]:
    """Clean a supplier's tax ID by its country; blank means none (returns None).

    Taiwan: 8 digits with checksum (the 統一編號 rule). Japan: 13 digits, written with
    or without the leading T; returned as T + 13 digits. Any other country: free text,
    kept trimmed.
    """
    raw = (value or "").strip()
    if not raw:
        return None
    nation = (country or "").strip().upper()
    if nation in _TAIWAN or (country or "").strip() in _TAIWAN:
        return validate_tax_id(raw)
    if nation in _JAPAN or (country or "").strip() in _JAPAN:
        compact = re.sub(r"[\s-]", "", raw).upper()
        m = _JP_TAX_ID.match(compact)
        if not m:
            raise ValidationError("A Japanese tax number is T followed by 13 digits")
        return "T" + m.group(1)
    return raw[:32]
