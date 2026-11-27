"""User preferences set on the Settings page, stored in the app_settings table.

Each setting has a fixed key, a default and a validator. A key with no row
uses its default, so a fresh database behaves exactly like the app did before
these settings existed (auto-export to Google Contacts, Project Management on).

Settings
  auto_export_destinations   list of destinations a card is pushed to
                             automatically whenever it is saved or its person
                             is edited. Subset of AUTO_EXPORT_DESTINATIONS.
  project_management_enabled show the Project Management module (the switch on
                             each organization and project-code issuing).
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppSetting

# Destinations that can run in the background. The order here is the order
# they run in: Odoo first, so a Google contact pushed in the same pass already
# reflects any Odoo side effects (none today, but keeps runs predictable).
AUTO_EXPORT_DESTINATIONS = ("odoo", "google_contacts")

AUTO_EXPORT_KEY = "auto_export_destinations"
PROJECT_MANAGEMENT_KEY = "project_management_enabled"


def _clean_destinations(value: Any) -> list[str]:
    """Keep known destinations only, de-duplicated, in AUTO_EXPORT_DESTINATIONS order."""
    if not isinstance(value, list):
        raise ValueError(f"{AUTO_EXPORT_KEY} must be a list")
    unknown = [v for v in value if v not in AUTO_EXPORT_DESTINATIONS]
    if unknown:
        raise ValueError(f"Unknown destination(s): {', '.join(map(str, unknown))}")
    return [d for d in AUTO_EXPORT_DESTINATIONS if d in value]


def _clean_bool(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError("must be true or false")
    return value


# key -> (default, validator)
_SPECS: Dict[str, tuple[Any, Callable[[Any], Any]]] = {
    AUTO_EXPORT_KEY: (["google_contacts"], _clean_destinations),
    PROJECT_MANAGEMENT_KEY: (True, _clean_bool),
}


async def get_all(db: AsyncSession) -> dict[str, Any]:
    """Every setting, stored value or default. A stored value that no longer
    validates (e.g. a destination that was removed) falls back to the default."""
    rows = {r.key: r.value for r in (await db.execute(select(AppSetting))).scalars()}
    out: dict[str, Any] = {}
    for key, (default, clean) in _SPECS.items():
        if key not in rows:
            out[key] = default
            continue
        try:
            out[key] = clean(json.loads(rows[key]))
        except (ValueError, TypeError):
            out[key] = default
    return out


async def get(db: AsyncSession, key: str) -> Any:
    return (await get_all(db))[key]


async def update(db: AsyncSession, changes: dict[str, Any]) -> dict[str, Any]:
    """Validate and store the given settings; unknown keys raise ValueError.
    Does not commit — the request session commits on success."""
    for key, value in changes.items():
        if key not in _SPECS:
            raise ValueError(f"Unknown setting: {key}")
        cleaned = _SPECS[key][1](value)
        row = await db.get(AppSetting, key)
        if row is None:
            db.add(AppSetting(key=key, value=json.dumps(cleaned)))
        else:
            row.value = json.dumps(cleaned)
    await db.flush()
    return await get_all(db)
