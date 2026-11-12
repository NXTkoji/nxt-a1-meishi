"""
Odoo access for project code issuing.

Layout: the customer is the COMPANY partner (`company_registry`, labelled "Company ID"
in Odoo, = Customer ID; `vat` = 統編). `ref` ("Reference") holds the user's short
names and is never read or written here;
people are child contacts (`parent_id`), created by the card export. Projects are
`project.project` records with the x_* fields created on 2026-11-11.

All calls here are blocking XML-RPC. Async callers should run them with
asyncio.to_thread. Tests pass a fake `obj` exposing `execute_kw`.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, List, Optional

from app.services import odoo_sync
from app.services.project_codes import CUSTOMER_CODE_RE, normalize_org_name, strip_legal_forms

logger = logging.getLogger(__name__)

SEQUENCE_CODE = "nxt.customer"
TAG_CUSTOMER = "NXT Customer"
TAG_RESELLER = "NXT Reseller"
TAG_END_USER = "NXT End User"
ROLE_TAGS = {"reseller": TAG_RESELLER, "end_user": TAG_END_USER}

# Odoo's "Company ID" field. It also holds older identifiers (DS127, SA028, JFE), so only
# a value shaped like a Customer ID (C0201) counts as one.
CUSTOMER_ID_FIELD = "company_registry"


def customer_id_of(record: dict) -> Optional[str]:
    """The record's Customer ID, or None when its Company ID is empty or an old code."""
    value = (record.get(CUSTOMER_ID_FIELD) or "").strip()
    return value if CUSTOMER_CODE_RE.match(value) else None


def old_code_of(record: dict) -> Optional[str]:
    """An older identifier in the Company ID field (DS127, SA028, JFE), else None."""
    value = (record.get(CUSTOMER_ID_FIELD) or "").strip()
    return value if value and not CUSTOMER_CODE_RE.match(value) else None

# Company details filled from the card's work contacts (see company_details.py);
# read back so an existing company only gets the ones still empty in Odoo
DETAIL_FIELDS = ["x_studio_name_2nd_language", "street", "country_id", "phone", "x_fax", "website"]
COMPANY_FIELDS = ["name", CUSTOMER_ID_FIELD, "vat", "x_drive_url", "x_legacy_codes", "category_id"] + DETAIL_FIELDS


class OdooSetupError(RuntimeError):
    """Something the Odoo setup should provide is missing (sequence, field)."""


class OdooDataError(RuntimeError):
    """Odoo data breaks an assumption (for example, two companies share a Customer ID)."""


