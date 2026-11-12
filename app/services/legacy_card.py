"""Build the legacy app.models.card.Card Pydantic model from v2 database
objects, for handing off to the Odoo and Google Contacts sync services
(both still speak the pre-v2 Card/Person shape).
"""
from __future__ import annotations

from app.db.models import Card as DBCard, ContactDetail, Person, Position
from app.services.work_contact_labels import pos_index


def build_legacy_card(
    db_card: DBCard,
    person: Person,
    contact_details: list[ContactDetail],
    positions: list[Position],
):
    from app.models.card import (
        Address,
        Card as LegacyCard,
        Email,
        Person as LegacyPerson,
        PersonName as LegacyName,
        PersonRelation as LegacyRelation,
        Phone,
        Position as LegacyPosition,
        Social,
    )

    names = [
        LegacyName(value=n.full_name, language=n.language, type=n.name_type)
        for n in person.names
        if n.is_current
    ]

    # Work contacts name their organization by index into the positions in id order
    # ("_pos:N"); positions may arrive in any order, so look the index up per position
    index_of = {pos.id: i for i, pos in enumerate(sorted(positions, key=lambda p: p.id))}

    def _work(detail_type: str, index: int):
        return next((cd for cd in contact_details
                     if cd.detail_type == detail_type and cd.value and pos_index(cd.label) == index), None)

    legacy_positions = []
    for pos in positions:
        work_phone = _work("phone_work", index_of[pos.id])
        work_address = _work("address_work", index_of[pos.id])
        org_name_ja = next(
            (on.name for on in pos.organization.names if on.language == "ja" and on.is_current),
            next((on.name for on in pos.organization.names if on.is_current), ""),
        )
        org_name_en = next(
            (on.name for on in pos.organization.names if on.language == "en" and on.is_current),
            "",
        )
        # Prefer the Japanese title, else the first one in any language (a zh-TW card
        # has no ja title, and Odoo would get an empty job position)
        title_ja = next(
            (pd.title for pd in pos.details if pd.language == "ja" and pd.title),
            next((pd.title for pd in pos.details if pd.title), ""),
        )
        title_en = next((pd.title or "" for pd in pos.details if pd.language == "en"), "")
        dept = next(
            (pd.department or "" for pd in pos.details if pd.language == "ja"),
            next((pd.department or "" for pd in pos.details), ""),
        )
        legacy_positions.append(LegacyPosition(
            company=org_name_ja,
            company_english=org_name_en,
            title=title_ja,
            title_english=title_en,
            department=dept,
            phone=work_phone.value if work_phone else "",
            address=work_address.value if work_address else "",
            address_country_code=(work_address.country_code or "") if work_address else "",
            odoo_partner_id=getattr(pos.organization, "odoo_partner_id", None),
        ))

    phones, emails, addresses = [], [], []
    website = ""
    website_personal = ""
    social = Social()
    for cd in contact_details:
        t = cd.detail_type
        if t in ("phone_work", "phone_mobile", "phone_fax"):
            kind = t.replace("phone_", "")
            phones.append(Phone(value=cd.value, type=kind, label=cd.label or ""))
        elif t in ("email_work", "email_personal"):
            kind = t.replace("email_", "")
            emails.append(Email(value=cd.value, type=kind))
        elif t in ("address_work", "address_home"):
            kind = t.replace("address_", "")
            addresses.append(Address(type=kind, full=cd.value, country_code=cd.country_code or ""))
        elif t == "url_website":
            website = cd.value
        elif t == "url_personal":
            website_personal = cd.value
        elif t == "social_wechat":
            social.wechat = cd.value
        elif t == "social_line":
            social.line = cd.value
        elif t == "social_linkedin":
            social.linkedin = cd.value

    relations = []
    for rel in person.relationships_from:
        to_name = next(
            (n.full_name for n in rel.to_person.names if n.is_current and n.name_type == "primary"),
            next((n.full_name for n in rel.to_person.names if n.is_current), ""),
        )
        if to_name:
            relations.append(LegacyRelation(type=rel.relationship_type.key, name=to_name))

    legacy_person = LegacyPerson(
        names=names,
        positions=legacy_positions,
        phones=phones,
        emails=emails,
        addresses=addresses,
        website=website,
        website_personal=website_personal,
        social=social,
        relations=relations,
        birthday=person.birthday or "",
        notes=person.notes or "",
    )

    # Raw, unsorted, not deduped — google_contacts.py's _build_person_body
    # is the only consumer today and does its own dedup/sort; a future
    # second consumer should not assume this list is already clean.
    my_company_labels = [
        link.my_company.google_label or link.my_company.name
        for link in db_card.my_company_links
    ]

    # A live link wins so renames propagate; occasion_label is the snapshot left
    # behind when the occasion was deleted. occasion_location has no snapshot —
    # location is a property of the occasion, not of the card — so it goes empty.
    occasion_name = db_card.occasion.name if db_card.occasion else (db_card.occasion_label or "")
    occasion_location = db_card.occasion.location or "" if db_card.occasion else ""

    return LegacyCard(
        person=legacy_person,
        received_date=str(db_card.received_date) if db_card.received_date else "",
        received_location=db_card.received_location or "",
        notes=db_card.notes or "",
        my_company_labels=my_company_labels,
        occasion_name=occasion_name,
        occasion_location=occasion_location,
    )
