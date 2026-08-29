"""
Image storage service.

Layout on disk:
  ~/.nxt-a1/
    temp/
      {session_ext_id}/
        {filename}           ← raw upload, lives here until Confirm
    images/
      {card_ext_id}/
        {side_order}.jpg     ← permanent after Confirm

All paths stored in the DB are RELATIVE to their respective root
(temp_path or images_path), e.g. "{session_id}/0001.jpg".
"""
from __future__ import annotations

import base64
import hashlib
import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

from PIL import Image

from app.config import settings

logger = logging.getLogger(__name__)

MAX_DIMENSION = 1568  # Claude Vision optimal max


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _resize_bytes(data: bytes, max_dim: int = MAX_DIMENSION) -> bytes:
    """Resize image so longest side ≤ max_dim. Returns JPEG bytes."""
    img = Image.open(io.BytesIO(data))
    if max(img.size) > max_dim:
        ratio = max_dim / max(img.size)
        img = img.resize(
            (int(img.width * ratio), int(img.height * ratio)),
            Image.LANCZOS,
        )
    buf = io.BytesIO()
    img = img.convert("RGB")  # ensure JPEG-compatible
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def image_size(data: bytes) -> Tuple[int, int]:
    img = Image.open(io.BytesIO(data))
    return img.size  # (width, height)


def bytes_to_b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


# ---------------------------------------------------------------------------
# Temp storage  (during scan session, before Confirm)
# ---------------------------------------------------------------------------

def save_temp_image(session_ext_id: str, filename: str, data: bytes) -> Tuple[str, str]:
    """
    Save upload to temp directory, applying EXIF orientation so the stored
    pixels match what browsers display. All downstream code (cropping, Claude
    Vision, coordinate mapping) then works on consistently-oriented pixels.

    Returns:
        (relative_path, sha256_hash)
        relative_path is relative to settings.temp_path,
        e.g. "{session_ext_id}/original_filename.jpg"
    """
    from PIL import ImageOps
    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)  # bake EXIF rotation into pixels
    img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    data = buf.getvalue()

    dest_dir = settings.temp_path / session_ext_id
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest = dest_dir / filename
    dest.write_bytes(data)

    relative = f"{session_ext_id}/{filename}"
    sha = _sha256(data)
    logger.debug("Saved temp image %s (%d bytes, sha=%s)", relative, len(data), sha[:8])
    return relative, sha


def read_temp_image(relative_path: str) -> bytes:
    return (settings.temp_path / relative_path).read_bytes()


def delete_temp_session(session_ext_id: str) -> None:
    """Remove all temp files for a session (called after Confirm or abandon)."""
    import shutil
    target = settings.temp_path / session_ext_id
    if target.exists():
        shutil.rmtree(target)
        logger.debug("Deleted temp session dir: %s", target)


# ---------------------------------------------------------------------------
# Permanent storage  (after Confirm)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PreparedImage:
    """
    A temp image already read, resized and hashed, but NOT yet written to
    permanent storage. See prepare_permanent() for why the two are separate.
    """
    relative_path: str      # relative to settings.images_path, e.g. "{card_ext_id}/0.jpg"
    filename: str           # "{side_order}.jpg"
    sha256: str
    width_px: int
    height_px: int
    data: bytes             # resized JPEG bytes, held until write_prepared()


def prepare_permanent_bytes(
    card_ext_id: str,
    side_order: int,
    data: bytes,
) -> PreparedImage:
    """
    Resize raw image bytes to MAX_DIMENSION and return everything the CardSide
    row needs — but touch nothing on disk.

    Writing is deferred to write_prepared() so that callers can commit their
    database transaction FIRST. Writing before the commit means a rolled-back
    transaction leaves orphaned files behind, and — because the destination path
    is derived from side_order — a duplicated side_order silently overwrites a
    sibling side's image.
    """
    resized = _resize_bytes(data)
    sha = _sha256(resized)
    w, h = image_size(resized)

    filename = f"{side_order}.jpg"
    return PreparedImage(
        relative_path=f"{card_ext_id}/{filename}",
        filename=filename,
        sha256=sha,
        width_px=w,
        height_px=h,
        data=resized,
    )


def prepare_permanent(
    card_ext_id: str,
    side_order: int,
    temp_relative_path: str,
) -> PreparedImage:
    """Same as prepare_permanent_bytes(), sourcing the bytes from temp storage."""
    return prepare_permanent_bytes(card_ext_id, side_order, read_temp_image(temp_relative_path))


def write_prepared(prepared: PreparedImage) -> None:
    """Flush a PreparedImage to permanent storage. Call only after the commit."""
    dest = settings.images_path / prepared.relative_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(prepared.data)
    logger.debug(
        "Saved permanent image %s (%dx%d, sha=%s)",
        prepared.relative_path, prepared.width_px, prepared.height_px, prepared.sha256[:8],
    )


def delete_permanent_image(relative_path: str) -> None:
    """
    Remove one permanent image. Call only after the row referencing it is
    committed as deleted — otherwise a failed commit leaves a card_sides row
    pointing at a file that no longer exists.

    The card's directory is left in place: a card always keeps at least one
    side, so it is never emptied by this call.
    """
    root = settings.images_path.resolve()
    target = (settings.images_path / relative_path).resolve()
    if not target.is_relative_to(root):
        # Paths come from our own rows, but never let one escape the store.
        logger.error("Refusing to delete image outside the image store: %s", relative_path)
        return
    target.unlink(missing_ok=True)
    logger.debug("Deleted permanent image %s", relative_path)


def read_permanent_image(relative_path: str) -> bytes:
    return (settings.images_path / relative_path).read_bytes()


def permanent_image_as_b64(relative_path: str) -> str:
    return bytes_to_b64(read_permanent_image(relative_path))


def temp_image_as_b64(relative_path: str) -> str:
    return bytes_to_b64(read_temp_image(relative_path))


def temp_image_resized_b64(relative_path: str, max_dim: int = MAX_DIMENSION) -> str:
    """Read a temp image, resize it, return as base64 for Claude."""
    raw = read_temp_image(relative_path)
    resized = _resize_bytes(raw, max_dim)
    return bytes_to_b64(resized)
