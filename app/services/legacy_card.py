"""Build the legacy app.models.card.Card Pydantic model from v2 database
objects, for handing off to the Odoo and Google Contacts sync services
(both still speak the pre-v2 Card/Person shape).
"""
from __future__ import annotations

from app.db.models import Card as DBCard, ContactDetail, Person, Position

# Which language a Google contact shows for company, title and department.
# Most contacts are Taiwanese, so Traditional Chinese comes first, then
# Japanese, then any other non-English language. English is carried
# separately (a second organizations[] entry, and the notes), so it's never
# picked here. One order for all three fields keeps a bilingual card from
# pairing, say, a Japanese company name with a Chinese title.
NATIVE_LANGUAGE_ORDER = ("zh-TW", "ja")


def pick_native(values: list[tuple[str, str | None]]) -> str:
    """Pick the preferred native-language value from (language, value) pairs.

    Empty values are skipped. Returns "" when only English (or nothing) is present.
    """
    present = [(lang, value) for lang, value in values if value]
    for preferred in NATIVE_LANGUAGE_ORDER:
        for lang, value in present:
            if lang == preferred:
                return value
    return next((value for lang, value in present if lang != "en"), "")


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

    legacy_positions = []
    for pos in positions:
        current_org_names = [(on.language, on.name) for on in pos.organization.names if on.is_current]
        # An English-only company still needs a name, so fall back to any name.
        org_name_native = pick_native(current_org_names) or next(
            (name for _, name in current_org_names if name), ""
        )
        org_name_en = next((name for lang, name in current_org_names if lang == "en" and name), "")
        title_native = pick_native([(pd.language, pd.title) for pd in pos.details])
        title_en = next((pd.title for pd in pos.details if pd.language == "en" and pd.title), "")
        # Department has no English slot of its own, so fall back to any.
        dept = pick_native([(pd.language, pd.department) for pd in pos.details]) or next(
            (pd.department for pd in pos.details if pd.department), ""
        )
        legacy_positions.append(LegacyPosition(
            company=org_name_native,
            company_english=org_name_en,
            title=title_native,
            title_english=title_en,
            department=dept,
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
            addresses.append(Address(type=kind, full=cd.value))
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
