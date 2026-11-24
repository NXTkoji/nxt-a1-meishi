"""
Customer and supplier roles of an organization (see the party-codes spec).

Each role has a status: none (stored as None), potential, active or inactive.
`active` means the role has a code, so only the issue flows (propose / commit) make a
role active; this module changes the statuses that need no code and keeps the legacy
`is_nxt_customer` switch in step (it is true when the customer status is not none).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.db.models import Organization
from app.services import supplier_codes as sc
from app.services.project_codes import ValidationError


class RoleConflict(ValueError):
    """The change is not allowed in the organization's current state (HTTP 409)."""


def sync_customer_switch(org: Organization) -> None:
    """is_nxt_customer is derived: true when the customer status is not none."""
    org.is_nxt_customer = org.customer_status is not None


def ensure_customer_potential(org: Organization) -> None:
    """Mark an organization as a (potential) customer without ever lowering a status."""
    if org.customer_status is None:
        org.customer_status = "potential"
    sync_customer_switch(org)


def set_customer_active(org: Organization) -> None:
    org.customer_status = "active"
    sync_customer_switch(org)


@dataclass
class RoleChange:
    """What a roles update changed, for the Odoo side."""
    odoo_vals: dict = field(default_factory=dict)      # x_customer_status / x_supplier_status
    tag_names: list = field(default_factory=list)      # category tag to add
    changed: bool = False


def _check_status(role: str, current: Optional[str], target: Optional[str], has_code: bool) -> None:
    """Allowed moves for one role. A role with a code is active or inactive; one without
    is none or potential. Becoming active needs the issue flow (a code)."""
    if target == current:
        return
    if target == "active":
        if not has_code:
            raise ValidationError(
                f"{role.capitalize()} cannot be made Active here: issue its code first "
                "(propose, then approve)")
        return
    if target == "inactive":
        if not has_code:
            raise RoleConflict(f"{role.capitalize()} has no code yet, so there is nothing to "
                               "deactivate; set it to none instead")
        return
    # none or potential
    if has_code:
        raise RoleConflict(f"{role.capitalize()} already has a code; set it to Inactive, not "
                           f"{target or 'none'}")


def apply_role_update(org: Organization, *, fields_set: set, customer_status: Optional[str],
                      supplier_status: Optional[str], supplier_category: Optional[str]) -> RoleChange:
    """Validate and apply a roles update to the organization. Only the fields in
    `fields_set` are touched. Raises ValidationError (422) or RoleConflict (409)."""
    change = RoleChange()

    if "customer_status" in fields_set:
        target = sc.validate_role_status(customer_status)
        _check_status("customer", org.customer_status, target, bool(org.customer_code))
        if target != org.customer_status:
            org.customer_status = target
            sync_customer_switch(org)
            change.odoo_vals["x_customer_status"] = target or False
            change.changed = True

    if "supplier_category" in fields_set:
        key = (supplier_category or "").strip().lower() or None
        category = sc.category(key) if key else None
        if org.supplier_code and key != org.supplier_category:
            raise RoleConflict("The supplier already has a code and a folder; its category "
                               "cannot be changed here")
        if key != org.supplier_category:
            org.supplier_category = key
            change.changed = True
        if category:
            change.tag_names.append(category.odoo_tag)

    if "supplier_status" in fields_set:
        target = sc.validate_role_status(supplier_status)
        _check_status("supplier", org.supplier_status, target, bool(org.supplier_code))
        if target != org.supplier_status:
            org.supplier_status = target
            change.odoo_vals["x_supplier_status"] = target or False
            change.changed = True
    return change
