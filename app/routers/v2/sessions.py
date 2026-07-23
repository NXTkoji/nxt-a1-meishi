"""
Scan session router — the heart of the scan flow.

Flow:
  1. POST   /api/v2/sessions                       → create session
  2. POST   /api/v2/sessions/{sid}/images           → upload N images
  3. GET    /api/v2/sessions/{sid}                  → list session + images
  4. PATCH  /api/v2/sessions/{sid}/images/{img_id}  → assign to card group
  5. POST   /api/v2/sessions/{sid}/analyze          → SSE: parse each card group
  6. POST   /api/v2/sessions/{sid}/confirm          → write permanent records
  7. DELETE /api/v2/sessions/{sid}                  → abandon + clean up temp files
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import date
from typing import AsyncIterator, List

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import verify_api_key
from app.db.models import (
    Card,
    CardMyCompany,
    CardSide,
    ContactDetail,
    Organization,
    OrganizationName,
    Person,
    PersonName,
    Position,
    PositionDetail,
    ScanSession,
    ScanSessionImage,
)
from app.db.session import get_db
from app.schemas.api import (
    AnalysisProgress,
    CardDraft,
    ConfirmRequest,
    ConfirmResponse,
    ConfirmedCardOut,
    ImageGroupUpdate,
    SessionCreate,
    SessionOut,
)
from app.services import card_detector, contact_matcher, correction_store, image_store
from app.services.claude_parser import stream_parse_card_sides
from app.services.contact_sync import auto_sync_card

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v2/sessions",
    tags=["sessions"],
    dependencies=[Depends(verify_api_key)],
)


# ---------------------------------------------------------------------------
# Helper: load session or 404
# ---------------------------------------------------------------------------

async def _get_session(db: AsyncSession, external_id: str) -> ScanSession:
    row = await db.scalar(
        select(ScanSession)
        .where(ScanSession.external_id == external_id)
        .options(selectinload(ScanSession.images))
    )
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    return row


# ---------------------------------------------------------------------------
# 1. Create session
# ---------------------------------------------------------------------------

@router.post("", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(body: SessionCreate, db: AsyncSession = Depends(get_db)):
    session = ScanSession(external_id=str(uuid.uuid4()), notes=body.notes)
    db.add(session)
    await db.flush()
    # Reload with relationships so Pydantic can serialize without lazy I/O
    return await _get_session(db, session.external_id)


# ---------------------------------------------------------------------------
# 2. Upload image
# ---------------------------------------------------------------------------

@router.post("/{sid}/images", status_code=status.HTTP_201_CREATED)
async def upload_image(
    sid: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    session = await _get_session(db, sid)
    if session.status not in ("uploading", "grouping"):
        raise HTTPException(400, "Session is not accepting new images")

    data = await file.read()
    filename = file.filename or f"{uuid.uuid4()}.jpg"
    rel_path, sha = image_store.save_temp_image(sid, filename, data)

    img = ScanSessionImage(
        session_id=session.id,
        image_path=rel_path,
        image_filename=filename,
    )
    db.add(img)
    await db.flush()

    session.status = "grouping"

    return {
        "id": img.id,
        "image_filename": filename,
        "image_path": rel_path,
        "sha256": sha,
        "temp_card_id": None,
        "side_order": None,
    }


# ---------------------------------------------------------------------------
# 2b. Serve temp image
# ---------------------------------------------------------------------------

@router.get("/{sid}/temp/{filename}")
async def get_temp_image(sid: str, filename: str):
    """Serve a temp image so the browser can preview it before analysis."""
    from app.config import settings
    path = settings.temp_path / sid / filename
    if not path.exists():
        raise HTTPException(404, "Image not found")
    return FileResponse(str(path))


# ---------------------------------------------------------------------------
# 3. Get session
# ---------------------------------------------------------------------------

@router.get("/{sid}", response_model=SessionOut)
async def get_session(sid: str, db: AsyncSession = Depends(get_db)):
    return await _get_session(db, sid)


# ---------------------------------------------------------------------------
# 3b. Manual crop
# ---------------------------------------------------------------------------

class CropRequest(BaseModel):
    x: int
    y: int
    width: int
    height: int
    natural_width: int   # original image pixel width (before browser scaling)
    natural_height: int  # original image pixel height (before browser scaling)

@router.post("/{sid}/images/{img_id}/crop")
async def crop_image(
    sid: str,
    img_id: int,
    body: CropRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Apply a manual crop to a session image.
    Coordinates are in the natural (original) pixel space of the image.
    Replaces the existing file in-place and returns the updated image record.
    """
    from PIL import Image as PILImage
    import io as _io

    session = await _get_session(db, sid)
    img = await db.scalar(
        select(ScanSessionImage).where(
            ScanSessionImage.id == img_id,
            ScanSessionImage.session_id == session.id,
        )
    )
    if not img:
        raise HTTPException(404, "Image not found in this session")

    from app.config import settings as _settings
    path = _settings.temp_path / img.image_path
    raw = path.read_bytes()
    pil_img = PILImage.open(_io.BytesIO(raw)).convert("RGB")
    iw, ih = pil_img.size

    # Scale crop coords from natural_width/height to actual image dimensions
    sx = iw / max(body.natural_width, 1)
    sy = ih / max(body.natural_height, 1)
    x1 = max(0, int(body.x * sx))
    y1 = max(0, int(body.y * sy))
    x2 = min(iw, int((body.x + body.width) * sx))
    y2 = min(ih, int((body.y + body.height) * sy))

    if x2 <= x1 or y2 <= y1:
        raise HTTPException(400, "Invalid crop region")

    cropped = pil_img.crop((x1, y1, x2, y2))
    buf = _io.BytesIO()
    cropped.save(buf, format="JPEG", quality=95)
    path.write_bytes(buf.getvalue())

    return {"id": img.id, "image_filename": img.image_filename}


