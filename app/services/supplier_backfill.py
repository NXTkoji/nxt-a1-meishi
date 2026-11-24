"""Backfill of existing suppliers into the party-code model (spec: Backfill).

Two steps, both safe to repeat:

* `build_plan` reads the Odoo partners of the company in scope and the Drive supplier tree and
  writes nothing. It returns rows, each saying what would be done and whether it is automatic
  (`apply`), needs a person's decision (`confirm`) or is only reported (`skip`).
* `apply_plan` performs the rows whose decision is `apply`, re-reading each Odoo record first,
  so a second run changes nothing.

Only a code match (the Company ID or Supplier code equals the folder's code) is automatic.
Drive folders carry no tax ID, so a name match is always a candidate for a person to confirm.
Partners of other Odoo companies are counted and never read further. Local SQLite is not
touched: it is a cache, and the app adopts these Odoo records when a card matches them.
"""
from __future__ import annotations

import csv
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field, fields
from typing import Dict, Iterable, List, Optional

from app.services import supplier_codes
from app.services.drive_folders import DriveFolders, FolderRef
from app.services.odoo_codes import CUSTOMER_ID_FIELD, OdooCodes
from app.services.project_codes import normalize_org_name

logger = logging.getLogger(__name__)

LEGACY_FOLDER = "Japan Suppliers"       # kept for the existing Japanese suppliers; no category
_INACTIVE_FOLDER = re.compile(r"not in use|inactive", re.I)   # coded folders inside these are inactive
_CODE_AT_START = re.compile(r"^(S[ACFIMP]?\d{3,})(?!\d)", re.I)

# Decisions
APPLY, CONFIRM, SKIP, CREATE = "apply", "confirm", "skip", "create"

PARTNER_FIELDS = ["name", CUSTOMER_ID_FIELD, "vat", "parent_id", "company_id", "customer_rank",
                  "x_supplier_code", "x_supplier_status", "x_customer_status",
                  "x_supplier_drive_url", "category_id"]


# ---------------------------------------------------------------------------
# Plan rows and the CSV report
# ---------------------------------------------------------------------------

@dataclass
class Row:
    rule: str                 # spec rule number, "1", "2", "3", "4" or "5"
    action: str               # set_supplier, link_name_match, create_company, set_potential, conflict
    decision: str             # apply, confirm or skip (create: on a link row, create a new company)
    name: str = ""
    code: str = ""
    category: str = ""
    status: str = ""          # the supplier status the row would set
    partner_id: Optional[int] = None
    registry: str = ""        # the partner's Company ID today
    folder_id: str = ""
    folder_path: str = ""
    folder_url: str = ""
    note: str = ""

    @property
    def row_id(self) -> str:
        return f"{self.action}:{self.partner_id or ''}:{self.folder_id}:{self.rule}"


@dataclass
class Plan:
    rows: List[Row] = field(default_factory=list)
    counts: Dict[str, int] = field(default_factory=dict)

    def add(self, row: Row) -> None:
        self.rows.append(row)


CSV_COLUMNS = ["row_id", "decision"] + [f.name for f in fields(Row)]


def write_report(plan: Plan, path: str, results: Optional[Dict[str, str]] = None) -> None:
    columns = CSV_COLUMNS + (["result"] if results is not None else [])
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in plan.rows:
            data = {f.name: getattr(row, f.name) for f in fields(Row)}
            data["row_id"] = row.row_id
            if results is not None:
                data["result"] = results.get(row.row_id, "")
            writer.writerow(data)


