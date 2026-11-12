"""What the app knows about an organization, shaped for its Odoo company record.

Organizations have no contact details of their own: phones, addresses and
websites are work contacts of the people who work there, each pointing at one
of that person's positions by list index ("_pos:N", see work_contact_labels).
This module collects them for one organization and maps them onto Odoo fields.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Card, ContactDetail, OrganizationName, Position
from app.services.work_contact_labels import pos_index

# Work contact type -> CompanyDetails attribute
_TYPE_TO_FIELD = {
    "address_work": "address",
    "phone_work": "phone",
    "phone_fax": "fax",
    "url_website": "website",
}


@dataclass
class CompanyDetails:
    other_names: str = ""            # every other current name, joined with " / "
    address: str = ""
    country_code: str = ""           # of the address
    phone: str = ""
    fax: str = ""
    website: str = ""

    def odoo_vals(self, country_id: Optional[int]) -> dict:
        """Odoo res.partner values for the fields that have a value."""
        vals = {
            "x_studio_name_2nd_language": self.other_names,
            "street": self.address,
            "phone": self.phone,
            "x_fax": self.fax,
            "website": self.website,
        }
        if country_id:
            vals["country_id"] = country_id
        return {k: v for k, v in vals.items() if v}


async def _other_names(db: AsyncSession, org_id: int, primary_name: str) -> str:
    names = (await db.execute(
        select(OrganizationName.name)
        .where(OrganizationName.org_id == org_id, OrganizationName.is_current == True)  # noqa: E712
        .order_by(OrganizationName.id)
    )).scalars().all()
    others: list[str] = []
    for name in names:
        if name and name != primary_name and name not in others:
            others.append(name)
    return " / ".join(others)


async def _person_ids(db: AsyncSession, org_id: int, card_ext_id: Optional[str]) -> list[int]:
    """People with a current position at the organization; the card's person first."""
    ids = list((await db.execute(
        select(Position.person_id)
        .where(Position.org_id == org_id, Position.status == "current")
        .order_by(Position.id)
    )).scalars().unique())
    if card_ext_id:
        first = await db.scalar(select(Card.person_id).where(Card.external_id == card_ext_id))
        if first in ids:
            ids.remove(first)
            ids.insert(0, first)
    return ids


async def _work_contacts(db: AsyncSession, person_id: int, org_id: int) -> list[ContactDetail]:
    """The person's work contacts that belong to their position at org_id."""
    position_orgs = (await db.execute(
        select(Position.org_id).where(Position.person_id == person_id).order_by(Position.id)
    )).scalars().all()
    if org_id not in position_orgs:
        return []
    index = position_orgs.index(org_id)
    details = (await db.execute(
        select(ContactDetail)
        .where(ContactDetail.person_id == person_id,
               ContactDetail.detail_type.in_(_TYPE_TO_FIELD))
        .order_by(ContactDetail.id)
    )).scalars().all()
    return [d for d in details if pos_index(d.label) == index]


async def gather(db: AsyncSession, org_id: int, primary_name: str,
                 card_ext_id: Optional[str] = None) -> CompanyDetails:
    """Collect the organization's details. The first value found wins per field,
    looking at the issuing card's person first, then everyone else there."""
    out = CompanyDetails(other_names=await _other_names(db, org_id, primary_name))
    for person_id in await _person_ids(db, org_id, card_ext_id):
        for d in await _work_contacts(db, person_id, org_id):
            attr = _TYPE_TO_FIELD[d.detail_type]
            if not getattr(out, attr) and d.value:
                setattr(out, attr, d.value)
                if attr == "address":
                    out.country_code = d.country_code or ""
    return out
