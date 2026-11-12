"""
Project code issuing router (NXT customers only).

POST /api/v2/customers/resolve                 who might this customer already be?
POST /api/v2/projects/propose                  preview codes and actions; creates nothing
POST /api/v2/projects/commit                   approve: create everything, resumable
POST /api/v2/projects/runs/{run_id}/retry      resume a failed run
GET  /api/v2/projects                          list issued projects
PUT  /api/v2/organizations/{org}/nxt-customer  turn the NXT customer switch on or off
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import verify_api_key
from app.config import settings
from app.db.models import Organization, OrganizationName, Project
from app.db.session import get_db
from app.schemas.api import (
    CandidateOut, IssueRequest, IssueRunOut, NxtCustomerToggle, OrgListItem,
    ProposalOut, ResolveOut, ResolveRequest,
)
from app.services import issue_flow
from app.services import project_codes as pc
from app.services.drive_folders import DriveError, DriveFolders
from app.services.odoo_codes import OdooCodes, OdooDataError, OdooSetupError, customer_id_of

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v2",
    tags=["project-codes"],
    dependencies=[Depends(verify_api_key)],
)


# ---------------------------------------------------------------------------
# Dependencies (tests override these with fakes)
# ---------------------------------------------------------------------------

async def get_odoo() -> OdooCodes:
    if not (settings.odoo_url and settings.odoo_password):
        raise HTTPException(503, "Odoo is not configured")
    try:
        return await asyncio.to_thread(OdooCodes.connect)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Odoo connection failed")
        raise HTTPException(502, f"Could not connect to Odoo: {exc}") from exc


async def get_drive():
    try:
        drive = await DriveFolders.from_settings()
    except DriveError as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("Google Drive connection failed")
        raise HTTPException(502, f"Could not connect to Google Drive: {exc}") from exc
    try:
        yield drive
    finally:
        await drive.aclose()


async def _open_drive_or_none():
    """Drive for read-only previews; None when it cannot be reached (preview then warns)."""
    try:
        return await DriveFolders.from_settings()
    except Exception:  # noqa: BLE001
        logger.warning("Google Drive unavailable for preview", exc_info=True)
        return None


async def get_drive_opener():
    """Dependency returning the function that opens Drive (overridable in tests)."""
    return _open_drive_or_none


def _as_http(exc: Exception) -> HTTPException:
    if isinstance(exc, pc.ValidationError):
        return HTTPException(422, str(exc))
    if isinstance(exc, OdooSetupError):
        return HTTPException(503, f"Odoo setup problem: {exc}")
    if isinstance(exc, OdooDataError):
        return HTTPException(409, str(exc))
    raise exc


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/customers/resolve", response_model=ResolveOut)
async def resolve_customer(
    body: ResolveRequest,
    db: AsyncSession = Depends(get_db),
    odoo: OdooCodes = Depends(get_odoo),
):
    try:
        tax_id = pc.validate_tax_id(body.tax_id) if (body.tax_id or "").strip() else None
        candidates = await pc.match_organization(
            db, tax_id=tax_id, phone=body.phone, name=body.name)
        ext_ids = {c.org_id: (await db.get(Organization, c.org_id)).external_id for c in candidates}
        company = await asyncio.to_thread(odoo.find_company, vat=tax_id) if tax_id else None
    except (pc.ValidationError, OdooSetupError, OdooDataError) as exc:
        raise _as_http(exc)
    return ResolveOut(
        candidates=[issue_flow._candidate_out(c, ext_ids) for c in candidates],
        odoo_customer_code=customer_id_of(company) if company else None,
        odoo_company_name=(company or {}).get("name"),
    )


@router.post("/projects/propose", response_model=ProposalOut)
async def propose_project(
    body: IssueRequest,
    db: AsyncSession = Depends(get_db),
    odoo: OdooCodes = Depends(get_odoo),
    open_drive=Depends(get_drive_opener),
):
    # Drive is only needed to preview old-folder moves, so connect only when asked for
    drive = await open_drive() if (body.legacy_codes or "").strip() else None
    try:
        return await issue_flow.propose(db, odoo, body, drive)
    except (pc.ValidationError, OdooSetupError, OdooDataError) as exc:
        raise _as_http(exc)
    finally:
        if drive is not None:
            await drive.aclose()


@router.post("/projects/commit", response_model=IssueRunOut)
async def commit_project(
    body: IssueRequest,
    db: AsyncSession = Depends(get_db),
    odoo: OdooCodes = Depends(get_odoo),
    drive: DriveFolders = Depends(get_drive),
):
    """Approve a proposal. A failure partway returns status "failed" with the step
    that failed; call the retry endpoint to resume without creating anything twice."""
    try:
        return await issue_flow.commit(db, odoo, drive, body)
    except (pc.ValidationError, OdooSetupError, OdooDataError) as exc:
        raise _as_http(exc)


@router.post("/projects/runs/{run_id}/retry", response_model=IssueRunOut)
async def retry_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    odoo: OdooCodes = Depends(get_odoo),
    drive: DriveFolders = Depends(get_drive),
):
    try:
        return await issue_flow.retry(db, odoo, drive, run_id)
    except (pc.ValidationError, OdooSetupError, OdooDataError) as exc:
        raise _as_http(exc)


@router.get("/projects")
async def list_projects(
    org_ext_id: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Project, Organization.external_id).join(
        Organization, Organization.id == Project.org_id).order_by(Project.id.desc())
    if org_ext_id:
        stmt = stmt.where(Organization.external_id == org_ext_id)
    rows = (await db.execute(stmt)).all()
    return [
        {
            "code": p.code, "org_ext_id": ext, "product_line": p.product_line,
            "site_name": p.site_name, "drive_url": p.drive_url,
            "odoo_project_id": p.odoo_project_id, "created_at": p.created_at,
        }
        for p, ext in rows
    ]


@router.put("/organizations/{org_ext_id}/nxt-customer", response_model=OrgListItem)
async def set_nxt_customer(
    org_ext_id: str,
    body: NxtCustomerToggle,
    db: AsyncSession = Depends(get_db),
):
    """Turn the NXT customer switch on or off. Once a Customer ID has been issued
    the switch cannot be turned off: the code, the Odoo company and the folders exist."""
    org = await db.scalar(select(Organization).where(Organization.external_id == org_ext_id))
    if not org:
        raise HTTPException(404, "Organization not found")
    if not body.enabled and org.customer_code:
        raise HTTPException(409, f"{org.customer_code} was already issued; it cannot be switched off")
    org.is_nxt_customer = body.enabled
    primary = await db.scalar(
        select(OrganizationName.name)
        .where(OrganizationName.org_id == org.id, OrganizationName.is_current == True)  # noqa: E712
        .order_by(OrganizationName.id.asc()).limit(1)
    )
    return OrgListItem(
        id=org.id, external_id=org.external_id, primary_name=primary, created_at=org.created_at,
        is_nxt_customer=org.is_nxt_customer, customer_code=org.customer_code,
    )
