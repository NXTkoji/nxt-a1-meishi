"""
Propose and commit for supplier codes (see docs/superpowers/specs/2026-11-24-party-codes-design.md).

propose_supplier()  Read-only. Works out which organization this is, which supplier
                    code it has or would get, and what approval will do. Creates
                    nothing and consumes no sequence number.
commit_supplier()   Runs the approved request as idempotent steps recorded in an
                    `issue_runs` row (kind "supplier"): local_org, supplier_code,
                    odoo_company, drive_supplier, person. A failed run is resumed
                    with retry_supplier() without creating or drawing anything twice.

An organization that is already a customer is the same Odoo company: the supplier role
adds fields and a folder in the suppliers tree, and touches nothing of the customer's.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, List, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IssueRun, Organization, OrganizationName
from app.schemas.api import (
    CandidateOut, IssueRunOut, SupplierIssueRequest, SupplierProposalOut,
)
from app.services import company_details
from app.services import project_codes as pc
from app.services import supplier_codes as sc
from app.services.drive_folders import DriveError, DriveFolders
from app.services.issue_flow import (
    IssueError, _contains_cjk, _in_thread, _org_by_ext, _org_name,
)
from app.services.odoo_codes import (
    OdooCodes, supplier_code_in_company_id, supplier_code_of,
)

logger = logging.getLogger(__name__)

KIND = "supplier"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _clean_request(req: SupplierIssueRequest) -> tuple[sc.SupplierCategory, Optional[str]]:
    """Validate the user-correctable parts. Returns (category, tax_id)."""
    category = sc.category(req.category)
    tax_id = sc.validate_tax_id_for_country(req.tax_id, req.country)
    return category, tax_id


def _candidate_out(c: pc.OrgCandidate, ext_ids: dict[int, str]) -> CandidateOut:
    return CandidateOut(
        org_ext_id=ext_ids[c.org_id], name=c.name, customer_code=c.customer_code,
        supplier_code=c.supplier_code, tax_id=c.tax_id, reason=c.reason,
        confidence=round(c.confidence, 2), auto=c.auto,
    )


async def _check_tax_id_conflict(db: AsyncSession, org: Optional[Organization],
                                 tax_id: Optional[str]) -> None:
    if not tax_id:
        return
    if org and org.tax_id and org.tax_id != tax_id:
        raise pc.ValidationError(
            f"This organization already has tax ID {org.tax_id}; it does not match {tax_id}")
    other = await db.scalar(select(Organization).where(Organization.tax_id == tax_id))
    if other and (not org or other.id != org.id):
        raise pc.ValidationError(
            f"Tax ID {tax_id} already belongs to another organization ({await _org_name(db, other)})")


@dataclass
class _Target:
    org: Optional[Organization] = None
    needs_choice: bool = False
    candidates: List[pc.OrgCandidate] = field(default_factory=list)


async def _resolve_target(db: AsyncSession, req: SupplierIssueRequest, tax_id: Optional[str],
                          name: str) -> _Target:
    if req.org_ext_id:
        return _Target(org=await _org_by_ext(db, req.org_ext_id))
    if req.chosen_org_ext_id:
        return _Target(org=await _org_by_ext(db, req.chosen_org_ext_id))
    candidates = await pc.match_organization(db, tax_id=tax_id, phone=req.phone, name=name)
    if req.create_new:
        owner = next((c for c in candidates if c.reason == "tax_id"), None)
        if owner:
            raise pc.ValidationError(
                f"Tax ID {tax_id} already belongs to {owner.name}; choose that organization instead")
        return _Target(candidates=candidates)
    autos = [c for c in candidates if c.auto]
    if len(autos) == 1:
        return _Target(org=await db.get(Organization, autos[0].org_id), candidates=candidates)
    if candidates:
        return _Target(needs_choice=True, candidates=candidates)
    return _Target()


def _odoo_supplier_code(company: Optional[dict]) -> Optional[str]:
    """The supplier code an Odoo company already carries: its own field, or (before the
    backfill has moved it) a supplier-shaped value in Company ID."""
    if not company:
        return None
    return supplier_code_of(company) or supplier_code_in_company_id(company)


async def _odoo_company_for(odoo: OdooCodes, org: Optional[Organization], tax_id: Optional[str],
                            name: str) -> tuple[Optional[dict], List[str]]:
    """The Odoo company for this organization, and warnings. By its codes and tax ID first
    (a customer is the same company), then by name when exactly one company has it."""
    warnings: List[str] = []
    company = await _in_thread(
        odoo.find_company,
        customer_id=org.customer_code if org else None,
        supplier_code=org.supplier_code if org else None,
        vat=tax_id or (org.tax_id if org else None))
    if company is None and name:
        same_name = await _in_thread(odoo.find_companies_by_name, name)
        if len(same_name) == 1:
            company = same_name[0]
            warnings.append(f"Odoo already has a company named {company['name']!r}; "
                            "it will be reused rather than duplicated.")
        elif len(same_name) > 1:
            warnings.append(f"Odoo has {len(same_name)} companies named {name!r}; merge them in "
                            "Odoo before approving, or approval will stop.")
    return company, warnings


# ---------------------------------------------------------------------------
# Propose (no side effects)
# ---------------------------------------------------------------------------

async def propose_supplier(db: AsyncSession, odoo: OdooCodes, req: SupplierIssueRequest,
                           drive: Optional[DriveFolders] = None) -> SupplierProposalOut:
    category, tax_id = _clean_request(req)
    if not req.org_ext_id and not req.chosen_org_ext_id and not (req.supplier_name or "").strip():
        raise pc.ValidationError("Supplier name is required")

    target = await _resolve_target(db, req, tax_id, (req.supplier_name or "").strip())
    org = target.org
    await _check_tax_id_conflict(db, org, tax_id)
    name = ((await _org_name(db, org)) if org else "") or (req.supplier_name or "").strip()
    tax_id = tax_id or (org.tax_id if org else None)

    ext_ids = {}
    for c in target.candidates:
        ext_ids[c.org_id] = (await db.get(Organization, c.org_id)).external_id
    out = SupplierProposalOut(
        supplier_mode="new", org_ext_id=org.external_id if org else None, supplier_name=name,
        tax_id=tax_id, category=category.key, category_folder=category.drive_folder,
        needs_choice=target.needs_choice,
        candidates=[_candidate_out(c, ext_ids) for c in target.candidates],
    )
    if target.needs_choice:
        out.warnings.append("Possible existing organizations found; choose one or create a new one.")
        return out

    warnings: List[str] = []
    odoo_company, name_warnings = await _odoo_company_for(odoo, org, tax_id, name)
    warnings.extend(name_warnings)
    code = (org.supplier_code if org else None) or _odoo_supplier_code(odoo_company)
    if code:
        out.supplier_mode = "existing"
        out.supplier_code = code
    else:
        out.suggested_supplier_code = await _in_thread(odoo.peek_next_supplier_code)
        warnings.append("The supplier code is a preview; the final number is assigned on approval.")

    actions: List[str] = []
    shown = code or f"{out.suggested_supplier_code} (preview)"
    actions.append(f"Use existing supplier code {code}" if code
                   else f"Issue supplier code {shown} for {name}")
    if odoo_company is None:
        actions.append("Create the company in Odoo with the supplier code, status Active and the "
                       f"tag {category.odoo_tag}")
    else:
        if odoo_company.get("company_registry") and pc.CUSTOMER_CODE_RE.match(
                (odoo_company["company_registry"] or "").strip()):
            actions.append("This organization is already a customer; its Customer ID and customer "
                           "folder are not changed")
        actions.append(f"Set supplier status Active and add the tag {category.odoo_tag} on the "
                       "Odoo company")
    if org:
        details = await company_details.gather(db, org.id, name, req.card_external_id)
        filled = [label for label, value in [
            ("other names", details.other_names), ("address", details.address),
            ("phone", details.phone), ("fax", details.fax), ("website", details.website)] if value]
        if filled:
            actions.append("Add to the Odoo company where empty: " + ", ".join(filled))
    folder_label = f"{code or out.suggested_supplier_code} {name}".strip()
    if drive is None:
        warnings.append("Google Drive could not be checked now; the folder is decided on approval.")
        actions.append(f"Create or reuse the folder {folder_label!r} in {category.drive_folder}")
    else:
        try:
            existing = await drive.find_folders_by_code(code) if code else []
            prospects = [] if existing else await drive.find_potential_supplier_folders(name)
        except DriveError as exc:
            warnings.append(f"Google Drive: {exc}")
            existing, prospects = [], []
        if existing:
            actions.append(f"Use the existing folder {existing[0].name!r} (not renamed or moved)")
        elif len(prospects) == 1:
            actions.append(f"Move the prospect folder {prospects[0].name!r} into "
                           f"{category.drive_folder} and put the code in front of its name")
        else:
            actions.append(f"Create the folder {folder_label!r} in {category.drive_folder}")
            if len(prospects) > 1:
                warnings.append("More than one prospect folder matches this name ("
                                + ", ".join(repr(p.name) for p in prospects)
                                + "); none will be moved.")
    if req.card_external_id and req.include_person:
        actions.append("Export the person on the card under the company in Odoo")
    out.actions, out.warnings = actions, warnings
    return out


# ---------------------------------------------------------------------------
# Commit (resumable)
# ---------------------------------------------------------------------------

@dataclass
class _Ctx:
    db: AsyncSession
    odoo: OdooCodes
    drive: DriveFolders
    req: SupplierIssueRequest
    run: IssueRun
    category: sc.SupplierCategory
    tax_id: Optional[str]
    name: str
    org: Optional[Organization] = None
    steps: dict = field(default_factory=dict)   # results saved so far (kept across retries)


async def _step_local_org(c: _Ctx) -> dict:
    db, req = c.db, c.req
    org = None
    if c.run.org_id:
        org = await db.get(Organization, c.run.org_id)
    elif req.org_ext_id:
        org = await _org_by_ext(db, req.org_ext_id)
    elif req.chosen_org_ext_id:
        org = await _org_by_ext(db, req.chosen_org_ext_id)
    elif not req.create_new:
        candidates = await pc.match_organization(db, tax_id=c.tax_id, phone=req.phone, name=c.name)
        autos = [x for x in candidates if x.auto]
        if len(autos) == 1:
            org = await db.get(Organization, autos[0].org_id)
        elif candidates:
            raise IssueError("Several organizations match; choose one or create a new one")
    if org is None:
        org = Organization()
        db.add(org)
        await db.flush()
        db.add(OrganizationName(
            org_id=org.id, language="zh" if _contains_cjk(c.name) else "en",
            name=c.name, source="issue"))
    await _check_tax_id_conflict(db, org, c.tax_id)
    if c.tax_id and not org.tax_id:
        org.tax_id = c.tax_id
    org.supplier_category = c.category.key
    c.run.org_id = org.id
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise IssueError(f"Tax ID or organization already exists: {exc.orig}") from exc
    c.org = org
    return {"org_ext_id": org.external_id}


async def _step_supplier_code(c: _Ctx) -> dict:
    org = c.org
    if org.supplier_code:
        org.supplier_status = "active"
        await c.db.commit()
        return {"supplier_code": org.supplier_code, "reused": True}
    # The Odoo company may already carry a code (set by hand, or by an earlier run that
    # stopped after the draw): reuse it instead of drawing again.
    company, _ = await _odoo_company_for(c.odoo, org, c.tax_id, c.name)
    code = _odoo_supplier_code(company)
    if code:
        source = "odoo"
    else:
        code, source = await _in_thread(c.odoo.draw_supplier_code), "drawn"
    code = sc.validate_supplier_code(code)
    org.supplier_code = code
    org.supplier_status = "active"
    try:
        await c.db.commit()   # persist before anything else, so a retry never draws again
    except IntegrityError as exc:
        await c.db.rollback()
        raise IssueError(f"Supplier code {code} already belongs to another organization") from exc
    return {"supplier_code": code, "source": source}


async def _step_odoo_company(c: _Ctx) -> dict:
    org, odoo = c.org, c.odoo
    code = org.supplier_code
    tags = [c.category.odoo_tag]

    company, _ = await _odoo_company_for(odoo, org, c.tax_id, c.name)
    details = await company_details.gather(c.db, org.id, c.name, c.req.card_external_id)
    detail_vals = details.odoo_vals(await _in_thread(odoo.country_id, details.country_code))

    if company is None:
        vals = {"name": c.name, "x_supplier_code": code, "x_supplier_status": "active",
                **detail_vals}
        if org.tax_id:
            vals["vat"] = org.tax_id
        partner_id = await _in_thread(odoo.create_company, vals, tags)
        created = True
    else:
        existing = _odoo_supplier_code(company)
        if existing and existing != code:
            raise IssueError(f"Odoo company {company['name']!r} already has supplier code "
                             f"{existing}, not {code}")
        vals = {"x_supplier_status": "active"}
        if not company.get("x_supplier_code"):
            vals["x_supplier_code"] = code
        if org.tax_id and not company.get("vat"):
            vals["vat"] = org.tax_id
        # Fill only what Odoo does not have yet; never overwrite edits made in Odoo
        vals.update({k: v for k, v in detail_vals.items() if not company.get(k)})
        await _in_thread(odoo.update_company, company["id"], vals, tags)
        partner_id, created = company["id"], False
    org.odoo_partner_id = partner_id
    await c.db.commit()
    return {"partner_id": partner_id, "created": created}


async def _step_drive_supplier(c: _Ctx) -> dict:
    org = c.org
    res = await c.drive.ensure_supplier_folder(c.category.key, org.supplier_code, c.name)
    org.supplier_drive_folder_id, org.supplier_drive_url = res.folder.id, res.folder.url
    await _in_thread(c.odoo.update_company, org.odoo_partner_id,
                     {"x_supplier_drive_url": res.folder.url})
    await c.db.commit()
    return {"folder_id": res.folder.id, "url": res.folder.url, "status": res.status,
            "warnings": res.warnings}


async def _step_person(c: _Ctx) -> dict:
    if not (c.req.card_external_id and c.req.include_person):
        return {"skipped": True}
    from app.routers.v2.export import _export_one, _load_full_card
    card = await _load_full_card(c.db, c.req.card_external_id)
    if not card:
        raise IssueError("The card to export was not found")
    item = await _export_one(c.db, card, "odoo", company_partner_id=c.org.odoo_partner_id)
    await c.db.commit()
    if item.result == "error":
        raise IssueError(f"Exporting the person failed: {item.error_message}")
    return {"result": item.result}


_STEPS: List[tuple[str, Callable[[_Ctx], Awaitable[dict]]]] = [
    ("local_org", _step_local_org),
    ("supplier_code", _step_supplier_code),
    ("odoo_company", _step_odoo_company),
    ("drive_supplier", _step_drive_supplier),
    ("person", _step_person),
]


async def _run_out(db: AsyncSession, run: IssueRun, failed_step: Optional[str]) -> IssueRunOut:
    org = await db.get(Organization, run.org_id) if run.org_id else None
    return IssueRunOut(
        run_id=run.external_id, kind=run.kind, status=run.status, failed_step=failed_step,
        error=run.error, steps=json.loads(run.steps_json or "{}"),
        supplier_code=org.supplier_code if org else None,
        supplier_drive_url=org.supplier_drive_url if org else None,
        customer_code=org.customer_code if org else None,
        customer_drive_url=org.drive_url if org else None,
        odoo_partner_id=org.odoo_partner_id if org else None,
    )


async def _execute(db: AsyncSession, odoo: OdooCodes, drive: DriveFolders,
                   run: IssueRun) -> IssueRunOut:
    req = SupplierIssueRequest.model_validate_json(run.input_json)
    category, tax_id = _clean_request(req)
    steps: dict = json.loads(run.steps_json or "{}")
    c = _Ctx(db=db, odoo=odoo, drive=drive, req=req, run=run, category=category, tax_id=tax_id,
             name=(req.supplier_name or "").strip(), steps=steps)
    for ext in (req.org_ext_id, req.chosen_org_ext_id):
        if ext and not c.name:
            c.name = await _org_name(db, await _org_by_ext(db, ext))

    run.status, run.error = "running", None
    run_ext_id = run.external_id          # read now: a rollback below expires the row
    failed_step = None
    for step_name, fn in _STEPS:
        try:
            steps[step_name] = await fn(c)
        except Exception as exc:  # noqa: BLE001 - report any failure, keep what was done
            await db.rollback()
            await db.refresh(run)
            logger.exception("Supplier run %s failed at %s", run_ext_id, step_name)
            failed_step = step_name
            run.status = "failed"
            run.error = f"{step_name}: {exc}"
            break
        run.steps_json = json.dumps(steps, ensure_ascii=False)
        await db.commit()
    else:
        run.status = "done"
    run.steps_json = json.dumps(steps, ensure_ascii=False)
    await db.commit()
    return await _run_out(db, run, failed_step)


async def commit_supplier(db: AsyncSession, odoo: OdooCodes, drive: DriveFolders,
                          req: SupplierIssueRequest) -> IssueRunOut:
    # Validate first so a bad request never creates a run or touches Odoo
    _clean_request(req)
    if not req.org_ext_id and not req.chosen_org_ext_id and not (req.supplier_name or "").strip():
        raise pc.ValidationError("Supplier name is required")
    run = IssueRun(kind=KIND, input_json=req.model_dump_json(), status="running")
    db.add(run)
    await db.commit()
    return await _execute(db, odoo, drive, run)


async def retry_supplier(db: AsyncSession, odoo: OdooCodes, drive: DriveFolders,
                         run: IssueRun) -> IssueRunOut:
    if run.status == "done":
        return await _run_out(db, run, None)
    return await _execute(db, odoo, drive, run)