# ---------------------------------------------------------------------------
# 3b2. Rotate image 90° clockwise
# ---------------------------------------------------------------------------

@router.post("/{sid}/images/{img_id}/rotate")
async def rotate_image(
    sid: str,
    img_id: int,
    direction: str = Query(default="cw", regex="^(cw|ccw)$"),
    db: AsyncSession = Depends(get_db),
):
    """Rotate the image 90° clockwise or counter-clockwise in-place."""
    from PIL import Image as PILImage
    import io as _io
    from app.config import settings as _settings

    session = await _get_session(db, sid)
    img = await db.scalar(
        select(ScanSessionImage).where(
            ScanSessionImage.id == img_id,
            ScanSessionImage.session_id == session.id,
        )
    )
    if not img:
        raise HTTPException(404, "Image not found in this session")

    path = _settings.temp_path / img.image_path
    raw = path.read_bytes()
    pil_img = PILImage.open(_io.BytesIO(raw)).convert("RGB")
    degrees = -90 if direction == "cw" else 90
    rotated = pil_img.rotate(degrees, expand=True)
    buf = _io.BytesIO()
    rotated.save(buf, format="JPEG", quality=95)
    path.write_bytes(buf.getvalue())

    return {"id": img.id, "image_filename": img.image_filename}


# ---------------------------------------------------------------------------
# 3c. Split multi-card image
# ---------------------------------------------------------------------------

