from __future__ import annotations

import base64
import logging
import xmlrpc.client

from app.config import settings
from app.models.card import Card

logger = logging.getLogger(__name__)


def _get_odoo_clients() -> tuple[xmlrpc.client.ServerProxy, xmlrpc.client.ServerProxy, int]:
    """Return (common_proxy, object_proxy, uid)."""
    common = xmlrpc.client.ServerProxy(f"{settings.odoo_url}/xmlrpc/2/common")
    uid = common.authenticate(
        settings.odoo_db, settings.odoo_username, settings.odoo_password, {}
    )
    if not uid:
        raise RuntimeError("Odoo authentication failed")
    obj = xmlrpc.client.ServerProxy(f"{settings.odoo_url}/xmlrpc/2/object")
    return common, obj, uid


def _call(obj, uid, model: str, method: str, *args, **kwargs):
    return obj.execute_kw(
        settings.odoo_db, uid, settings.odoo_password,
        model, method, *args, **kwargs
    )


def _find_or_create_company(obj, uid, company_name: str) -> int | None:
    """Find existing company partner or create a new one."""
    if not company_name:
        return None

    ids = _call(obj, uid, "res.partner", "search", [
        [["is_company", "=", True], ["name", "=", company_name]]
    ])
    if ids:
        return ids[0]

    return _call(obj, uid, "res.partner", "create", [{
        "name": company_name,
        "is_company": True,
    }])


def _resolve_country(obj, uid, country_code: str) -> int | None:
    if not country_code:
        return None
    ids = _call(obj, uid, "res.country", "search", [
        [["code", "=", country_code.upper()]]
    ])
    return ids[0] if ids else None


def _resolve_state(obj, uid, state_name: str, country_id: int | None) -> int | None:
    if not state_name:
        return None
    domain = [["name", "like", state_name]]
    if country_id:
        domain.append(["country_id", "=", country_id])
    ids = _call(obj, uid, "res.country.state", "search", [domain])
    return ids[0] if ids else None


async def sync_to_odoo(card: Card, existing_id: int | None = None) -> int:
    """Create or update an Odoo res.partner from card data. Returns partner ID."""
    _, obj, uid = _get_odoo_clients()
    person = card.person

    # Primary name
    primary_name = ""
    second_lang_name = ""
    for n in person.names:
        if n.type == "primary":
            primary_name = n.value
        elif n.type == "romanized" or n.language == "en":
            second_lang_name = n.value

    if not primary_name and person.names:
        primary_name = person.names[0].value

    # First position
    pos = person.positions[0] if person.positions else None
    company_name = pos.company if pos else ""
    company_id = _find_or_create_company(obj, uid, company_name)

    # Phones
    work_phone = ""
    mobile = ""
    for p in person.phones:
        if p.type == "mobile" and not mobile:
            mobile = p.value
        elif p.type in ("work", "") and not work_phone:
            work_phone = p.value

    # Email
    email = person.emails[0].value if person.emails else ""

    # Address
    addr = person.addresses[0] if person.addresses else None
    country_id = _resolve_country(obj, uid, addr.country_code) if addr else None
    state_id = _resolve_state(obj, uid, addr.state if addr else "", country_id)

    vals: dict = {
        "name": primary_name,
        "phone": work_phone,
        "mobile": mobile,
        "email": email,
        "website": person.website,
        "function": pos.title if pos else "",
        "comment": _build_notes(card),
    }

    if company_id:
        vals["parent_id"] = company_id

    if second_lang_name:
        vals["x_studio_name_2nd_language"] = second_lang_name

    if addr:
        vals["street"] = addr.street
        vals["city"] = addr.city
        vals["zip"] = addr.postal_code
        if country_id:
            vals["country_id"] = country_id
        if state_id:
            vals["state_id"] = state_id

    # Person photo
    if card.images.person_photo:
        vals["image_1920"] = card.images.person_photo

    if existing_id:
        _call(obj, uid, "res.partner", "write", [[existing_id], vals])
        partner_id = existing_id
        logger.info("Updated Odoo partner %d", partner_id)
    else:
        partner_id = _call(obj, uid, "res.partner", "create", [vals])
        logger.info("Created Odoo partner %d", partner_id)

    return partner_id


def _build_notes(card: Card) -> str:
    """Build HTML notes with all positions, extra names, and metadata."""
    lines: list[str] = []

    if card.received_date:
        lines.append(f"<b>Card received:</b> {card.received_date}")

    # All positions
    if len(card.person.positions) > 1:
        lines.append("<b>Additional positions:</b>")
        for i, pos in enumerate(card.person.positions[1:], 2):
            parts = [p for p in [pos.company, pos.department, pos.title] if p]
            lines.append(f"  {i}. {' / '.join(parts)}")

    # All names
    if len(card.person.names) > 1:
        lines.append("<b>Names:</b>")
        for n in card.person.names:
            lines.append(f"  - {n.value} ({n.language}, {n.type})")

    # Extra phones (fax, etc.)
    fax_phones = [p for p in card.person.phones if p.type == "fax"]
    if fax_phones:
        lines.append(f"<b>FAX:</b> {fax_phones[0].value}")

    # Social
    soc = card.person.social
    if soc.linkedin:
        lines.append(f"<b>LinkedIn:</b> {soc.linkedin}")
    if soc.wechat:
        lines.append(f"<b>WeChat:</b> {soc.wechat}")
    if soc.line:
        lines.append(f"<b>LINE:</b> {soc.line}")

    if card.notes:
        lines.append(f"<b>Notes:</b> {card.notes}")

    return "<br/>".join(lines)


async def search_odoo_contacts(query: str) -> list[dict]:
    """Search Odoo contacts by name, email, or phone."""
    _, obj, uid = _get_odoo_clients()

    domain = [
        "|", "|",
        ["name", "ilike", query],
        ["email", "ilike", query],
        ["phone", "ilike", query],
    ]
    ids = _call(obj, uid, "res.partner", "search", [domain], {"limit": 10})
    if not ids:
        return []

    records = _call(obj, uid, "res.partner", "read", [ids], {
        "fields": ["name", "email", "phone", "parent_id"]
    })
    results = []
    for r in records:
        results.append({
            "id": str(r["id"]),
            "name": r.get("name", ""),
            "email": r.get("email", "") or "",
            "phone": r.get("phone", "") or "",
            "company": r["parent_id"][1] if r.get("parent_id") else "",
            "source": "odoo",
        })
    return results
