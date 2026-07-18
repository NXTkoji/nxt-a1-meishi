"""
CSV export service — pure functions with no I/O.

Takes a list of Card (app.models.card.Card) objects and returns a
UTF-8 CSV string formatted for the target system's import.
"""
from __future__ import annotations

import csv
import io

from app.models.card import Card


def _primary_name(card: Card) -> str:
    """Return the primary display name for the card owner."""
    names = card.person.names
    # Prefer name_type == "primary", fall back to first name
    for n in names:
        if n.type == "primary":
            return n.value
    return names[0].value if names else ""


def _work_phone(card: Card) -> str:
    for p in card.person.phones:
        if p.type in ("work", ""):
            return p.value
    return ""


def _mobile_phone(card: Card) -> str:
    for p in card.person.phones:
        if p.type == "mobile":
            return p.value
    return ""


def _first_email(card: Card) -> str:
    return card.person.emails[0].value if card.person.emails else ""


def _first_address(card: Card):
    return card.person.addresses[0] if card.person.addresses else None


def _notes(card: Card) -> str:
    """Plain-text notes (no HTML, unlike odoo_sync._build_notes)."""
    parts = []
    if card.received_date:
        parts.append(f"Card received: {card.received_date}")
    if card.notes:
        parts.append(card.notes)
    soc = card.person.social
    if soc.linkedin:
        parts.append(f"LinkedIn: {soc.linkedin}")
    if soc.wechat:
        parts.append(f"WeChat: {soc.wechat}")
    if soc.line:
        parts.append(f"LINE: {soc.line}")
    return "\n".join(parts)


def format_odoo_csv(cards: list[Card]) -> str:
    """
    Return a UTF-8 CSV string ready for Odoo's standard contact import.

    Columns: Name, Company Name, Job Position, Department, Phone, Mobile,
             Email, Website, Street, City, Zip, Country, Notes
    """
    FIELDNAMES = [
        "Name", "Company Name", "Job Position", "Department",
        "Phone", "Mobile", "Email", "Website",
        "Street", "City", "Zip", "Country", "Notes",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=FIELDNAMES, lineterminator="\r\n")
    writer.writeheader()

    for card in cards:
        pos = card.person.positions[0] if card.person.positions else None
        addr = _first_address(card)
        writer.writerow({
            "Name": _primary_name(card),
            "Company Name": pos.company if pos else "",
            "Job Position": pos.title if pos else "",
            "Department": pos.department if pos else "",
            "Phone": _work_phone(card),
            "Mobile": _mobile_phone(card),
            "Email": _first_email(card),
            "Website": card.person.website,
            "Street": addr.street if addr else "",
            "City": addr.city if addr else "",
            "Zip": addr.postal_code if addr else "",
            "Country": addr.country if addr else "",
            "Notes": _notes(card),
        })

    return buf.getvalue()


def format_google_csv(cards: list[Card]) -> str:
    """
    Return a UTF-8 CSV string ready for Google Contacts import.

    Columns: Name, Given Name, Family Name, Organization Name,
             Organization Title, Phone 1 - Value, Phone 1 - Type,
             E-mail 1 - Value, Address 1 - Street, Address 1 - City,
             Address 1 - Postal Code, Address 1 - Country, Notes
    """
    FIELDNAMES = [
        "Name", "Given Name", "Family Name",
        "Organization Name", "Organization Title",
        "Phone 1 - Value", "Phone 1 - Type",
        "E-mail 1 - Value",
        "Address 1 - Street", "Address 1 - City",
        "Address 1 - Postal Code", "Address 1 - Country",
        "Notes",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=FIELDNAMES, lineterminator="\r\n")
    writer.writeheader()

    for card in cards:
        pos = card.person.positions[0] if card.person.positions else None
        addr = _first_address(card)

        # Split display name into given/family heuristically.
        # For CJK names the full name goes into "Name" and both given/family
        # remain empty — Google handles it fine.
        display = _primary_name(card)
        parts = display.split()
        given = parts[0] if len(parts) >= 2 else ""
        family = " ".join(parts[1:]) if len(parts) >= 2 else ""

        # First non-fax phone
        phone = next(
            (p for p in card.person.phones if p.type != "fax"), None
        )

        writer.writerow({
            "Name": display,
            "Given Name": given,
            "Family Name": family,
            "Organization Name": pos.company if pos else "",
            "Organization Title": pos.title if pos else "",
            "Phone 1 - Value": phone.value if phone else "",
            "Phone 1 - Type": phone.type.capitalize() if phone else "",
            "E-mail 1 - Value": _first_email(card),
            "Address 1 - Street": addr.street if addr else "",
            "Address 1 - City": addr.city if addr else "",
            "Address 1 - Postal Code": addr.postal_code if addr else "",
            "Address 1 - Country": addr.country if addr else "",
            "Notes": _notes(card),
        })

    return buf.getvalue()
