"""
Claude Vision parser for business cards.

Features vs. original:
  - N-side support (front, back, fold-out pages, up to 6 sides)
  - Per-field confidence scores (0.0–1.0)
  - Few-shot correction injection from FieldCorrection history
  - Adaptive thinking (claude-opus-4-6)
  - Streaming with progress callback for SSE
  - Returns ParsedCard (structured Pydantic model) instead of raw JSON
"""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator, Callable, List, Optional

import anthropic

from app.config import settings
from app.schemas.parsed_card import (
    CF,
    MatchResult,
    ParsedCard,
    ParsedContactDetail,
    ParsedName,
    ParsedOrgName,
    ParsedPosition,
    ParsedPositionDetail,
)
from app.services.image_store import temp_image_resized_b64

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an expert business card OCR and data-extraction specialist.
You read business cards written in Japanese, Chinese (Traditional and \
Simplified), English, and Korean with high accuracy.

You return ONLY valid JSON matching the schema shown in the user message. \
Never guess or fabricate information. If a field is not present on the card, \
omit it or use null — never invent data.

For every extracted value, you MUST include a confidence score (0.0–1.0):
  1.0 = clearly printed, no ambiguity
  0.8 = mostly clear, minor formatting uncertainty
  0.6 = partially legible or inferred from context
  <0.6 = low confidence — flag for user review"""

_SCHEMA = """\
{
  "names": [
    {
      "language": "ja|zh|zh-TW|en|ko",
      "name_type": "primary|romanized|nickname|former",
      "family_name": {"value": "...", "confidence": 1.0},
      "given_name":  {"value": "...", "confidence": 1.0},
      "full_name":   {"value": "...", "confidence": 1.0}
    }
  ],
  "positions": [
    {
      "org_names": [
        {"language": "ja|zh|zh-TW|en|ko", "name": {"value": "...", "confidence": 1.0}}
      ],
      "details": [
        {
          "language": "ja|zh|zh-TW|en|ko",
          "title":      {"value": "...", "confidence": 1.0},
          "department": {"value": "...", "confidence": 1.0}
        }
      ]
    }
  ],
  "contact_details": [
    {
      "detail_type": "phone_work|phone_mobile|phone_fax|email_work|email_personal|address_work|address_home|url_website|social_wechat|social_line|social_linkedin|social_other",
      "value": {"value": "...", "confidence": 1.0},
      "label": "original label printed on card or null"
    }
  ],
  "card_date": "YYYY-MM-DD or null",
  "languages_detected": ["ja", "en"],
  "overall_confidence": 0.95
}"""

_RULES = """\
EXTRACTION RULES:
1. Names: extract every name representation visible on the card. Tag each
   with its language (ja=Japanese, zh=Simplified Chinese, zh-TW=Traditional
   Chinese, en=English/Romaji, ko=Korean). The "primary" name_type goes to
   the most visually prominent name. Romaji/furigana reading = "romanized".
2. Family name comes FIRST in Japanese (田中 太郎 → family=田中, given=太郎)
   and Chinese (王大明 → family=王, given=大明) names.
3. Organizations: if the card shows the same company in multiple languages,
   add one org_names entry per language within the SAME position object.
   If the person holds MULTIPLE distinct positions (different orgs or titles),
   create SEPARATE objects in the "positions" array.
4. Phone labels: TEL/電話 → phone_work, 携帯/手機/Mobile/Cell → phone_mobile,
   FAX/傳真/ファックス → phone_fax. Preserve the exact number format.
5. Address: use address_work for business addresses, address_home for home.
   For addresses, put the full formatted address as the value.
6. Social: WeChat ID (微信) → social_wechat, LINE ID → social_line.
7. Back/additional sides: MERGE information — do not duplicate. Use extra
   sides to fill in missing language versions or additional contact details.
