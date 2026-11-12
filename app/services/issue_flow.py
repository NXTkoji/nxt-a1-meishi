"""
Propose and commit for NXT project codes.

propose()  Read-only. Works out which customer this is, what the Customer ID and
           project code would be, and what approval will do. Creates nothing and
           consumes no sequence number.
commit()   Runs the approved request as a series of steps. Every step is idempotent
           (it looks for what it would create first) and persists its result before
           the next one starts, so a failure can be retried with retry() without
           duplicating anything.

See docs/superpowers/specs/2026-11-11-project-code-issuing-design.md.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, List, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IssueRun, Organization, OrganizationName, Project
from app.schemas.api import CandidateOut, IssueRequest, IssueRunOut, ProposalOut
from app.services import company_details
from app.services import project_codes as pc
from app.services.drive_folders import (
    CUSTOMER_SUBFOLDERS, PROJECT_SUBFOLDERS, DriveError, DriveFolders,
)
from app.services.odoo_codes import ROLE_TAGS, TAG_CUSTOMER, OdooCodes

logger = logging.getLogger(__name__)


class IssueError(RuntimeError):
    """A step could not finish for a reason the user can act on."""


async def _in_thread(fn, *args, **kwargs):
    # Odoo's XML-RPC client is blocking
    return await asyncio.to_thread(fn, *args, **kwargs)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

async def _org_by_ext(db: AsyncSession, ext_id: str) -> Organization:
    org = await db.scalar(select(Organization).where(Organization.external_id == ext_id))
    if not org:
        raise pc.ValidationError(f"Organization {ext_id} not found")
    return org


async def _org_name(db: AsyncSession, org: Organization) -> str:
    return await db.scalar(
        select(OrganizationName.name)
        .where(OrganizationName.org_id == org.id, OrganizationName.is_current == True)  # noqa: E712
        .order_by(OrganizationName.id.asc()).limit(1)
    ) or ""


def _clean_request(req: IssueRequest) -> tuple[Optional[str], str, List[str]]:
    """Validate the user-correctable parts. Returns (tax_id, product_line, roles)."""
    tax_id = pc.validate_tax_id(req.tax_id) if (req.tax_id or "").strip() else None
    line = pc.validate_product_line(req.product_line)
    if not (req.site_name or "").strip():
        raise pc.ValidationError("Site name is required")
    roles = []
    for role in req.roles:
        if role not in ROLE_TAGS:
            raise pc.ValidationError(f"Unknown role {role!r}; use reseller or end_user")
        roles.append(role)
    pc.parse_legacy_codes(req.legacy_codes)   # raises ValidationError on a bad old code
    return tax_id, line, roles


async def _local_project_codes(db: AsyncSession, customer_code: str) -> List[str]:
    rows = await db.scalars(select(Project.code).where(Project.code.like(f"{customer_code}-%")))
    return list(rows)


async def _sold_via(db: AsyncSession, req: IssueRequest) -> Optional[Organization]:
    if not req.sold_via_org_ext_id:
        return None
    org = await _org_by_ext(db, req.sold_via_org_ext_id)
    if not org.customer_code:
        raise pc.ValidationError(
            "The reseller must be an NXT customer with a Customer ID first; issue one for it")
    return org


def _candidate_out(c: pc.OrgCandidate, ext_ids: dict[int, str]) -> CandidateOut:
    return CandidateOut(
        org_ext_id=ext_ids[c.org_id], name=c.name, customer_code=c.customer_code,
        tax_id=c.tax_id, reason=c.reason, confidence=round(c.confidence, 2), auto=c.auto,
    )


# ---------------------------------------------------------------------------
# Propose (no side effects)
# ---------------------------------------------------------------------------

@dataclass
class _Target:
    org: Optional[Organization] = None
    needs_choice: bool = False
    candidates: List[pc.OrgCandidate] = field(default_factory=list)


async def _resolve_target(db: AsyncSession, req: IssueRequest, tax_id: Optional[str],
                          name: str) -> _Target:
    if req.org_ext_id:
        return _Target(org=await _org_by_ext(db, req.org_ext_id))
    if req.chosen_org_ext_id:
        return _Target(org=await _org_by_ext(db, req.chosen_org_ext_id))

    candidates = await pc.match_organization(db, tax_id=tax_id, phone=req.phone, name=name)
    if req.create_new:
        # Rejecting every candidate is fine, but a 統編 can only belong to one customer.
        owner = next((c for c in candidates if c.reason == "tax_id"), None)
        if owner:
            raise pc.ValidationError(
                f"統編 {tax_id} already belongs to {owner.name}; choose that customer instead")
        return _Target(candidates=candidates)
    autos = [c for c in candidates if c.auto]
    if len(autos) == 1:
        org = await db.get(Organization, autos[0].org_id)
        return _Target(org=org, candidates=candidates)
    if candidates:
        return _Target(needs_choice=True, candidates=candidates)
    return _Target()


async def _check_tax_id_conflicts(db: AsyncSession, org: Optional[Organization],
                                  tax_id: Optional[str]) -> None:
    if not tax_id:
        return
    if org and org.tax_id and org.tax_id != tax_id:
        raise pc.ValidationError(
            f"This customer already has 統編 {org.tax_id}; it does not match {tax_id}")
    other = await db.scalar(select(Organization).where(Organization.tax_id == tax_id))
    if other and (not org or other.id != org.id):
        raise pc.ValidationError(
            f"統編 {tax_id} already belongs to another customer ({await _org_name(db, other)})")


async def _check_legacy_conflicts(db: AsyncSession, org: Optional[Organization],
                                  codes: List[str]) -> None:
    """An old code belongs to one customer; refuse if another customer already has it."""
    if not codes:
        return
    rows = (await db.scalars(select(Organization).where(Organization.legacy_codes.is_not(None)))).all()
    for other in rows:
        if org and other.id == org.id:
            continue
        clash = [c for c in codes if c in pc.parse_legacy_codes(other.legacy_codes)]
        if clash:
            raise pc.ValidationError(
                f"Old code {clash[0]} already belongs to another customer "
                f"({await _org_name(db, other)})")


async def _legacy_preview(drive: Optional[DriveFolders], codes: List[str],
                          customer_folder_id: Optional[str]) -> tuple[List[str], List[str]]:
    """What approval will do about old folders. Returns (actions, warnings); read-only."""
    actions: List[str] = []
    warnings: List[str] = []
    if not codes:
        return actions, warnings
    actions.append("Record old code(s) " + ", ".join(codes) + " as aliases of this customer")
    if drive is None:
        warnings.append("Google Drive could not be checked now; old folders for "
                        + ", ".join(codes) + " will be looked for on approval.")
        return actions, warnings
    for code in codes:
        try:
            status, folder = await drive.peek_legacy_folder(code, customer_folder_id)
        except DriveError as exc:
            warnings.append(f"{code}: {exc}")
            continue
        if status == "movable":
            actions.append(f"Move old folder {folder.name!r} into the customer folder")
        elif status == "already_inside":
            actions.append(f"Old folder {folder.name!r} is already inside the customer folder")
        else:
            warnings.append(
                f"No folder starting with {code} was found in the NXT Google Drive. The code is "
                "still recorded. If the folder is in another Drive or OneDrive, move it into the "
                "customer folder by hand after approval.")
    return actions, warnings


async def propose(db: AsyncSession, odoo: OdooCodes, req: IssueRequest,
                  drive: Optional[DriveFolders] = None) -> ProposalOut:
    tax_id, line, roles = _clean_request(req)
    if not req.org_ext_id and not (req.customer_name or "").strip():
        raise pc.ValidationError("Customer name is required")
    sold_via = await _sold_via(db, req)

    target = await _resolve_target(db, req, tax_id, (req.customer_name or "").strip())
    org = target.org
    await _check_tax_id_conflicts(db, org, tax_id)
    legacy = pc.parse_legacy_codes(req.legacy_codes)
    await _check_legacy_conflicts(db, org, legacy)

    name = (await _org_name(db, org)) if org else ""
    name = name or (req.customer_name or "").strip()
    tax_id = tax_id or (org.tax_id if org else None)

    ext_ids = {}
    for c in target.candidates:
        ext_ids[c.org_id] = (await db.get(Organization, c.org_id)).external_id

    warnings: List[str] = []
    out = ProposalOut(
        customer_mode="new", org_ext_id=org.external_id if org else None,
        customer_name=name, tax_id=tax_id, needs_choice=target.needs_choice,
        candidates=[_candidate_out(c, ext_ids) for c in target.candidates],
        product_line=line, site_name=req.site_name.strip(),
        sold_via_name=(await _org_name(db, sold_via)) if sold_via else None,
    )
    if target.needs_choice:
        out.warnings.append("Possible existing customers found; choose one or create a new customer.")
        return out

    # Does Odoo already know this customer (by Customer ID, or by 統編)?
    customer_code = org.customer_code if org else None
    odoo_company = await _in_thread(odoo.find_company, ref=customer_code, vat=tax_id)
    if odoo_company and odoo_company.get("ref"):
        if customer_code and odoo_company["ref"] != customer_code:
            raise pc.ValidationError(
                f"Odoo has {odoo_company['ref']} for this 統編 but this customer is {customer_code}")
        customer_code = odoo_company["ref"]
    elif odoo_company:
        warnings.append(f"Odoo already has a company named {odoo_company['name']!r} without a "
                        "Customer ID; it will be reused rather than duplicated.")
    elif name:
        # Not found by Customer ID or 統編; a card export may still have created it by name
        same_name = [m for m in await _in_thread(odoo.find_companies_by_name, name)
                     if not m.get("ref")]
        if len(same_name) == 1:
            warnings.append(f"Odoo already has a company named {same_name[0]['name']!r} without a "
                            "Customer ID; it will be reused rather than duplicated.")
        elif len(same_name) > 1:
            warnings.append(f"Odoo has {len(same_name)} companies named {name!r}; merge them in "
                            "Odoo before approving, or approval will stop.")

    if customer_code:
        out.customer_mode = "existing"
        out.customer_code = customer_code
        existing = (await _in_thread(odoo.project_codes_for, customer_code)
                    + await _local_project_codes(db, customer_code))
        number = pc.next_project_number(existing, customer_code, line)
        out.project_code = pc.format_project_code(customer_code, line, number)
    else:
        tentative = await _in_thread(odoo.peek_next_customer_id)
        out.suggested_customer_code = tentative
        out.project_code = pc.format_project_code(tentative, line, 1)
        out.project_code_tentative = True
        warnings.append("The Customer ID is a preview; the final number is assigned on approval.")

    actions = []
    if out.customer_mode == "new":
        actions.append(f"Create Customer ID {out.suggested_customer_code} (preview) for {name}")
        actions.append("Create the company in Odoo (tag NXT Customer)")
    else:
        actions.append(f"Use existing customer {out.customer_code}")
        actions.append("Make sure the Odoo company carries the NXT Customer tag")
    if org:
        details = await company_details.gather(db, org.id, name, req.card_external_id)
        filled = [label for label, value in [
            ("other names", details.other_names), ("address", details.address),
            ("phone", details.phone), ("fax", details.fax), ("website", details.website)] if value]
        if filled:
            actions.append("Add to the Odoo company where empty: " + ", ".join(filled))
    actions.append("Create the customer folder in Google Drive with "
                   + ", ".join(CUSTOMER_SUBFOLDERS))
    actions.append(f"Create project folder {out.project_code} with " + ", ".join(PROJECT_SUBFOLDERS))
    legacy_actions, legacy_warnings = await _legacy_preview(
        drive, legacy, org.drive_folder_id if org else None)
    actions.extend(legacy_actions)
    warnings.extend(legacy_warnings)
    actions.append(f"Create Odoo project {out.project_code}")
    if req.card_external_id and req.include_person:
        actions.append("Export the person on the card under the company in Odoo")
    out.actions = actions
    out.warnings = warnings
    return out


# ---------------------------------------------------------------------------
# Commit (resumable)
# ---------------------------------------------------------------------------

@dataclass
class _Ctx:
    db: AsyncSession
    odoo: OdooCodes
    drive: DriveFolders
    req: IssueRequest
    run: IssueRun
    tax_id: Optional[str]
    line: str
    roles: List[str]
    name: str
    org: Optional[Organization] = None
    project: Optional[Project] = None


def _contains_cjk(text: str) -> bool:
    return any("　" <= ch <= "鿿" for ch in text)


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
        candidates = await pc.match_organization(
            db, tax_id=c.tax_id, phone=req.phone, name=c.name)
        autos = [x for x in candidates if x.auto]
        if len(autos) == 1:
            org = await db.get(Organization, autos[0].org_id)
        elif candidates:
            raise IssueError("Several customers match; choose one or create a new customer")
    if org is None:
        org = Organization()
        db.add(org)
        await db.flush()
        db.add(OrganizationName(
            org_id=org.id, language="zh" if _contains_cjk(c.name) else "en",
            name=c.name, source="issue"))
    await _check_tax_id_conflicts(db, org, c.tax_id)
    org.is_nxt_customer = True
    if c.tax_id and not org.tax_id:
        org.tax_id = c.tax_id
    c.run.org_id = org.id
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise IssueError(f"統編 or customer already exists: {exc.orig}") from exc
    c.org = org
    return {"org_ext_id": org.external_id}


async def _step_customer_id(c: _Ctx) -> dict:
    org = c.org
    if org.customer_code:
        return {"customer_code": org.customer_code, "reused": True}
    # A company already in Odoo with this 統編 may carry a Customer ID from before
    found = await _in_thread(c.odoo.find_company, vat=org.tax_id) if org.tax_id else None
    if found and found.get("ref"):
        code, source = found["ref"], "odoo"
    else:
        code, source = await _in_thread(c.odoo.draw_customer_id), "drawn"
    org.customer_code = code
    await c.db.commit()   # persist before anything else, so a retry never draws again
    return {"customer_code": code, "source": source}


async def _step_odoo_company(c: _Ctx) -> dict:
    org, odoo = c.org, c.odoo
    code = org.customer_code
    tags = [TAG_CUSTOMER] + [ROLE_TAGS[r] for r in c.roles]

    company = await _in_thread(odoo.find_company, ref=code, vat=org.tax_id)
    if company is None:
        # A card export may already have created this company without a Customer ID
        matches = [m for m in await _in_thread(odoo.find_companies_by_name, c.name)
                   if not m.get("ref")]
        if len(matches) > 1:
            raise IssueError(f"Odoo has {len(matches)} companies named {c.name!r}; "
                             "merge them in Odoo first, then retry")
        company = matches[0] if matches else None

    # Other-language names, address, phone, fax and website, from the work contacts
    details = await company_details.gather(c.db, org.id, c.name, c.req.card_external_id)
    detail_vals = details.odoo_vals(await _in_thread(odoo.country_id, details.country_code))

    if company is None:
        vals = {"name": c.name, "ref": code, **detail_vals}
        if org.tax_id:
            vals["vat"] = org.tax_id
        partner_id = await _in_thread(odoo.create_company, vals, tags)
        created = True
    else:
        if company.get("ref") and company["ref"] != code:
            raise IssueError(f"Odoo company {company['name']!r} already has Customer ID "
                             f"{company['ref']}, not {code}")
        vals = {}
        if not company.get("ref"):
            vals["ref"] = code
        if org.tax_id and not company.get("vat"):
            vals["vat"] = org.tax_id
        # Fill only what Odoo does not have yet; never overwrite edits made in Odoo
        vals.update({k: v for k, v in detail_vals.items() if not company.get(k)})
        await _in_thread(odoo.update_company, company["id"], vals, tags)
        partner_id, created = company["id"], False
    org.odoo_partner_id = partner_id
    await c.db.commit()
    return {"partner_id": partner_id, "created": created}


async def _step_drive_customer(c: _Ctx) -> dict:
    org = c.org
    folder = await c.drive.ensure_customer_folder(org.customer_code, c.name)
    org.drive_folder_id, org.drive_url = folder.id, folder.url
    await _in_thread(c.odoo.update_company, org.odoo_partner_id, {"x_drive_url": folder.url})
    await c.db.commit()
    return {"folder_id": folder.id, "url": folder.url}


async def _step_legacy(c: _Ctx) -> dict:
    """Move each old folder into the customer folder (name kept) and record the old codes."""
    codes = pc.parse_legacy_codes(c.req.legacy_codes)
    if not codes:
        return {"skipped": True}
    org = c.org
    await _check_legacy_conflicts(c.db, org, codes)
    results: dict = {}
    for code in codes:
        status, folder = await c.drive.move_legacy_folder(code, org.drive_folder_id)
        results[code] = {"status": status, "name": folder.name if folder else None}
    org.legacy_codes = pc.merge_legacy_codes(org.legacy_codes, codes)
    await _in_thread(c.odoo.update_company, org.odoo_partner_id, {"x_legacy_codes": org.legacy_codes})
    await c.db.commit()
    return results


async def _step_local_project(c: _Ctx) -> dict:
    db, org = c.db, c.org
    project = await db.get(Project, c.run.project_id) if c.run.project_id else None
    if project is None:
        sold_via = await _sold_via(db, c.req)
        existing = (await _in_thread(c.odoo.project_codes_for, org.customer_code)
                    + await _local_project_codes(db, org.customer_code))
        number = pc.next_project_number(existing, org.customer_code, c.line)
        project = Project(
            code=pc.format_project_code(org.customer_code, c.line, number),
            org_id=org.id, product_line=c.line, site_name=c.req.site_name.strip(),
            sold_via_org_id=sold_via.id if sold_via else None,
        )
        db.add(project)
        try:
            await db.flush()
        except IntegrityError as exc:
            await db.rollback()
            raise IssueError(f"Project code {project.code} was taken just now; retry") from exc
        c.run.project_id = project.id
        await db.commit()
    c.project = project
    return {"project_code": project.code}


async def _step_odoo_project(c: _Ctx) -> dict:
    project, org = c.project, c.org
    found = await _in_thread(c.odoo.find_project, project.code)
    if found:
        project.odoo_project_id = found["id"]
        created = False
    else:
        vals = {
            "name": f"{project.code} {project.site_name}".strip(),
            "partner_id": org.odoo_partner_id,
            "x_project_code": project.code,
            "x_product_line": project.product_line,
        }
        if project.sold_via_org_id:
            sold_via = await c.db.get(Organization, project.sold_via_org_id)
            if sold_via and sold_via.odoo_partner_id:
                vals["x_sold_via"] = sold_via.odoo_partner_id
        project.odoo_project_id = await _in_thread(c.odoo.create_project, vals)
        created = True
    await c.db.commit()
    return {"odoo_project_id": project.odoo_project_id, "created": created}


async def _step_drive_project(c: _Ctx) -> dict:
    project, org = c.project, c.org
    folder = await c.drive.ensure_project_tree(org.drive_folder_id, project.code, project.site_name or "")
    project.drive_folder_id, project.drive_url = folder.id, folder.url
    await _in_thread(c.odoo.update_project, project.odoo_project_id, {"x_drive_url": folder.url})
    await c.db.commit()
    return {"folder_id": folder.id, "url": folder.url}


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
    ("customer_id", _step_customer_id),
    ("odoo_company", _step_odoo_company),
    ("drive_customer", _step_drive_customer),
    ("legacy", _step_legacy),
    ("local_project", _step_local_project),
    ("odoo_project", _step_odoo_project),
    ("drive_project", _step_drive_project),
    ("person", _step_person),
]


async def _execute(db: AsyncSession, odoo: OdooCodes, drive: DriveFolders,
                   run: IssueRun) -> IssueRunOut:
    req = IssueRequest.model_validate_json(run.input_json)
    tax_id, line, roles = _clean_request(req)
    steps: dict = json.loads(run.steps_json or "{}")
    c = _Ctx(db=db, odoo=odoo, drive=drive, req=req, run=run, tax_id=tax_id, line=line,
             roles=roles, name=(req.customer_name or "").strip())
    if req.org_ext_id and not c.name:
        c.name = await _org_name(db, await _org_by_ext(db, req.org_ext_id))
    if not c.name and req.chosen_org_ext_id:
        c.name = await _org_name(db, await _org_by_ext(db, req.chosen_org_ext_id))

    run.status, run.error = "running", None
    run_ext_id = run.external_id          # read now: a rollback below expires the row
    failed_step = None
    for step_name, fn in _STEPS:
        try:
            steps[step_name] = await fn(c)
        except Exception as exc:  # noqa: BLE001 - report any failure, keep what was done
            await db.rollback()
            await db.refresh(run)
            logger.exception("Issue run %s failed at %s", run_ext_id, step_name)
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


async def _run_out(db: AsyncSession, run: IssueRun, failed_step: Optional[str]) -> IssueRunOut:
    steps = json.loads(run.steps_json or "{}")
    org = await db.get(Organization, run.org_id) if run.org_id else None
    project = await db.get(Project, run.project_id) if run.project_id else None
    return IssueRunOut(
        run_id=run.external_id, status=run.status, failed_step=failed_step, error=run.error,
        steps=steps,
        customer_code=org.customer_code if org else None,
        project_code=project.code if project else None,
        customer_drive_url=org.drive_url if org else None,
        project_drive_url=project.drive_url if project else None,
        odoo_partner_id=org.odoo_partner_id if org else None,
        odoo_project_id=project.odoo_project_id if project else None,
    )


async def commit(db: AsyncSession, odoo: OdooCodes, drive: DriveFolders,
                 req: IssueRequest) -> IssueRunOut:
    # Validate first so a bad request never creates a run or touches Odoo
    _clean_request(req)
    if not req.org_ext_id and not req.chosen_org_ext_id and not (req.customer_name or "").strip():
        raise pc.ValidationError("Customer name is required")
    run = IssueRun(input_json=req.model_dump_json(), status="running")
    db.add(run)
    await db.commit()
    return await _execute(db, odoo, drive, run)


async def retry(db: AsyncSession, odoo: OdooCodes, drive: DriveFolders,
                run_ext_id: str) -> IssueRunOut:
    run = await db.scalar(select(IssueRun).where(IssueRun.external_id == run_ext_id))
    if not run:
        raise pc.ValidationError(f"Run {run_ext_id} not found")
    if run.status == "done":
        return await _run_out(db, run, None)
    return await _execute(db, odoo, drive, run)
