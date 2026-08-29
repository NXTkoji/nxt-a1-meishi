"""
Stale scan-session cleanup.

A scan session is only cleaned up by a successful confirm. Anything the user
walks away from — a closed tab, a failed confirm, a re-scan — keeps its
non-terminal status forever and keeps its uploads in temp/. The frontend
defines abandonSession() but never calls it, so nothing reclaims them.

This sweep ages those out: sessions older than settings.session_retention_days
are marked abandoned and their temp images deleted. Temp directories with no
session row at all are unreachable by the app and are removed too.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import ScanSession
from app.services import image_store

logger = logging.getLogger(__name__)

# Statuses a session can sit in while still in progress.
ACTIVE_STATUSES = ("uploading", "grouping", "analyzing", "review")


def _delete_stray_temp_dirs(known_external_ids: set[str]) -> int:
    """Remove temp dirs that no session row points at — the app cannot reach them."""
    if not settings.temp_path.exists():
        return 0
    removed = 0
    for d in settings.temp_path.iterdir():
        if d.is_dir() and d.name not in known_external_ids:
            image_store.delete_temp_session(d.name)
            removed += 1
    return removed


async def sweep_stale_sessions(
    db: AsyncSession,
    max_age_days: Optional[int] = None,
) -> dict:
    """
    Abandon sessions older than max_age_days and reclaim their temp images.

    Returns counts: {"abandoned": n, "temp_dirs_removed": n, "stray_dirs_removed": n}
    """
    if max_age_days is None:
        max_age_days = settings.session_retention_days
    cutoff = datetime.utcnow() - timedelta(days=max_age_days)

    stale = (await db.execute(
        select(ScanSession).where(
            ScanSession.status.in_(ACTIVE_STATUSES),
            ScanSession.created_at < cutoff,
        )
    )).scalars().all()

    now = datetime.utcnow()
    stale_ext_ids = [s.external_id for s in stale]
    for session in stale:
        session.status = "abandoned"
        session.completed_at = now
    await db.commit()

    # Only once the rows are committed: a failed commit must not destroy the
    # images of a session that is still active.
    for ext_id in stale_ext_ids:
        image_store.delete_temp_session(ext_id)

    known = set((await db.execute(select(ScanSession.external_id))).scalars().all())
    strays = _delete_stray_temp_dirs(known)

    result = {
        "abandoned": len(stale_ext_ids),
        "temp_dirs_removed": len(stale_ext_ids),
        "stray_dirs_removed": strays,
    }
    if any(result.values()):
        logger.info(
            "Session sweep (>%dd): abandoned %d, removed %d temp dirs and %d stray dirs",
            max_age_days, result["abandoned"], result["temp_dirs_removed"],
            result["stray_dirs_removed"],
        )
    return result