8. Return ONLY the JSON object. No markdown fences, no explanation."""


def _build_user_content(
    side_paths: List[str],
    few_shot_block: str,
) -> List[dict]:
    """Build the Claude message content list with images + prompt."""
    side_labels = ["FRONT", "BACK", "SIDE 3", "SIDE 4", "SIDE 5", "SIDE 6"]
    content: List[dict] = []

    for i, rel_path in enumerate(side_paths):
        b64 = temp_image_resized_b64(rel_path)
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
        })
        label = side_labels[i] if i < len(side_labels) else f"SIDE {i + 1}"
        content.append({"type": "text", "text": f"{label} of business card."})

    prompt_parts = [f"Analyze the business card image(s) above.\n\n{_RULES}"]
    if few_shot_block:
        prompt_parts.append(f"\n\n{few_shot_block}")
    prompt_parts.append(f"\n\nReturn JSON matching this schema exactly:\n{_SCHEMA}")

    content.append({"type": "text", "text": "\n".join(prompt_parts)})
    return content


# ---------------------------------------------------------------------------
# JSON → ParsedCard
# ---------------------------------------------------------------------------

def _normalize_caps(value: str) -> str:
    """Title-case a string that is entirely uppercase Latin text.

    Leaves CJK/mixed strings untouched; only fires when every ASCII
    alphabetic character in the string is uppercase (e.g. "YUICHI FUKUHARA"
    → "Yuichi Fukuhara").
    """
    has_latin = any(c.isascii() and c.isalpha() for c in value)
    all_upper = all(not (c.isascii() and c.isalpha()) or c.isupper() for c in value)
    return value.title() if has_latin and all_upper else value


def _cf(raw: Optional[dict], normalize: bool = False) -> Optional[CF]:
    if not raw or not raw.get("value"):
        return None
    value = raw["value"]
    if normalize:
        value = _normalize_caps(value)
    return CF(value=value, confidence=float(raw.get("confidence", 1.0)))


def _cf_required(raw: dict, normalize: bool = False) -> CF:
    value = raw.get("value", "")
    if normalize:
        value = _normalize_caps(value)
    return CF(value=value, confidence=float(raw.get("confidence", 1.0)))


def _build_parsed_card(data: dict) -> ParsedCard:
    names = [
        ParsedName(
            language=n["language"],
            name_type=n.get("name_type", "primary"),
            family_name=_cf(n.get("family_name"), normalize=True),
            given_name=_cf(n.get("given_name"), normalize=True),
            full_name=_cf_required(n.get("full_name", {"value": ""}), normalize=True),
        )
        for n in data.get("names", [])
        if n.get("full_name", {}).get("value")
    ]

    positions = [
        ParsedPosition(
            org_names=[
                ParsedOrgName(
                    language=on["language"],
                    name=_cf_required(on.get("name", {"value": ""})),
                )
                for on in p.get("org_names", [])
                if on.get("name", {}).get("value")
            ],
            details=[
                ParsedPositionDetail(
                    language=d["language"],
                    title=_cf(d.get("title")),
                    department=_cf(d.get("department")),
                )
                for d in p.get("details", [])
            ],
        )
        for p in data.get("positions", [])
    ]

    contact_details = [
        ParsedContactDetail(
            detail_type=cd["detail_type"],
            value=_cf_required(cd.get("value", {"value": ""})),
            label=cd.get("label"),
        )
        for cd in data.get("contact_details", [])
        if cd.get("value", {}).get("value")
    ]

    return ParsedCard(
        names=names,
        positions=positions,
        contact_details=contact_details,
        card_date=data.get("card_date"),
        languages_detected=data.get("languages_detected", []),
        overall_confidence=float(data.get("overall_confidence", 1.0)),
    )


# ---------------------------------------------------------------------------
# JSON extraction helper
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> str:
    """Strip markdown fences if present."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n", 1)
        text = lines[1] if len(lines) > 1 else text
        if text.endswith("```"):
            text = text[: text.rfind("```")]
    return text.strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def parse_card_sides(
    side_paths: List[str],
    few_shot_block: str = "",
    on_progress: Optional[Callable[[str], None]] = None,
) -> ParsedCard:
    """
    Parse N card side images with Claude Vision.

    Args:
        side_paths: list of temp-relative image paths (index 0 = front)
        few_shot_block: formatted correction examples from correction_store
        on_progress: optional callback called with status strings for SSE

    Returns:
        ParsedCard with per-field confidence scores
    """
    if not side_paths:
        raise ValueError("At least one card side image is required")

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key or None)
    content = _build_user_content(side_paths, few_shot_block)

    if on_progress:
        on_progress("Sending to Claude Vision…")

    # Use adaptive thinking for Opus; Sonnet/Haiku don't support it
    stream_kwargs: dict = dict(
        model=settings.claude_model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    )
    if "opus" in settings.claude_model:
        stream_kwargs["thinking"] = {"type": "adaptive"}

    full_text = ""
    async with client.messages.stream(**stream_kwargs) as stream:
        async for event in stream:
            if hasattr(event, "type"):
                if event.type == "content_block_start":
                    if on_progress and getattr(event.content_block, "type", "") == "thinking":
                        on_progress("Thinking…")
                elif event.type == "content_block_delta":
                    delta = event.delta
                    if getattr(delta, "type", "") == "text_delta":
                        full_text += delta.text
        # Ensure we have the complete message text
        msg = await stream.get_final_message()
        for block in msg.content:
            if getattr(block, "type", "") == "text":
                full_text = block.text
                break

    if on_progress:
        on_progress("Parsing response…")

    raw = _extract_json(full_text)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Claude returned invalid JSON, requesting fix…")
        if on_progress:
            on_progress("Fixing JSON…")
        fix = await client.messages.create(
            model=settings.claude_model,
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": f"Fix this invalid JSON. Return ONLY valid JSON:\n{raw}",
            }],
        )
        raw = _extract_json(fix.content[0].text)
        data = json.loads(raw)

    card = _build_parsed_card(data)
    if on_progress:
        on_progress(f"Done (confidence: {card.overall_confidence:.0%})")

    return card


async def stream_parse_card_sides(
    side_paths: List[str],
    few_shot_block: str = "",
) -> AsyncIterator[str]:
    """
    Async generator that yields SSE-compatible progress strings, then the
    final JSON result as the last item (prefixed with "result:").

    Usage in a FastAPI SSE endpoint:
        async for chunk in stream_parse_card_sides(paths, corrections):
            yield f"data: {chunk}\n\n"
    """
    progress_events: List[str] = []

    def collect(msg: str) -> None:
        progress_events.append(msg)

    # We run parse_card_sides and yield progress events as they arrive.
    # Since parse_card_sides is not a generator itself, we buffer events.
    # For true streaming, call parse_card_sides with on_progress.
    import asyncio

    result_holder: List[ParsedCard] = []
    error_holder: List[Exception] = []

    async def run() -> None:
        try:
            card = await parse_card_sides(side_paths, few_shot_block, on_progress=collect)
            result_holder.append(card)
        except Exception as exc:
            error_holder.append(exc)

    task = asyncio.create_task(run())

    # Yield progress events while the task runs
    last_yielded = 0
    while not task.done():
        await asyncio.sleep(0.1)
        while last_yielded < len(progress_events):
            yield progress_events[last_yielded]
            last_yielded += 1

    # Drain remaining progress events
    while last_yielded < len(progress_events):
        yield progress_events[last_yielded]
        last_yielded += 1

    if error_holder:
        raise error_holder[0]

    yield f"result:{result_holder[0].model_dump_json()}"