@router.post("/{sid}/images/{img_id}/split")
async def split_image(sid: str, img_id: int, db: AsyncSession = Depends(get_db)):
    """
    Detect multiple business cards in a single uploaded image and replace it
    with individually-cropped images, one per detected card.

    Returns:
        { "split": bool, "images": [SessionImage...] }
        split=False means only one card was found; original image is kept.
    """
    session = await _get_session(db, sid)
    img = await db.scalar(
        select(ScanSessionImage).where(
            ScanSessionImage.id == img_id,
            ScanSessionImage.session_id == session.id,
        )
    )
    if not img:
        raise HTTPException(404, "Image not found in this session")

    try:
        results = await card_detector.detect_and_split(sid, img.image_path, img.image_filename)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    if len(results) == 1:
        # Single card — nothing to split
        return {
            "split": False,
            "images": [{"id": img.id, "image_filename": img.image_filename,
                        "temp_card_id": img.temp_card_id, "side_order": img.side_order}],
        }

    # Multiple cards — create new image records and delete original
    new_images = []
    for rel_path, filename, _ in results:
        new_img = ScanSessionImage(
            session_id=session.id,
            image_path=rel_path,
            image_filename=filename,
        )
        db.add(new_img)
        await db.flush()
        new_images.append({
            "id": new_img.id,
            "image_filename": filename,
            "temp_card_id": None,
            "side_order": None,
        })

    await db.delete(img)
    return {"split": True, "images": new_images}


# ---------------------------------------------------------------------------
# 3b. Count cards (used by manual-split flow)
# ---------------------------------------------------------------------------

@router.post("/{sid}/images/{img_id}/count-cards")
async def count_cards(sid: str, img_id: int, db: AsyncSession = Depends(get_db)):
    """Return the number of distinct business cards detected in the image."""
    session = await _get_session(db, sid)
    img = await db.scalar(
        select(ScanSessionImage).where(
            ScanSessionImage.id == img_id,
            ScanSessionImage.session_id == session.id,
        )
    )
    if not img:
        raise HTTPException(404, "Image not found in this session")
    try:
        count = await card_detector.count_cards_in_image(img.image_path)
    except Exception as exc:
        logger.warning("count_cards_in_image failed: %s", exc)
        count = 1
    return {"count": count}


class _Point(BaseModel):
    x: float
    y: float


# ---------------------------------------------------------------------------
# 3c. Detect card corners from a seed tap point
# ---------------------------------------------------------------------------

class DetectCornersRequest(BaseModel):
    x: float  # normalized [0, 1] tap x coordinate
    y: float  # normalized [0, 1] tap y coordinate
    existing_polygons: list[list[_Point]] = []  # already-defined card outlines to mask out


class DetectCornersResponse(BaseModel):
    corners: list[_Point]  # 4 points: TL, TR, BR, BL in normalized coords
    confidence: float      # 0.0 = fallback rectangle, 1.0 = high-confidence quad


