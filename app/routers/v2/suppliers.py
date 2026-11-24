"""
Supplier code issuing and party roles (see the party-codes spec).

POST /api/v2/suppliers/propose                 preview the supplier code and actions; creates nothing
POST /api/v2/suppliers/commit                  approve: issue the code, Odoo company, folder; resumable
PUT  /api/v2/organizations/{org}/roles         customer / supplier status and supplier category

A failed supplier run is resumed with POST /api/v2/projects/runs/{run_id}/retry, which
dispatches on the run's kind.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import verify_api_key
from app.db.models import Organization, OrganizationName
from app.db.session import get_db
from app.routers.v2.projects import _as_http, get_drive, get_drive_opener, get_odoo
from app.schemas.api import (
    IssueRunOut, OrgListItem, RolesUpdate, SupplierIssueRequest, SupplierProposalOut,
)
from app.services import party_roles, supplier_flow
from app.services import project_codes as pc
from app.services.drive_folders import DriveFolders
from app.services.odoo_codes import OdooCodes, OdooDataError, OdooSetupError

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v2",
    tags=["supplier-codes"],
    dependencies=[Depends(verify_api_key)],
)


async def _open_odoo_or_none():
    """Odoo for a status write; None when it cannot be reached."""
    try:
        return await asyncio.to_thread(OdooCodes.connect)
    except Exception:  # noqa: BLE001
        logger.warning("Odoo unavailable for a roles update", exc_info=True)
        return None


async def get_odoo_opener():
    """Dependency returning the function that opens Odoo (overridable in tests)."""
    return _open_odoo_or_none


@router.post("/suppliers/propose", response_model=SupplierProposalOut)
async def propose_supplier(
    body: SupplierIssueRequest,
    db: AsyncSession = Depends(get_db),
    odoo: OdooCodes = Depends(get_odoo),
    open_drive=Depends(get_drive_opener),
):
    drive = await open_drive()
    try:
        return await supplier_flow.propose_supplier(db, odoo, body, drive)
    except (pc.ValidationError, OdooSetupError, OdooDataError) as exc:
        raise _as_http(exc)
    finally:
        if drive is not None:
            await drive.aclose()


@router.post("/suppliers/commit", response_model=IssueRunOut)
async def commit_supplier(
    body: SupplierIssueRequest,
    db: AsyncSession = Depends(get_db),
    odoo: OdooCodes = Depends(get_odoo),
    drive: DriveFolders = Depends(get_drive),
):
    """Approve a proposal. A failure partway returns status "failed" with the step that
    failed; call the retry endpoint to resume without creating or drawing anything twice."""
    try:
        return await supplier_flow.commit_supplier(db, odoo, drive, body)
    except (pc.ValidationError, OdooSetupError, OdooDataError) as exc:
        raise _as_http(exc)


@router.put("/organizations/{org_ext_id}/roles", response_model=OrgListItem)
async def set_roles(
    org_ext_id: str,
    body: RolesUpdate,
    db: AsyncSession = Depends(get_db),
    open_odoo=Depends(get_odoo_opener),
):
    """Set the customer / supplier status (none, potential, inactive) and the supplier
    category. Making a role Active needs a code, so it goes through the issue flows.
    The Odoo company, when there is one, gets the same status; if Odoo cannot be
    updated nothing is changed."""
    org = await db.scalar(select(Organization).where(Organization.external_id == org_ext_id))
    if not org:
        raise HTTPException(404, "Organization not found")
    try:
        change = party_roles.apply_role_update(
            org, fields_set=body.model_fields_set, customer_status=body.customer_status,
            supplier_status=body.supplier_status, supplier_category=body.supplier_category)
    except pc.ValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
    except party_roles.RoleConflict as exc:
        raise HTTPException(409, str(exc)) from exc

    if org.odoo_partner_id and (change.odoo_vals or change.tag_names):
        odoo = await open_odoo()
        if odoo is None:
            raise HTTPException(503, "Odoo cannot be reached, so the status was not changed")
        try:
            await asyncio.to_thread(odoo.update_company, org.odoo_partner_id,
                                    change.odoo_vals, change.tag_names)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Updating the Odoo company failed")
            raise HTTPException(502, f"Odoo update failed, nothing was changed: {exc}") from exc

    primary = await db.scalar(
        select(OrganizationName.name)
        .where(OrganizationName.org_id == org.id, OrganizationName.is_current == True)  # noqa: E712
        .order_by(OrganizationName.id.asc()).limit(1)
    )
    return OrgListItem(
        id=org.id, external_id=org.external_id, primary_name=primary, created_at=org.created_at,
        is_nxt_customer=org.is_nxt_customer, customer_code=org.customer_code,
        customer_status=org.customer_status, supplier_status=org.supplier_status,
        supplier_code=org.supplier_code, supplier_category=org.supplier_category,
    )
