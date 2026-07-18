"""Build the legacy app.models.card.Card Pydantic model from v2 database
objects, for handing off to the Odoo and Google Contacts sync services
(both still speak the pre-v2 Card/Person shape).
"""
from __future__ import annotations

from app.db.models import Card as DBCard, ContactDetail, Person, Position


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
        Phone,
        Position as LegacyPosition,
        Social,
    )

    names = [
        LegacyName(value=n.full_name, language=n.language, type=n.name_type)
        for n in person.names
        if n.is_current
    ]

    legacy_positions = []
    for pos in positions:
        org_name_ja = next(
            (on.name for on in pos.organization.names if on.language == "ja" and on.is_current),
            next((on.name for on in pos.organization.names if on.is_current), ""),
        )
        org_name_en = next(
            (on.name for on in pos.organization.names if on.language == "en" and on.is_current),
            "",
        )
        title_ja = next((pd.title or "" for pd in pos.details if pd.language == "ja"), "")
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
        ))

    phones, emails, addresses = [], [], []
    website = ""
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
            addresses.append(Address(type=kind, full=cd.value))
        elif t == "url_website":
            website = cd.value
        elif t == "social_wechat":
            social.wechat = cd.value
        elif t == "social_line":
            social.line = cd.value
        elif t == "social_linkedin":
            social.linkedin = cd.value

    legacy_person = LegacyPerson(
        names=names,
        positions=legacy_positions,
        phones=phones,
        emails=emails,
        addresses=addresses,
        website=website,
        social=social,
    )

    # Raw, unsorted, not deduped — google_contacts.py's _build_person_body
    # is the only consumer today and does its own dedup/sort; a future
    # second consumer should not assume this list is already clean.
    my_company_labels = [
        link.my_company.google_label or link.my_company.name
        for link in db_card.my_company_links
    ]

    return LegacyCard(
        person=legacy_person,
        received_date=str(db_card.received_date) if db_card.received_date else "",
        notes=db_card.notes or "",
        my_company_labels=my_company_labels,
    )