@router.post("/{sid}/images/{img_id}/detect-corners", response_model=DetectCornersResponse)
async def detect_corners(
    sid: str,
    img_id: int,
    body: DetectCornersRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Given a seed tap point on the image, detect the 4 corners of the business
    card nearest that point using OpenCV contour detection.

    Returns 4 normalized corner coordinates (TL/TR/BR/BL) and a confidence
    score (0.0 = fell back to default rectangle, 1.0 = clear quad found).
    """
    session = await _get_session(db, sid)
    img = await db.scalar(
        select(ScanSessionImage).where(
            ScanSessionImage.id == img_id,
            ScanSessionImage.session_id == session.id,
        )
    )
    if not img:
        raise HTTPException(404, "Image not found in this session")

    existing = [
        [{"x": p.x, "y": p.y} for p in poly]
        for poly in body.existing_polygons
    ]
    corners, confidence = card_detector.detect_corners_from_seed(
        img.image_path, body.x, body.y, existing
    )
    return DetectCornersResponse(
        corners=[_Point(x=c["x"], y=c["y"]) for c in corners],
        confidence=confidence,
    )


class ManualSplitRequest(BaseModel):
    polygons: list[list[_Point]]  # one 4-point polygon per card


@router.post("/{sid}/images/{img_id}/manual-split")
async def manual_split(
    sid: str,
    img_id: int,
    body: ManualSplitRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Crop individual cards from an image using user-defined 4-corner polygons.
    Replaces the original image with the individually cropped cards.
    """
    session = await _get_session(db, sid)
    img = await db.scalar(
        select(ScanSessionImage).where(
            ScanSessionImage.id == img_id,
            ScanSessionImage.session_id == session.id,
        )
    )
    if not img:
        raise HTTPException(404, "Image not found in this session")

    raw_polys = [[{"x": p.x, "y": p.y} for p in poly] for poly in body.polygons]
    try:
        results = await card_detector.manual_crop_cards(
            sid, img.image_path, img.image_filename, raw_polys
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    if len(results) == 1 and len(body.polygons) == 1:
        # Only one card outlined — nothing to split, keep original
        return {
            "split": False,
            "images": [{"id": img.id, "image_filename": img.image_filename,
                        "temp_card_id": img.temp_card_id, "side_order": img.side_order}],
        }

    new_images = []
    for rel_path, filename, _ in results:
        new_img = ScanSessionImage(
            session_id=session.id,
            image_path=rel_path,
            image_filename=filename,
        )
        db.add(new_img)
        await db.flush()
        new_images.append({
            "id": new_img.id,
            "image_filename": filename,
            "temp_card_id": None,
            "side_order": None,
        })

    await db.delete(img)
    return {"split": True, "images": new_images}


# ---------------------------------------------------------------------------
# 4. Update image grouping
# ---------------------------------------------------------------------------

@router.patch("/{sid}/images/{img_id}")
async def update_image_group(
    sid: str,
    img_id: int,
    body: ImageGroupUpdate,
    db: AsyncSession = Depends(get_db),
):
    session = await _get_session(db, sid)
    img = await db.scalar(
        select(ScanSessionImage).where(
            ScanSessionImage.id == img_id,
            ScanSessionImage.session_id == session.id,
        )
    )
    if not img:
        raise HTTPException(404, "Image not found in this session")

    img.temp_card_id = body.temp_card_id
    img.side_order = body.side_order
    await db.flush()
    return {"id": img.id, "temp_card_id": img.temp_card_id, "side_order": img.side_order}


# ---------------------------------------------------------------------------
# 5. Analyze — SSE stream
# ---------------------------------------------------------------------------

@router.post("/{sid}/analyze")
async def analyze_session(sid: str, db: AsyncSession = Depends(get_db)):
    """
    For each temp_card_id group, call Claude Vision and run contact matching.
    Streams Server-Sent Events:
      data: {"type": "progress", "temp_card_id": "...", "message": "..."}
      data: {"type": "result",   "temp_card_id": "...", "parsed": {...}, "match": {...}}
      data: {"type": "error",    "temp_card_id": "...", "error": "..."}
      data: {"type": "done"}
    """
    session = await _get_session(db, sid)
    if session.status not in ("grouping", "analyzing"):
        raise HTTPException(400, "Session must be in grouping state to analyze")

    session.status = "analyzing"

    # Group images by temp_card_id
    grouped: dict[str, list[ScanSessionImage]] = {}
    for img in session.images:
        if img.temp_card_id:
            grouped.setdefault(img.temp_card_id, []).append(img)

    if not grouped:
        raise HTTPException(400, "No grouped images to analyze")

    # Fetch few-shot corrections once
    corrections = await correction_store.get_few_shot_examples(db)
    few_shot_block = correction_store.format_for_prompt(corrections)

    async def event_stream() -> AsyncIterator[str]:
        for temp_card_id, images in grouped.items():
            # Sort by side_order
            images_sorted = sorted(images, key=lambda i: i.side_order or 0)
            side_paths = [img.image_path for img in images_sorted]

            try:
                async for chunk in stream_parse_card_sides(side_paths, few_shot_block):
                    if chunk.startswith("result:"):
                        from app.schemas.parsed_card import ParsedCard as PC
                        parsed = PC.model_validate_json(chunk[7:])
                        match = await contact_matcher.find_match(db, parsed)
                        event = AnalysisProgress(
                            type="result",
                            temp_card_id=temp_card_id,
                            parsed=parsed,
                            match=match,
                        )
                        yield f"data: {event.model_dump_json()}\n\n"
                    else:
                        event = AnalysisProgress(
                            type="progress",
                            temp_card_id=temp_card_id,
                            message=chunk,
                        )
                        yield f"data: {event.model_dump_json()}\n\n"
            except Exception as exc:
                logger.exception("Analysis failed for temp_card_id=%s", temp_card_id)
                event = AnalysisProgress(
                    type="error",
                    temp_card_id=temp_card_id,
                    error=str(exc),
                )
                yield f"data: {event.model_dump_json()}\n\n"

        yield f"data: {AnalysisProgress(type='done').model_dump_json()}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# 6. Confirm — write permanent records
# ---------------------------------------------------------------------------

async def _find_or_create_org(
    db: AsyncSession, org_names: list
) -> Organization:
    """Find existing org by any of its names, or create a new one."""
    for on in org_names:
        name_val = on.name.value if hasattr(on.name, "value") else on.name
        existing_id = await db.scalar(
            select(OrganizationName.org_id).where(
                OrganizationName.name == name_val,
                OrganizationName.is_current == True,  # noqa: E712
            )
        )
        if existing_id:
            org = await db.get(Organization, existing_id)
            if org:
                return org

    org = Organization(external_id=str(uuid.uuid4()))
    db.add(org)
    await db.flush()

    for on in org_names:
        lang = on.language
        name_val = on.name.value if hasattr(on.name, "value") else on.name
        if name_val:
            db.add(OrganizationName(
                org_id=org.id,
                language=lang,
                name=name_val,
                is_current=True,
                valid_from=date.today(),
                source="card",
            ))
    return org


async def _upsert_person_names(db: AsyncSession, person_id: int, parsed_card) -> None:
    for pn in parsed_card.names:
        full = pn.full_name.value if hasattr(pn.full_name, "value") else pn.full_name
        if not full:
            continue
        existing = await db.scalar(
            select(PersonName).where(
                PersonName.person_id == person_id,
                PersonName.language == pn.language,
                PersonName.name_type == pn.name_type,
                PersonName.is_current == True,  # noqa: E712
            )
        )
        if existing and existing.full_name == full:
            continue  # unchanged — skip
        if existing:
            existing.is_current = False
            existing.valid_to = date.today()
        db.add(PersonName(
            person_id=person_id,
            language=pn.language,
            name_type=pn.name_type,
            family_name=(pn.family_name.value if pn.family_name and hasattr(pn.family_name, "value") else None),
            given_name=(pn.given_name.value if pn.given_name and hasattr(pn.given_name, "value") else None),
            full_name=full,
            is_current=True,
            valid_from=date.today(),
            source="card",
        ))


async def _upsert_contact_details(
    db: AsyncSession, person_id: int, card_id: int, parsed_card
) -> None:
    for cd in parsed_card.contact_details:
        val = cd.value.value if hasattr(cd.value, "value") else cd.value
        if not val:
            continue
        existing = await db.scalar(
            select(ContactDetail).where(
                ContactDetail.person_id == person_id,
                ContactDetail.detail_type == cd.detail_type,
                ContactDetail.value == val,
            )
        )
        if not existing:
            db.add(ContactDetail(
                person_id=person_id,
                card_id=card_id,
                detail_type=cd.detail_type,
                value=val,
                label=cd.label,
                country_code=cd.country_code if cd.detail_type.startswith("address_") else None,
            ))


async def _confirm_one_card(
    db: AsyncSession,
    session: ScanSession,
    draft: CardDraft,
) -> ConfirmedCardOut:
    # 1. Person: find existing or create
    if draft.match_person_id:
        person = await db.get(Person, draft.match_person_id)
        if not person:
            raise HTTPException(404, f"Person {draft.match_person_id} not found")
    else:
        person = Person(external_id=str(uuid.uuid4()))
        db.add(person)
        await db.flush()

    # 1b. Birthday — set only when the card provides one (never clobber with blank)
    if draft.parsed.birthday:
        person.birthday = draft.parsed.birthday

    # 2. Names
    await _upsert_person_names(db, person.id, draft.parsed)

    # 3. Organizations + Positions
    for parsed_pos in draft.parsed.positions:
        if not parsed_pos.org_names:
            continue
        org = await _find_or_create_org(db, parsed_pos.org_names)

        # Find or create position
        pos = await db.scalar(
            select(Position).where(
                Position.person_id == person.id,
                Position.org_id == org.id,
                Position.status == "current",
            )
        )
        if not pos:
            pos = Position(
                person_id=person.id,
                org_id=org.id,
                status="current",
                started_on=date.today(),
            )
            db.add(pos)
            await db.flush()

        for detail in parsed_pos.details:
            title_val = (detail.title.value if detail.title and hasattr(detail.title, "value") else None)
            dept_val = (detail.department.value if detail.department and hasattr(detail.department, "value") else None)
            existing_detail = await db.scalar(
                select(PositionDetail).where(
                    PositionDetail.position_id == pos.id,
                    PositionDetail.language == detail.language,
                )
            )
            if existing_detail:
                if title_val:
                    existing_detail.title = title_val
                if dept_val:
                    existing_detail.department = dept_val
            else:
                db.add(PositionDetail(
                    position_id=pos.id,
                    language=detail.language,
                    title=title_val,
                    department=dept_val,
                ))

    # 4. Card record
    card_ext_id = str(uuid.uuid4())
    card = Card(
        external_id=card_ext_id,
        person_id=person.id,
        occasion_id=draft.occasion_id,
        received_date=draft.received_date,
        notes=draft.notes,
        sync_status="pending",
    )
    db.add(card)
    await db.flush()

    # 5. My company links
    for mc_id in draft.my_company_ids:
        db.add(CardMyCompany(card_id=card.id, my_company_id=mc_id))

    # 6. Contact details (after card.id is available)
    await _upsert_contact_details(db, person.id, card.id, draft.parsed)

    # 7. Card sides: move temp images to permanent storage
    imgs = await db.execute(
        select(ScanSessionImage).where(
            ScanSessionImage.session_id == session.id,
            ScanSessionImage.temp_card_id == draft.temp_card_id,
        ).order_by(ScanSessionImage.side_order)
    )
    for img in imgs.scalars():
        rel_path, filename, sha, w, h = image_store.move_to_permanent(
            card_ext_id, img.side_order or 0, img.image_path
        )
        db.add(CardSide(
            card_id=card.id,
            side_order=img.side_order or 0,
            image_path=rel_path,
            image_filename=filename,
            image_hash=sha,
            width_px=w,
            height_px=h,
        ))

    return ConfirmedCardOut(
        temp_card_id=draft.temp_card_id,
        card_id=card.id,
        card_external_id=card_ext_id,
        person_id=person.id,
        person_external_id=person.external_id,
    )


@router.post("/{sid}/confirm", response_model=ConfirmResponse)
async def confirm_session(
    sid: str,
    body: ConfirmRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    session = await _get_session(db, sid)
    if session.status not in ("uploading", "grouping", "analyzing", "review"):
        raise HTTPException(400, "Session is not in a confirmable state")

    confirmed = []
    for draft in body.cards:
        result = await _confirm_one_card(db, session, draft)
        confirmed.append(result)

    # Mark session done and commit BEFORE deleting temp files.
    # Temp files are deleted as a background task only after the commit
    # succeeds — this prevents data loss if the commit fails.
    from datetime import datetime
    session.status = "done"
    session.completed_at = datetime.utcnow()
    await db.commit()

    background_tasks.add_task(image_store.delete_temp_session, sid)
    for result in confirmed:
        background_tasks.add_task(auto_sync_card, result.card_id)
    return ConfirmResponse(confirmed=confirmed)


# ---------------------------------------------------------------------------
# 7. Abandon session
# ---------------------------------------------------------------------------

@router.delete("/{sid}", status_code=status.HTTP_204_NO_CONTENT)
async def abandon_session(sid: str, db: AsyncSession = Depends(get_db)):
    session = await _get_session(db, sid)
    image_store.delete_temp_session(sid)
    session.status = "abandoned"
    await db.flush()