class OdooCodes:
    def __init__(self, obj, uid: int):
        self.obj = obj
        self.uid = uid

    @classmethod
    def connect(cls) -> "OdooCodes":
        _, obj, uid = odoo_sync._get_odoo_clients()
        return cls(obj, uid)

    def _call(self, model: str, method: str, *args, **kwargs):
        return odoo_sync._call(self.obj, self.uid, model, method, *args, **kwargs)

    # -- customer ids -------------------------------------------------------

    def peek_next_customer_id(self) -> str:
        """The Customer ID the sequence would hand out next, WITHOUT consuming it.
        A preview only: the real number is drawn on approval and can differ."""
        rows = self._call("ir.sequence", "search_read",
                          [[["code", "=", SEQUENCE_CODE]]],
                          {"fields": ["prefix", "padding", "number_next_actual"], "limit": 1})
        if not rows:
            raise OdooSetupError(f"Odoo sequence {SEQUENCE_CODE!r} does not exist")
        row = rows[0]
        return f"{row['prefix'] or ''}{int(row['number_next_actual']):0{int(row['padding'])}d}"

    def draw_customer_id(self) -> str:
        """Consume and return the next Customer ID (atomic inside Odoo)."""
        value = self._call("ir.sequence", "next_by_code", [SEQUENCE_CODE])
        if not value:
            raise OdooSetupError(f"Odoo sequence {SEQUENCE_CODE!r} did not return a value")
        return str(value)

    # -- company partners ---------------------------------------------------

    def find_company(self, *, customer_id: Optional[str] = None,
                     vat: Optional[str] = None) -> Optional[dict]:
        """Find the company partner by Customer ID first, then by 統編. None if absent."""
        attempts = []
        if customer_id:
            attempts.append((CUSTOMER_ID_FIELD, "=", customer_id))
        if vat:
            # Odoo may hold the 統編 with or without a "TW" prefix
            attempts.append(("vat", "in", [vat, f"TW{vat}"]))
        for field, op, value in attempts:
            rows = self._call("res.partner", "search_read",
                              [[["is_company", "=", True], [field, op, value]]],
                              {"fields": COMPANY_FIELDS, "limit": 2,
                               "context": {"active_test": False}})
            if len(rows) > 1:
                label = "Company ID" if field == CUSTOMER_ID_FIELD else "統編"
                raise OdooDataError(f"More than one Odoo company has {label} {value!r}")
            if rows:
                return rows[0]
        return None

    def find_companies_by_name(self, name: str) -> List[dict]:
        """Company partners whose name matches `name` ignoring case, width, spacing
        and legal form. Used to adopt a company created earlier by a card export
        instead of creating a second one."""
        core = strip_legal_forms(name) or name
        ids = self._call("res.partner", "search", [
            ["&", ["is_company", "=", True], "|", ["name", "=", name], ["name", "ilike", core]]
        ])
        if not ids:
            return []
        wanted = normalize_org_name(name)
        rows = self._call("res.partner", "read", [ids], {"fields": COMPANY_FIELDS})
        return [r for r in rows if normalize_org_name(r["name"]) == wanted]

    def country_id(self, code: str) -> Optional[int]:
        """Odoo res.country id for an ISO code such as "TW"; None if unknown."""
        if not code:
            return None
        found = self._call("res.country", "search", [[["code", "=", code.upper()]]], {"limit": 1})
        return found[0] if found else None

    def ensure_tag_ids(self, names: Iterable[str]) -> List[int]:
        ids: List[int] = []
        for name in names:
            found = self._call("res.partner.category", "search", [[["name", "=", name]]], {"limit": 1})
            ids.append(found[0] if found else self._call("res.partner.category", "create", [{"name": name}]))
        return ids

    def create_company(self, vals: dict, tag_names: Iterable[str] = ()) -> int:
        vals = dict(vals, is_company=True)
        tags = self.ensure_tag_ids(tag_names)
        if tags:
            vals["category_id"] = [(6, 0, tags)]
        partner_id = self._call("res.partner", "create", [vals])
        logger.info("Created Odoo company %d (%s)", partner_id, vals.get(CUSTOMER_ID_FIELD))
        return partner_id

    def read_company(self, partner_id: int) -> dict:
        return self._call("res.partner", "read", [[partner_id]], {"fields": COMPANY_FIELDS})[0]

    def update_company(self, partner_id: int, vals: dict, add_tag_names: Iterable[str] = ()) -> None:
        vals = dict(vals)
        tags = self.ensure_tag_ids(add_tag_names)
        if tags:
            vals["category_id"] = [(4, tag_id) for tag_id in tags]
        if vals:
            self._call("res.partner", "write", [[partner_id], vals])

    # -- projects -----------------------------------------------------------

    def project_codes_for(self, customer_ref: str) -> List[str]:
        """Every project code already used for this customer (archived included)."""
        rows = self._call("project.project", "search_read",
                          [[["x_project_code", "=like", f"{customer_ref}-%"]]],
                          {"fields": ["x_project_code"], "context": {"active_test": False}})
        return [r["x_project_code"] for r in rows if r.get("x_project_code")]

    def find_project(self, code: str) -> Optional[dict]:
        rows = self._call("project.project", "search_read",
                          [[["x_project_code", "=", code]]],
                          {"fields": ["name", "x_project_code", "x_drive_url"], "limit": 1,
                           "context": {"active_test": False}})
        return rows[0] if rows else None

    def create_project(self, vals: dict) -> int:
        project_id = self._call("project.project", "create", [vals])
        logger.info("Created Odoo project %d (%s)", project_id, vals.get("x_project_code"))
        return project_id

    def update_project(self, project_id: int, vals: dict) -> None:
        self._call("project.project", "write", [[project_id], vals])