def read_decisions(path: str) -> Dict[str, str]:
    """row_id -> decision from a report a person has edited."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        found = {r["row_id"]: (r.get("decision") or "").strip().lower() for r in csv.DictReader(fh)}
    bad = {d for d in found.values() if d not in (APPLY, CONFIRM, SKIP, CREATE)}
    if bad:
        raise ValueError(f"Decision must be apply, confirm, skip or create; found {sorted(bad)}")
    return found


# ---------------------------------------------------------------------------
# Drive tree
# ---------------------------------------------------------------------------

@dataclass
class FolderEntry:
    id: str
    name: str
    path: tuple
    category: Optional[str]       # category key; None for Japan Suppliers and Potential Suppliers
    code: Optional[str]
    place: str                    # coded, potential (a prospect location), uncoded, in_container
    status: str = "active"        # a coded folder inside a "not in use" / "inactive" folder is inactive

    @property
    def url(self) -> str:
        return f"https://drive.google.com/drive/folders/{self.id}"

    @property
    def clean_name(self) -> str:
        return _clean_name(self.name)


def code_of(folder_name: str) -> Optional[str]:
    match = _CODE_AT_START.match((folder_name or "").strip())
    return match.group(1).upper() if match else None


def _clean_name(folder_name: str) -> str:
    code = code_of(folder_name)
    text = (folder_name or "").strip()
    return text[len(code):].strip(" -_") if code else text


def _entry(ref: FolderRef, path: Iterable[str], category: Optional[str], uncoded_place: str,
           status: str = "active") -> FolderEntry:
    code = code_of(ref.name)
    return FolderEntry(ref.id, ref.name, tuple(path), category, code, "coded" if code else uncoded_place, status)


async def scan_drive(drive: DriveFolders, root_id: str) -> List[FolderEntry]:
    """Supplier folders under the suppliers root: the category folders, their "Potential"
    subfolders, "Potential Suppliers", and one more level inside an uncoded folder that holds
    coded ones (the regional folders under Construction Suppliers)."""
    by_name = {c.drive_folder: c.key for c in supplier_codes.CATEGORIES}
    entries: List[FolderEntry] = []
    for top in await drive._list_children(root_id):
        if top.name == supplier_codes.POTENTIAL_FOLDER:
            for child in await drive._list_children(top.id):
                entries.append(_entry(child, [top.name], None, "potential"))
        elif top.name in by_name or top.name == LEGACY_FOLDER:
            key = by_name.get(top.name)
            for child in await drive._list_children(top.id):
                if code_of(child.name):
                    entries.append(_entry(child, [top.name], key, "uncoded"))
                    continue
                grandchildren = await drive._list_children(child.id)
                if child.name == supplier_codes.POTENTIAL_SUBFOLDER:
                    entries.extend(_entry(g, [top.name, child.name], key, "potential") for g in grandchildren)
                elif any(code_of(g.name) for g in grandchildren):     # a regional container
                    status = "inactive" if _INACTIVE_FOLDER.search(child.name) else "active"
                    entries.extend(_entry(g, [top.name, child.name], key, "in_container", status)
                                   for g in grandchildren)
                else:
                    entries.append(_entry(child, [top.name], key, "uncoded"))
    return entries


# ---------------------------------------------------------------------------
# Odoo
# ---------------------------------------------------------------------------

def _read_partners(odoo: OdooCodes) -> tuple:
    """(partners of this company and shared ones, number of partners of other companies)."""
    scope = odoo._partner_scope()
    rows = odoo._call("res.partner", "search_read", [scope + [["is_company", "=", True]]],
                      {"fields": PARTNER_FIELDS})
    others = []
    if odoo.company_id:
        others = odoo._call("res.partner", "search", [[["is_company", "=", True],
                                                       ["company_id", "!=", odoo.company_id],
                                                       ["company_id", "!=", False]]])
    return rows, len(others)


def _company_of(partner: dict) -> Optional[int]:
    value = partner.get("company_id")
    return (value[0] if isinstance(value, (list, tuple)) else value) or None


def _parent_of(partner: dict) -> Optional[int]:
    value = partner.get("parent_id")
    return (value[0] if isinstance(value, (list, tuple)) else value) or None


def _is_customer(partner: dict) -> bool:
    return bool(partner.get("x_customer_status")) or (partner.get("customer_rank") or 0) > 0


def _registry_code(partner: dict) -> Optional[str]:
    value = (partner.get(CUSTOMER_ID_FIELD) or "").strip().upper()
    return value if supplier_codes.is_supplier_code(value) else None


def _held_code(partner: dict) -> Optional[str]:
    return (partner.get("x_supplier_code") or "").strip().upper() or None


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------

def _category_name(key: Optional[str]) -> str:
    return key or ""


def plan_rows(partners: List[dict], other_company_count: int, entries: List[FolderEntry]) -> Plan:
    plan = Plan(counts={"odoo_partners_in_scope": len(partners),
                        "odoo_partners_of_other_companies": other_company_count,
                        "drive_folders": len(entries)})
    by_id = {p["id"]: p for p in partners}

    # every partner that holds a supplier code, by that code
    holders: Dict[str, List[dict]] = defaultdict(list)
    for p in partners:
        for code in {_registry_code(p), _held_code(p)} - {None}:
            holders[code].append(p)

    def distinct_holders(code: str) -> List[dict]:
        """Holders of a code, leaving out a branch whose parent holds it too (not a conflict)."""
        found = holders.get(code, [])
        ids = {p["id"] for p in found}
        return [p for p in found if _parent_of(p) not in ids]

    folders_by_code: Dict[str, List[FolderEntry]] = defaultdict(list)
    for e in entries:
        if e.code:
            folders_by_code[e.code].append(e)

    names: Dict[str, List[dict]] = defaultdict(list)
    for p in partners:
        names[normalize_org_name(p["name"])].append(p)

    handled_partner_ids: set = set()

    def conflict(note: str, *, e: Optional[FolderEntry] = None, p: Optional[dict] = None, code: str = "") -> None:
        plan.add(Row(rule="5", action="conflict", decision=SKIP, code=code,
                     name=(p["name"] if p else e.clean_name if e else ""),
                     partner_id=p["id"] if p else None,
                     registry=(p.get(CUSTOMER_ID_FIELD) or "") if p else "",
                     folder_id=e.id if e else "", folder_path="/".join(e.path + (e.name,)) if e else "",
                     folder_url=e.url if e else "", note=note))

    # -- coded Drive folders -------------------------------------------------
    for code, folders in sorted(folders_by_code.items()):
        held = distinct_holders(code)
        if len(folders) > 1 or len(held) > 1:
            for e in folders:
                conflict(f"Code {code} is on {len(folders)} folder(s) and {len(held)} Odoo company(ies)", e=e, code=code)
            for p in held:
                conflict(f"Code {code} is on {len(folders)} folder(s) and {len(held)} Odoo company(ies)", p=p, code=code)
                handled_partner_ids.add(p["id"])
            continue
        e = folders[0]
        category = _category_name(e.category)
        if held:
            p = held[0]
            handled_partner_ids.add(p["id"])
            if _held_code(p) == code and p.get("x_supplier_status") in ("active", "inactive") \
                    and _registry_code(p) is None and (p.get("x_supplier_drive_url") or ""):
                plan.counts["already_done"] = plan.counts.get("already_done", 0) + 1
                continue
            decision, note = APPLY, ""
            if _company_of(p) is None:
                decision, note = CONFIRM, "Shared partner (no company): visible to every company"
            elif _is_customer(p) and _registry_code(p):
                decision, note = CONFIRM, "Customer whose Company ID looks like a supplier code"
            plan.add(Row(rule="1", action="set_supplier", decision=decision, name=p["name"], code=code,
                         category=category, status=e.status, partner_id=p["id"],
                         registry=p.get(CUSTOMER_ID_FIELD) or "", folder_id=e.id,
                         folder_path="/".join(e.path + (e.name,)), folder_url=e.url, note=note))
            continue
        candidates = [p for p in names.get(normalize_org_name(e.clean_name), []) if not _held_code(p)]
        if candidates:
            for p in candidates:
                handled_partner_ids.add(p["id"])
                plan.add(Row(rule="1", action="link_name_match", decision=CONFIRM, name=p["name"], code=code,
                             category=category, status=e.status, partner_id=p["id"],
                             registry=p.get(CUSTOMER_ID_FIELD) or "", folder_id=e.id,
                             folder_path="/".join(e.path + (e.name,)), folder_url=e.url,
                             note="Name match only. Set decision to apply to link, create for a new company, "
                                  "skip to leave"))
        else:
            plan.add(Row(rule="2", action="create_company", decision=APPLY, name=e.clean_name, code=code,
                         category=category, status=e.status, folder_id=e.id,
                         folder_path="/".join(e.path + (e.name,)), folder_url=e.url,
                         note="Coded folder with no Odoo company"))

    # -- supplier-shaped Company ID with no Drive folder -----------------------
    for p in partners:
        code = _registry_code(p)
        if not code or p["id"] in handled_partner_ids or code in folders_by_code:
            continue
        held = distinct_holders(code)
        if len(held) > 1:
            conflict(f"Code {code} is on {len(held)} Odoo companies", p=p, code=code)
            continue
        if _parent_of(p) and _parent_of(p) in by_id and _registry_code(by_id[_parent_of(p)]) == code:
            continue                                     # a branch: the code sits on the parent
        decision, note = APPLY, "No Drive folder with this code"
        if _company_of(p) is None:
            decision, note = CONFIRM, "Shared partner (no company); no Drive folder with this code"
        elif _is_customer(p):
            decision, note = CONFIRM, "Customer whose Company ID looks like a supplier code; no Drive folder"
        plan.add(Row(rule="1", action="set_supplier", decision=decision, name=p["name"], code=code,
                     status="active", partner_id=p["id"], registry=p.get(CUSTOMER_ID_FIELD) or "", note=note))

    # -- uncoded folders: prospects and the ones to decide ---------------------
    for e in entries:
        if e.code:
            continue
        category = _category_name(e.category)
        auto = e.place == "potential"
        rule = "3" if auto else "4"
        path = "/".join(e.path + (e.name,))
        candidates = [p for p in names.get(normalize_org_name(e.clean_name), [])]
        if candidates:
            for p in candidates:
                if p.get("x_supplier_status"):
                    plan.counts["already_done"] = plan.counts.get("already_done", 0) + 1
                    continue
                plan.add(Row(rule=rule, action="set_potential", decision=CONFIRM, name=p["name"],
                             category=category, status="potential", partner_id=p["id"],
                             registry=p.get(CUSTOMER_ID_FIELD) or "", folder_id=e.id, folder_path=path,
                             folder_url=e.url, note="Existing company with the same name; name match only"))
            continue
        plan.add(Row(rule=rule, action="create_company", decision=APPLY if auto else CONFIRM, name=e.clean_name,
                     category=category, status="potential", folder_id=e.id, folder_path=path, folder_url=e.url,
                     note="" if auto else f"Uncoded folder ({e.place.replace('_', ' ')}); default potential"))
    return plan


async def build_plan(odoo: OdooCodes, drive: DriveFolders, root_id: str) -> Plan:
    """Read Odoo and Drive and work out the rows. Writes nothing."""
    import asyncio
    partners, others = await asyncio.to_thread(_read_partners, odoo)
    entries = await scan_drive(drive, root_id)
    return plan_rows(partners, others, entries)


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def _tag_names(row: Row) -> List[str]:
    return [supplier_codes.category(row.category).odoo_tag] if row.category else []


def _apply_set_supplier(odoo: OdooCodes, row: Row) -> str:
    current = odoo.read_company(row.partner_id)
    held = _held_code(current)
    if held and held != row.code:
        return f"failed: company already has supplier code {held}"
    other = odoo.find_company(supplier_code=row.code)
    if other and other["id"] != row.partner_id:
        return f"failed: supplier code {row.code} is already on company {other['id']}"
    vals: dict = {}
    if not held:
        vals["x_supplier_code"] = row.code
    wanted = row.status or "active"
    if current.get("x_supplier_status") != wanted:
        vals["x_supplier_status"] = wanted
    if row.folder_url and not current.get("x_supplier_drive_url"):
        vals["x_supplier_drive_url"] = row.folder_url
    if _registry_code(current) == row.code:
        vals[CUSTOMER_ID_FIELD] = False          # Company ID holds only customer IDs from now on
    tags = _tag_names(row)
    has_tag = False
    if tags:
        tag_ids = odoo.ensure_tag_ids(tags)
        has_tag = all(t in (current.get("category_id") or []) for t in tag_ids)
    if not vals and (not tags or has_tag):
        return "unchanged"
    odoo.update_company(row.partner_id, vals, add_tag_names=[] if has_tag else tags)
    return "updated"


def _apply_set_potential(odoo: OdooCodes, row: Row) -> str:
    current = odoo.read_company(row.partner_id)
    if current.get("x_supplier_status"):
        return "unchanged"
    vals = {"x_supplier_status": "potential"}
    if row.folder_url and not current.get("x_supplier_drive_url"):
        vals["x_supplier_drive_url"] = row.folder_url
    odoo.update_company(row.partner_id, vals, add_tag_names=_tag_names(row))
    return "updated"


def _apply_create(odoo: OdooCodes, row: Row) -> str:
    if row.code:
        existing = odoo.find_company(supplier_code=row.code)
        if existing:
            return f"unchanged: company {existing['id']} already has {row.code}"
    elif odoo.find_companies_by_name(row.name):
        return "unchanged: a company with this name already exists"
    vals = {"name": row.name, "x_supplier_status": row.status}
    if row.code:
        vals["x_supplier_code"] = row.code
    if row.folder_url:
        vals["x_supplier_drive_url"] = row.folder_url
    odoo.create_company(vals, tag_names=_tag_names(row))
    return "created"


def apply_plan(odoo: OdooCodes, plan: Plan, decisions: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Do the rows whose decision is apply (or create, on a name-match row). row_id -> result."""
    results: Dict[str, str] = {}
    for row in plan.rows:
        decision = (decisions or {}).get(row.row_id, row.decision)
        if decision not in (APPLY, CREATE) or row.action == "conflict":
            results[row.row_id] = f"not applied ({decision})"
            continue
        try:
            action = "create_company" if decision == CREATE else row.action
            if action == "create_company":
                results[row.row_id] = _apply_create(odoo, row)
            elif action == "set_potential":
                results[row.row_id] = _apply_set_potential(odoo, row)
            else:
                results[row.row_id] = _apply_set_supplier(odoo, row)
        except Exception as exc:                          # one bad row never stops the others
            logger.exception("Backfill row %s failed", row.row_id)
            results[row.row_id] = f"failed: {exc}"
    return results
