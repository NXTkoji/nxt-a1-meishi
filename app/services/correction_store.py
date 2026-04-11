"""
Correction store: loads FieldCorrection records and formats them as few-shot
examples injected into Claude prompts.

The goal is to teach Claude from past mistakes: if the user corrected
"language detection" or "field assignment" for a certain type of card,
future cards of similar type get that correction as a hint.

Example few-shot block injected into the prompt:

  PAST CORRECTIONS (learn from these):
  - Field "names[0].language": Claude said "en", user corrected to "ja"
    (card hash: abc123)
  - Field "positions[0].title": Claude said "部長", user corrected to "General Manager"
    (card hash: def456)
"""
from __future__ import annotations

import logging
from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FieldCorrection

logger = logging.getLogger(__name__)

# How many recent corrections to inject (keeps prompt size bounded)
DEFAULT_LIMIT = 20


async def get_few_shot_examples(
    db: AsyncSession,
    limit: int = DEFAULT_LIMIT,
    correction_types: List[str] | None = None,
) -> List[dict]:
    """
    Fetch recent FieldCorrection records from the DB.

    Args:
        db: async DB session
        limit: max number of corrections to return
        correction_types: filter to specific types (e.g. ["language_detection"])
                          None = all types

    Returns:
        List of dicts with keys: field_path, claude_value, user_value,
        correction_type, card_image_hash
    """
    stmt = select(FieldCorrection).order_by(FieldCorrection.id.desc()).limit(limit)
    if correction_types:
        stmt = stmt.where(FieldCorrection.correction_type.in_(correction_types))

    result = await db.execute(stmt)
    rows = result.scalars().all()

    return [
        {
            "field_path": r.field_path,
            "claude_value": r.claude_value,
            "user_value": r.user_value,
            "correction_type": r.correction_type,
            "card_image_hash": r.card_image_hash,
        }
        for r in rows
    ]


def format_for_prompt(examples: List[dict]) -> str:
    """
    Format correction examples as a human-readable block for injection into
    the Claude system or user prompt.

    Returns empty string if there are no examples.
    """
    if not examples:
        return ""

    lines = ["PAST CORRECTIONS — learn from these to improve accuracy:"]
    for ex in examples:
        parts = [f'  • Field "{ex["field_path"]}"']
        if ex["claude_value"] is not None:
            parts.append(f'Claude extracted: "{ex["claude_value"]}"')
        parts.append(f'correct value: "{ex["user_value"]}"')
        if ex.get("card_image_hash"):
            parts.append(f'(card: {ex["card_image_hash"][:8]}…)')
        lines.append(" → ".join(parts))

    return "\n".join(lines)


async def log_correction(
    db: AsyncSession,
    *,
    card_id: int | None,
    field_path: str,
    claude_value: str | None,
    user_value: str,
    correction_type: str,
    card_image_hash: str | None = None,
) -> FieldCorrection:
    """
    Persist a user correction to the DB.

    correction_type values:
      field_value         — user changed an extracted text value
      language_detection  — user changed the detected language tag
      field_assignment    — user moved a value to a different field
      merge_decision      — user overrode a match/no-match decision
    """
    correction = FieldCorrection(
        card_id=card_id,
        field_path=field_path,
        claude_value=claude_value,
        user_value=user_value,
        correction_type=correction_type,
        card_image_hash=card_image_hash,
    )
    db.add(correction)
    await db.flush()
    logger.debug(
        "Logged correction: %s → '%s' (type=%s)", field_path, user_value, correction_type
    )
    return correction
