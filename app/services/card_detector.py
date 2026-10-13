"""
Business-card photo helpers for the manual-outline capture flow.

  1. count_cards_in_image   — Claude Vision counts the cards in a photo
  2. detect_corners_from_seed — OpenCV finds a card's 4 corners from a user tap
  3. manual_crop_cards      — perspective-warps each user-outlined card into
                              a flat, straight image

Cropping is pure OpenCV; Claude is only used for counting here.
"""
from __future__ import annotations

import base64
import io
import logging

import anthropic
import cv2
import numpy as np
from PIL import Image

from app.config import settings
from app.services.image_store import read_temp_image, save_temp_image, _resize_bytes

logger = logging.getLogger(__name__)


def _sort_quad_points(pts: np.ndarray) -> np.ndarray:
    """
    Sort 4 points into [top-left, top-right, bottom-right, bottom-left] order.

    1. Order the points clockwise by angle from the centroid (rotation-
       invariant — coordinate sums/diffs break on rotated cards).
    2. Start at the top-left corner, defined as the start of the edge that
       points most nearly rightward when walking clockwise — i.e. assume the
       card is rotated the least amount from upright. (Clockwise, the top
       edge runs →, right ↓, bottom ←, left ↑.) Starting at a fixed angle
       instead (e.g. -135°) fails for landscape cards, whose TL corner sits
       at ~-150° — that returned BL first and rotated every crop 90°.
    """
    cx = np.mean(pts[:, 0])
    cy = np.mean(pts[:, 1])

    # Image y points down, so ascending atan2 angle runs clockwise on screen
    angles = np.arctan2(pts[:, 1] - cy, pts[:, 0] - cx)
    cw = pts[np.argsort(angles)]

    # Angle of each clockwise edge vs. the +x axis; the top edge is closest to 0
    edge_angles = [
        abs(np.arctan2(cw[(i + 1) % 4, 1] - cw[i, 1], cw[(i + 1) % 4, 0] - cw[i, 0]))
        for i in range(4)
    ]
    top = int(np.argmin(edge_angles))
    return np.roll(cw, -top, axis=0).astype(np.float32)


# ── Seed-based corner detection ───────────────────────────────────────────────
#
# Two methods, tried in order:
#   1. Background-colour segmentation — models the table colour from the
#      photo's border and treats everything sufficiently different as "card".
#      Robust to text/logos inside the card, textured tables (wood, cutting
#      mats) and shadow gradients.
#   2. Canny edges + contours (the original method) — fallback for cases the
#      colour method can't separate: card colour ≈ table colour, or cards
#      touching each other with no outline yet to separate them.

BG_BORDER_FRAC = 0.03       # width of the border band used to model the table
BG_MIN_THRESH = 10.0        # minimum Lab distance (≈ ΔE) to count as "not table"
BG_SPREAD_FACTOR = 1.6      # threshold = factor × 90th-pct border residual
BG_CARD_ASPECT = (1.2, 2.4) # long/short side ratio accepted as a business card
BG_MIN_FILL = 0.85          # blob area / quad area — rejects irregular blobs


def _fallback_rect(seed_x: float, seed_y: float) -> list[dict]:
    """Axis-aligned rectangle centred on the seed, standard ~1.75:1 card aspect."""
    card_w = 0.35
    card_h = card_w / 1.75
    x1 = max(0.0, seed_x - card_w / 2)
    y1 = max(0.0, seed_y - card_h / 2)
    x2 = min(1.0, seed_x + card_w / 2)
    y2 = min(1.0, seed_y + card_h / 2)
    return [{"x": x1, "y": y1}, {"x": x2, "y": y1}, {"x": x2, "y": y2}, {"x": x1, "y": y2}]


def _polygons_to_px(polygons: list[list[dict]], img_w: int, img_h: int) -> list[np.ndarray]:
    """Normalized polygons → int32 pixel arrays for cv2 drawing."""
    return [
        np.array([[int(p["x"] * img_w), int(p["y"] * img_h)] for p in poly], dtype=np.int32)
        for poly in polygons
    ]


def _fit_table_surface(lab: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    Model the table colour across the whole image from the border band only.

    Fits a quadratic surface per Lab channel to the border pixels. Lighting
    falloff and phone shadows are smooth, so this predicts the table colour
    under the cards without ever looking at them.

    Returns (surface HxWx3, residual distance of each border pixel to it).
    The residuals measure how textured/noisy the table is.
    """
    h, w = lab.shape[:2]
    b = max(2, int(min(h, w) * BG_BORDER_FRAC))
    border = np.zeros((h, w), bool)
    border[:b] = border[-b:] = True
    border[:, :b] = border[:, -b:] = True

    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    xn, yn = xx / w - 0.5, yy / h - 0.5
    basis = np.stack([np.ones_like(xn), xn, yn, xn * xn, yn * yn, xn * yn], axis=-1)

    # Subsample border pixels — thousands are plenty for 6 coefficients
    coef, *_ = np.linalg.lstsq(basis[border][::7], lab[border][::7], rcond=None)
    surface = basis @ coef
    residual = np.linalg.norm(lab[border] - surface[border], axis=1)
    return surface, residual


def _quad_from_blob(contour: np.ndarray) -> np.ndarray:
    """Best 4-point approximation of a blob outline; minAreaRect as last resort."""
    hull = cv2.convexHull(contour)
    arc = cv2.arcLength(hull, True)
    for eps in (0.01, 0.02, 0.03, 0.04, 0.06, 0.08):
        approx = cv2.approxPolyDP(hull, eps * arc, True)
        if len(approx) == 4:
            return approx.reshape(4, 2).astype(np.float32)
    return cv2.boxPoints(cv2.minAreaRect(contour)).astype(np.float32)


def _detect_quad_by_background(
    img_bgr: np.ndarray,
    seed_px: int,
    seed_py: int,
    existing_polygons: list[list[dict]] | None,
) -> np.ndarray | None:
    """
    Primary method: segment "not table" pixels by colour, take the blob under
    the seed, fit 4 corners.

    Returns a 4x2 float32 pixel quad, or None when the result isn't a
    plausible single card (caller then falls back to the edge method).
    """
    img_h, img_w = img_bgr.shape[:2]
    lab = cv2.cvtColor(cv2.GaussianBlur(img_bgr, (5, 5), 0), cv2.COLOR_BGR2LAB).astype(np.float32)
    lab[..., 0] *= 100.0 / 255.0  # OpenCV 8-bit L is 0..255; rescale so units ≈ ΔE

    # 1. Table model + a threshold that scales with how textured the table is
    surface, residual = _fit_table_surface(lab)
    thresh = max(BG_MIN_THRESH, float(np.percentile(residual, 90)) * BG_SPREAD_FACTOR)

    # 2. Foreground = far from the modelled table colour
    dist = np.linalg.norm(lab - surface, axis=2)
    fg = (dist > thresh).astype(np.uint8) * 255

    # 3. Blank out cards the user already outlined, plus a thin line along
    #    their outline, so a touching neighbour becomes a separate blob
    if existing_polygons:
        line_w = max(3, int(min(img_h, img_w) * 0.004))
        for pts in _polygons_to_px(existing_polygons, img_w, img_h):
            cv2.fillPoly(fg, [pts], 0)
            cv2.polylines(fg, [pts], True, 0, thickness=line_w)

    # 4. Open: removes grid lines / wood grain specks and thin bridges.
    #    Close: seals small notches along the card edge.
    k = max(3, int(min(img_h, img_w) * 0.008)) | 1
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, kernel)
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # 5. Blob containing the seed, or the nearest one within 3% of the frame
    #    (a tap right on the card edge can land just outside the blob)
    best, best_dist = None, -min(img_h, img_w) * 0.03
    for c in contours:
        if cv2.contourArea(c) < img_w * img_h * 0.005:
            continue
        d = cv2.pointPolygonTest(c, (float(seed_px), float(seed_py)), True)
        if d > best_dist:
            best, best_dist = c, d
    if best is None:
        logger.info("detect_corners bg: no blob near seed (thresh=%.1f)", thresh)
        return None

    # 6. Sanity: card-sized, card-shaped, and the quad must fit the blob well.
    #    Merged cards, partial blobs and table texture fail at least one check.
    quad = _quad_from_blob(best)
    quad_area = cv2.contourArea(quad)
    (_, _), (rw, rh), _ = cv2.minAreaRect(quad)
    aspect = max(rw, rh) / max(1.0, min(rw, rh))
    fill = cv2.contourArea(best) / max(1.0, quad_area)
    ok = (
        img_w * img_h * 0.01 <= quad_area <= img_w * img_h * 0.85
        and BG_CARD_ASPECT[0] <= aspect <= BG_CARD_ASPECT[1]
        and fill >= BG_MIN_FILL
    )
    logger.info(
        "detect_corners bg: thresh=%.1f area=%.3f aspect=%.2f fill=%.2f → %s",
        thresh, quad_area / (img_w * img_h), aspect, fill, "accepted" if ok else "rejected",
    )
    return quad if ok else None


def _detect_quad_by_edges(
    img_bgr: np.ndarray,
    seed_px: int,
    seed_py: int,
    existing_polygons: list[list[dict]] | None,
) -> np.ndarray | None:
    """
    Fallback method: Canny edges + contour quadrilateral approximation.

    Returns the smallest card-shaped quad containing the seed, or None.
    """
    img_h, img_w = img_bgr.shape[:2]
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    # Use low Canny thresholds so dark-card boundaries (moderate gradient) are
    # detected. The global image mean is not a reliable signal here — a photo
    # with a dark card on a grey table has mean ≈ 150 but the card edge gradient
    # is only ~30-60 Sobel magnitude. Higher thresholds miss those edges.
    edges = cv2.Canny(blurred, 20, 80)

    # Dilate edges to close gaps in the card boundary.
    # Dark cards have a nearly-complete outer edge ring from Canny but with
    # gaps — especially along the card-background transition where the contrast
    # gradient is gradual. Without closing those gaps, findContours can't find
    # a single closed loop that encloses the card interior. 8 iterations of
    # 3×3 dilation bridges gaps up to ~17 px while still leaving interior
    # features (text, logos) as separate, smaller regions.
    k_dil = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    edges = cv2.dilate(edges, k_dil, iterations=8)

    # Erase the interiors of already-defined card outlines from the edge map.
    # This breaks multi-card spanning contours that form when dilation bridges
    # adjacent card edges. We erode the mask by 3px so the shared boundary
    # between an existing card and the new card is preserved as a Canny edge.
    if existing_polygons:
        exclude_mask = np.zeros((img_h, img_w), dtype=np.uint8)
        for pts in _polygons_to_px(existing_polygons, img_w, img_h):
            cv2.fillPoly(exclude_mask, [pts], 255)
        k_erode = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        exclude_mask = cv2.erode(exclude_mask, k_erode, iterations=3)
        edges[exclude_mask > 0] = 0
        logger.info(
            "detect_corners_from_seed: masked %d existing polygon(s) from edge map",
            len(existing_polygons),
        )

    # RETR_LIST finds all contours. We filter by:
    # 1. Area 1%–85% of image
    # 2. Contains seed point
    # 3. Aspect ratio within business-card range (0.25–2.5) — rejects header
    #    bands and other sub-card rectangles that are too wide/short
    # 4. Smallest area wins — tightest single-card match, avoids multi-card spanning contours.
    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    MIN_AREA = img_w * img_h * 0.01
    MAX_AREA = img_w * img_h * 0.85
    ASPECT_MIN, ASPECT_MAX = 0.25, 2.5  # standard cards: ~0.6 (portrait) or ~1.64 (landscape)
    seed_brightness = float(blurred[seed_py, seed_px])

    best_quad: np.ndarray | None = None
    best_area = float('inf')  # pick smallest valid quad (single card, not multi-card spanning contour)

    logger.info(
        "detect_corners_from_seed v4: seed_px=(%d,%d) brightness=%.0f img=%dx%d contours=%d",
        seed_px, seed_py, seed_brightness, img_w, img_h, len(contours),
    )

    for contour in contours:
        area = cv2.contourArea(contour)
        if area < MIN_AREA or area > MAX_AREA:
            continue
        if cv2.pointPolygonTest(contour, (float(seed_px), float(seed_py)), False) < 0:
            continue
        arc = cv2.arcLength(contour, True)
        for eps_factor in (0.02, 0.04, 0.06, 0.08, 0.10, 0.15):
            approx = cv2.approxPolyDP(contour, eps_factor * arc, True)
            if len(approx) == 4:
                pts = approx.reshape(4, 2).astype(np.float32)
                x_ext = float(pts[:, 0].max() - pts[:, 0].min())
                y_ext = float(pts[:, 1].max() - pts[:, 1].min())
                ar = (x_ext / y_ext) if y_ext > 0 else 999.0
                logger.info("  quad candidate: area=%.0f ar=%.2f %s",
                            area, ar, "REJECTED" if ar < ASPECT_MIN or ar > ASPECT_MAX else "accepted")
                if ar < ASPECT_MIN or ar > ASPECT_MAX:
                    break  # bad aspect ratio — skip this contour
                if area < best_area:
                    best_area = area
                    best_quad = pts
                break

    return best_quad


def detect_corners_from_seed(
    temp_relative_path: str,
    seed_x: float,
    seed_y: float,
    existing_polygons: list[list[dict]] | None = None,
) -> tuple[list[dict], float]:
    """
    Find the 4 corners of the business card nearest the seed point.

    Tries background-colour segmentation first, then Canny edge contours.
    If neither finds a card-shaped quad, returns a default rectangle
    centered on the seed with confidence 0.0.

    Args:
        temp_relative_path: Relative path under TEMP_DIR (passed to read_temp_image).
        seed_x: Normalized [0, 1] x coordinate of the tap point.
        seed_y: Normalized [0, 1] y coordinate of the tap point.
        existing_polygons: Cards the user already outlined (normalized); masked
            out so they don't merge with the card being detected.

    Returns:
        (corners, confidence)
        corners — list of 4 dicts {"x": float, "y": float} in TL/TR/BR/BL order,
                  coordinates normalized to [0, 1].
        confidence — 0.0 (fallback rectangle) to 1.0 (high-confidence quad).
    """
    raw = read_temp_image(temp_relative_path)
    working = _resize_bytes(raw)
    try:
        img_pil = Image.open(io.BytesIO(working)).convert("RGB")
    except Exception:
        logger.warning("detect_corners_from_seed: could not decode image at %s", temp_relative_path)
        return [{"x": 0.15, "y": 0.25}, {"x": 0.85, "y": 0.25},
                {"x": 0.85, "y": 0.75}, {"x": 0.15, "y": 0.75}], 0.0
    img_w, img_h = img_pil.size
    if img_w == 0 or img_h == 0:
        return [{"x": 0.15, "y": 0.25}, {"x": 0.85, "y": 0.25},
                {"x": 0.85, "y": 0.75}, {"x": 0.15, "y": 0.75}], 0.0

    # Clamp seed to valid pixel range
    seed_px = int(max(0, min(img_w - 1, seed_x * img_w)))
    seed_py = int(max(0, min(img_h - 1, seed_y * img_h)))
    img_bgr = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)

    method = "background"
    quad = _detect_quad_by_background(img_bgr, seed_px, seed_py, existing_polygons)
    if quad is None:
        method = "edges"
        quad = _detect_quad_by_edges(img_bgr, seed_px, seed_py, existing_polygons)

    if quad is None:
        logger.info(
            "detect_corners_from_seed: no quad found for seed=(%.3f,%.3f) → fallback rectangle",
            seed_x, seed_y,
        )
        return _fallback_rect(seed_x, seed_y), 0.0

    sorted_pts = _sort_quad_points(quad)
    corners = [{"x": float(p[0] / img_w), "y": float(p[1] / img_h)} for p in sorted_pts]
    # Scale confidence: 20% of image area → 1.0
    area = float(cv2.contourArea(quad))
    confidence = float(min(1.0, area / (img_w * img_h) * 5))
    logger.info(
        "detect_corners_from_seed: seed=(%.3f,%.3f) method=%s quad area=%.0f confidence=%.2f",
        seed_x, seed_y, method, area, confidence,
    )
    return corners, confidence


async def count_cards_in_image(temp_relative_path: str) -> int:
    """
    Ask Claude Vision how many distinct business cards are in the photo.
    Returns the count (1 if uncertain or on error).
    """
    raw = read_temp_image(temp_relative_path)
    working = _resize_bytes(raw)
    b64 = base64.b64encode(working).decode()
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key or None)
    resp = await client.messages.create(
        model=settings.claude_model,
        max_tokens=64,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
                {"type": "text", "text": (
                    "How many distinct business cards are visible in this photo? "
                    "Reply with a single integer and nothing else."
                )},
            ],
        }],
    )
    text = resp.content[0].text.strip()
    try:
        count = int("".join(c for c in text if c.isdigit()) or "1")
    except ValueError:
        count = 1
    logger.info("Card count response: %r → %d", text, count)
    return max(1, count)


async def manual_crop_cards(
    session_ext_id: str,
    temp_relative_path: str,
    original_filename: str,
    polygons: list[list[dict]],
) -> list[tuple[str, str, str]]:
    """
    Crop cards using user-defined 4-corner polygons (perspective warp).

    Each polygon is a list of 4 points with normalised coordinates:
        [{"x": 0.0..1.0, "y": 0.0..1.0}, ...]   (TL → TR → BR → BL order)

    Returns a list of (relative_path, filename, sha256) — one per polygon.
    """
    raw = read_temp_image(temp_relative_path)
    working = _resize_bytes(raw)
    img_pil = Image.open(io.BytesIO(working)).convert("RGB")
    img_w, img_h = img_pil.size
    img_bgr = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)

    stem = original_filename.rsplit(".", 1)[0]
    results: list[tuple[str, str, str]] = []

    for i, poly in enumerate(polygons):
        if len(poly) != 4:
            logger.warning("Polygon %d has %d points (expected 4), skipping", i, len(poly))
            continue

        # Convert normalised → pixel coordinates
        src = np.array(
            [[p["x"] * img_w, p["y"] * img_h] for p in poly],
            dtype=np.float32,
        )

        # Compute output rectangle dimensions from the four corners
        # Top edge width, bottom edge width, left edge height, right edge height
        w_top = float(np.linalg.norm(src[1] - src[0]))
        w_bot = float(np.linalg.norm(src[2] - src[3]))
        h_left = float(np.linalg.norm(src[3] - src[0]))
        h_right = float(np.linalg.norm(src[2] - src[1]))
        out_w = max(1, int(max(w_top, w_bot)))
        out_h = max(1, int(max(h_left, h_right)))

        dst = np.array(
            [[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]],
            dtype=np.float32,
        )
        M = cv2.getPerspectiveTransform(src, dst)
        warped_bgr = cv2.warpPerspective(img_bgr, M, (out_w, out_h))
        warped_rgb = cv2.cvtColor(warped_bgr, cv2.COLOR_BGR2RGB)
        card_img = Image.fromarray(warped_rgb)

        buf = io.BytesIO()
        card_img.save(buf, format="JPEG", quality=95)
        crop_bytes = buf.getvalue()

        filename = f"{stem}_card{i + 1}.jpg"
        rel_path, sha = save_temp_image(session_ext_id, filename, crop_bytes)
        results.append((rel_path, filename, sha))
        logger.info("Manual crop %d/%d: %s (%dx%d)", i + 1, len(polygons), filename, out_w, out_h)

    if not results:
        raise ValueError("No valid polygons provided")

    return results
