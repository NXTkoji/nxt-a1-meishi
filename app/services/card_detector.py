"""
Detect and crop individual business cards from a photo containing multiple cards.

Strategy:
  1. Claude Vision → rough bounding boxes (semantic: knows what a card is)
  2. OpenCV contour tracing → precise card edges within each rough region
  3. Perspective warp → flat, straight card image even if photographed at an angle
  4. Graceful fallback → rough crop if contour detection fails

Returns a list of (relative_path, filename, sha256) tuples — one per card found.
If only one card is detected the original image is returned unchanged.
"""
from __future__ import annotations

import base64
import io
import json
import logging
from typing import List, Tuple

import anthropic
import cv2
import numpy as np
from PIL import Image

from app.config import settings
from app.services.image_store import read_temp_image, save_temp_image, _sha256, _resize_bytes

logger = logging.getLogger(__name__)

_DETECT_PROMPT = """\
Your task: locate EVERY individual business card in this photograph and return a tight \
bounding box for each one.

A business card is a small rigid rectangular card (~90×55 mm). It has a uniform printed \
surface — one side may be text-dense (name, title, phone) while the OTHER SIDE of the same \
card may be sparser (just an address, logo, or blank area). BOTH sides belong to the SAME \
physical card rectangle. Cards may be laid out side-by-side, in a grid, or at angles.

Rules:
1. Count EVERY visible card — do NOT stop after the first.
2. Each card gets its OWN exclusive bounding box. Boxes must NOT overlap.
3. The bounding box must cover the ENTIRE physical card edge-to-edge, including any sparse \
   or logo-only regions. Do NOT truncate a card's box just because part of it has less text.
4. Look for the card's physical border/edge (color difference between card surface and \
   background/table). The box should align with those edges, not just the text cluster.
5. If two cards are touching or adjacent, find the dividing line between them and split there.
6. If a card is partially cut off at the photo edge, still include it.

Return ONLY a JSON array — no explanation, no markdown fences, no extra text:
[{"x1": 10, "y1": 20, "x2": 300, "y2": 200}, ...]
x1,y1 = top-left corner, x2,y2 = bottom-right corner, in pixel units.
Single card → single-element array. No cards → empty array []."""


def _tighten_crop(
    img: Image.Image,
    tighten_top: bool = True,
    tighten_bottom: bool = True,
    tighten_left: bool = True,
    tighten_right: bool = True,
    margin: int = 4,
) -> Image.Image:
    """
    Remove uniform background from the specified edges of a crop by scanning
    inward until rows/columns have sufficient pixel variance (i.e. contain content).
    Only tighten the outer edges — never the edge shared with an adjacent card.
    Falls back to the original image if tightening would remove too much.
    """
    arr = np.array(img.convert("L"), dtype=np.float32)  # grayscale
    h, w = arr.shape
    THRESHOLD = 20.0  # minimum std-dev of a row/column to be considered "content"
                      # gray surface JPEG noise is ~3-8; card text/graphics are 20+
    MIN_KEEP = 0.4    # must keep at least 40% of each dimension

    def first_content(matrix: np.ndarray) -> int:
        for i in range(len(matrix)):
            if matrix[i].std() > THRESHOLD:
                return max(0, i - margin)
        return 0

    top    = first_content(arr)        if tighten_top    else 0
    bottom = h - first_content(arr[::-1])  if tighten_bottom else h
    left   = first_content(arr.T)     if tighten_left   else 0
    right  = w - first_content(arr.T[::-1]) if tighten_right  else w

    # Sanity check — don't over-crop
    if (bottom - top) < h * MIN_KEEP or (right - left) < w * MIN_KEEP:
        return img

    return img.crop((left, top, right, bottom))


def _area(b: dict) -> float:
    return max(0.0, float(b["x2"] - b["x1"])) * max(0.0, float(b["y2"] - b["y1"]))


def _intersection_area(a: dict, b: dict) -> float:
    ix1 = max(a["x1"], b["x1"])
    iy1 = max(a["y1"], b["y1"])
    ix2 = min(a["x2"], b["x2"])
    iy2 = min(a["y2"], b["y2"])
    return max(0.0, float(ix2 - ix1)) * max(0.0, float(iy2 - iy1))


def _parse_json_boxes(text: str) -> list[dict] | None:
    """
    Parse Claude's JSON response into a list of box dicts.
    Strips markdown fences, validates structure, filters degenerate boxes.
    Returns None if JSON is unparseable; returns [] if no valid boxes found.
    """
    s = text.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        s = "\n".join(lines[1:])
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    try:
        parsed = json.loads(s.strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list):
        return None
    valid = []
    for b in parsed:
        try:
            x1, y1, x2, y2 = int(b["x1"]), int(b["y1"]), int(b["x2"]), int(b["y2"])
        except (KeyError, TypeError, ValueError):
            logger.warning("Skipping malformed box: %s", b)
            continue
        if x2 <= x1 or y2 <= y1:
            logger.warning("Skipping degenerate box (zero/negative area): %s", b)
            continue
        if (x2 - x1) < 20 or (y2 - y1) < 20:
            logger.warning("Skipping implausibly small box (%dx%d): %s", x2 - x1, y2 - y1, b)
            continue
        valid.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2})
    return valid


def _drop_wrappers(boxes: list[dict]) -> list[dict]:
    """Remove any box that is a large wrapper containing a smaller box (>80% overlap, >1.5× area)."""
    filtered = []
    for i, box in enumerate(boxes):
        keep = True
        for j, other in enumerate(boxes):
            if i == j:
                continue
            inter = _intersection_area(box, other)
            if inter / max(_area(other), 1.0) > 0.8 and _area(box) > _area(other) * 1.5:
                keep = False
                break
        if keep:
            filtered.append(box)
    return filtered if filtered else boxes


def _resolve_box_crops(boxes: list[dict], img_w: int, img_h: int) -> list[dict]:
    """
    Produce N clean non-overlapping crops from N detected bounding boxes.

    Uses each box's actual detected region (with padding), then resolves any
    pairwise overlaps by splitting at the midpoint between the two boxes.
    This handles cards in any 2D arrangement — horizontal, vertical, or mixed.
    """
    if len(boxes) == 1:
        PAD = 8
        b = boxes[0]
        return [{
            "x1": max(0, int(b["x1"]) - PAD),
            "y1": max(0, int(b["y1"]) - PAD),
            "x2": min(img_w, int(b["x2"]) + PAD),
            "y2": min(img_h, int(b["y2"]) + PAD),
        }]

    PAD = 8

    # Start with padded boxes clipped to image bounds
    crops = []
    for b in boxes:
        crops.append({
            "x1": max(0, int(b["x1"]) - PAD),
            "y1": max(0, int(b["y1"]) - PAD),
            "x2": min(img_w, int(b["x2"]) + PAD),
            "y2": min(img_h, int(b["y2"]) + PAD),
        })

    # Resolve pairwise overlaps: for each pair that actually intersects in 2D,
    # determine the dominant overlap axis and split there.
    n = len(crops)
    for i in range(n):
        for j in range(i + 1, n):
            # Check real 2D intersection
            x_overlap = crops[i]["x1"] < crops[j]["x2"] and crops[i]["x2"] > crops[j]["x1"]
            y_overlap = crops[i]["y1"] < crops[j]["y2"] and crops[i]["y2"] > crops[j]["y1"]
            if not (x_overlap and y_overlap):
                continue

            x_int = min(crops[i]["x2"], crops[j]["x2"]) - max(crops[i]["x1"], crops[j]["x1"])
            y_int = min(crops[i]["y2"], crops[j]["y2"]) - max(crops[i]["y1"], crops[j]["y1"])

            if x_int <= y_int:
                # Primarily side-by-side (X axis dominates) — split at X midpoint
                if crops[i]["x1"] < crops[j]["x1"]:
                    mid = (crops[i]["x2"] + crops[j]["x1"]) // 2
                    crops[i]["x2"] = mid
                    crops[j]["x1"] = mid
                else:
                    mid = (crops[j]["x2"] + crops[i]["x1"]) // 2
                    crops[j]["x2"] = mid
                    crops[i]["x1"] = mid
            else:
                # Primarily stacked (Y axis dominates) — split at Y midpoint
                if crops[i]["y1"] < crops[j]["y1"]:
                    mid = (crops[i]["y2"] + crops[j]["y1"]) // 2
                    crops[i]["y2"] = mid
                    crops[j]["y1"] = mid
                else:
                    mid = (crops[j]["y2"] + crops[i]["y1"]) // 2
                    crops[j]["y2"] = mid
                    crops[i]["y1"] = mid

    return crops


def _fill_row_gaps(crops: list[dict]) -> list[dict]:
    """
    Extend boxes that are the sole occupant of their Y-strip to fill horizontal gaps.

    Problem: Claude sometimes detects only the text-dense half of a wide card,
    leaving the sparse right portion uncovered.  When a box has no peers in the
    same row (i.e. no other box whose Y-center falls within the box's Y-range),
    we extend it left/right to match the global X extent of all cards.
    """
    if len(crops) <= 1:
        return crops

    global_x1 = min(c["x1"] for c in crops)
    global_x2 = max(c["x2"] for c in crops)

    for i, crop in enumerate(crops):
        h = max(1, crop["y2"] - crop["y1"])
        y_center_i = (crop["y1"] + crop["y2"]) / 2

        same_row = [
            j for j, other in enumerate(crops) if j != i
            and abs((other["y1"] + other["y2"]) / 2 - y_center_i)
            < max(h, other["y2"] - other["y1"]) * 0.6
        ]

        if not same_row:
            # Alone in its row — extend to close horizontal gaps.
            # Store original x so the squeeze step can use real card content
            # bounds (not the extended table region) when computing x overlap.
            crop.setdefault("orig_x1", crop["x1"])
            crop.setdefault("orig_x2", crop["x2"])
            if global_x2 - crop["x2"] > h * 0.15:
                crop["x2"] = global_x2
            if crop["x1"] - global_x1 > h * 0.15:
                crop["x1"] = global_x1

    return crops


def _sort_quad_points(pts: np.ndarray) -> np.ndarray:
    """
    Sort 4 points into [top-left, top-right, bottom-right, bottom-left] order.

    Works correctly for rotated cards by sorting points by angle from centroid,
    starting from top-left and moving clockwise.
    """
    # Compute centroid
    cx = np.mean(pts[:, 0])
    cy = np.mean(pts[:, 1])

    # Compute angle from centroid to each point
    # atan2 returns angle in [-π, π]; we adjust to start from top-left (-135°)
    angles = np.arctan2(pts[:, 1] - cy, pts[:, 0] - cx)

    # Rotate angles so 0° starts at top-left (-135° in standard coords)
    # and we go clockwise (TL → TR → BR → BL)
    angles = angles - np.pi * 0.75  # shift so -135° becomes 0°
    angles = np.where(angles < 0, angles + 2 * np.pi, angles)

    # Sort points by angle
    sorted_indices = np.argsort(angles)
    return pts[sorted_indices].astype(np.float32)


# ── Card background brightness ────────────────────────────────────────────────

def _card_bg_brightness(gray: np.ndarray, box: dict) -> float:
    """
    Estimate the card's surface brightness from the non-content (low-Laplacian)
    pixels in the inner 50 % of the rough box.

    Cards have a relatively uniform printed surface; text/logos create sharp
    Laplacian responses.  The remaining low-texture pixels represent the card's
    background colour, which is used as a reference for edge expansion.
    """
    x1, y1, x2, y2 = box["x1"], box["y1"], box["x2"], box["y2"]
    cx1 = x1 + (x2 - x1) // 4
    cx2 = x2 - (x2 - x1) // 4
    cy1 = y1 + (y2 - y1) // 4
    cy2 = y2 - (y2 - y1) // 4
    if cx2 <= cx1 or cy2 <= cy1:
        return float(gray[y1:y2, x1:x2].mean())
    roi = gray[cy1:cy2, cx1:cx2]
    lap = np.abs(cv2.Laplacian(roi.astype(np.float32), cv2.CV_32F))
    mask = lap < 15  # low-texture = background
    pixels = roi[mask] if mask.sum() >= 20 else roi.ravel()
    # Use 75th percentile so dark gap/table regions that happen to fall inside
    # the interior box don't drag the estimate down.
    return float(np.percentile(pixels, 75))


def _refine_by_card_color(
    crops: list[dict],
    img_rgb: np.ndarray,
    img_w: int,
    img_h: int,
) -> list[dict]:
    """
    Refine card crop boundaries using each card's background (surface) colour.

    Replaces ``_fill_row_gaps``, ``_squeeze_adjacent_pairs``, and the
    ``orig_x`` restore step.

    Algorithm
    ---------
    1. Compute each card's surface brightness from non-content interior pixels.
    2. Group cards into same-row sets (y-centres mutually contained).
    3. **Vertical boundaries** (between rows): scan the gap zone with a
       brightness-step detector to find the table strip between card rows.
       Lock the top/bottom edges of all affected cards.
    4. **Horizontal boundaries** (between same-row neighbours): centre-to-centre
       brightness-step scan to find the inter-card gap.  Lock the left/right
       edges of the adjacent pair.
    5. **Outer edge expansion**: for every unlocked edge, scan outward
       column-by-column (or row-by-row) until the mean brightness deviates
       from the card surface colour by more than ``OUTER_THRESH``.  This
       recovers the true card extent even when Claude's box underestimates it.
    """
    gray = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)
    n = len(crops)
    bg = [_card_bg_brightness(gray, c) for c in crops]
    logger.info("Card bg brightness: %s", [f"{b:.0f}" for b in bg])

    # ── Step 2: same-row groups ───────────────────────────────────────────────
    assigned = [-1] * n
    next_row = 0
    for i in range(n):
        if assigned[i] >= 0:
            continue
        assigned[i] = next_row
        ci_c = (crops[i]["y1"] + crops[i]["y2"]) / 2
        for j in range(i + 1, n):
            if assigned[j] >= 0:
                continue
            cj_c = (crops[j]["y1"] + crops[j]["y2"]) / 2
            if (crops[i]["y1"] <= cj_c <= crops[i]["y2"] and
                    crops[j]["y1"] <= ci_c <= crops[j]["y2"]):
                assigned[j] = next_row
        next_row += 1

    rows: dict[int, list[int]] = {}
    for i, r in enumerate(assigned):
        rows.setdefault(r, []).append(i)
    for r in rows:
        rows[r].sort(key=lambda k: crops[k]["x1"])
    logger.info("Same-row groups: %s", rows)

    # locked[i] accumulates edge overrides set by shared-boundary detection
    locked: dict[int, dict[str, int]] = {i: {} for i in range(n)}

    # ── Step 3: vertical boundaries ──────────────────────────────────────────
    row_ids = sorted(rows, key=lambda r: min(crops[i]["y1"] for i in rows[r]))
    for ri, top_rid in enumerate(row_ids[:-1]):
        bot_rid = row_ids[ri + 1]
        top_row, bot_row = rows[top_rid], rows[bot_rid]

        top_x1 = min(crops[i]["x1"] for i in top_row)
        top_x2 = max(crops[i]["x2"] for i in top_row)

        # Find vertical boundary for each bottom-row card that overlaps in X
        y1_candidates: list[int] = []
        for bot_idx in bot_row:
            bc = crops[bot_idx]
            x_lo = max(top_x1, bc["x1"])
            x_hi = min(top_x2, bc["x2"])
            if x_hi - x_lo < 40:
                continue

            top_y2_est = min(crops[i]["y2"] for i in top_row)
            scan_y1 = max(0, top_y2_est - 100)
            scan_y2 = min(img_h, bc["y1"] + 100)
            if scan_y2 <= scan_y1 + 4:
                continue

            strip = gray[scan_y1:scan_y2, x_lo:x_hi]
            rp = strip.mean(axis=1).astype(np.float32)
            n_r = len(rp)
            STEP_W = max(8, n_r // 8)
            best_step, best_cut = -1.0, n_r // 2
            for cut in range(n_r // 10, 9 * n_r // 10):
                step = abs(rp[max(0, cut - STEP_W):cut].mean() -
                           rp[cut:min(n_r, cut + STEP_W)].mean())
                if step > best_step:
                    best_step, best_cut = step, cut
            boundary_y = scan_y1 + best_cut
            y1_candidates.append(boundary_y + 1)
            logger.info(
                "Vertical boundary (rows %d→%d, bot card %d): y=%d (step=%.1f)",
                top_rid, bot_rid, bot_idx + 1, boundary_y, best_step,
            )

        if not y1_candidates:
            continue

        shared_y1 = max(y1_candidates)  # tightest (lowest table strip)
        shared_y2 = shared_y1 - 1

        for top_idx in top_row:
            locked[top_idx]["y2"] = min(locked[top_idx].get("y2", img_h), shared_y2)
        for bot_idx in bot_row:
            locked[bot_idx]["y1"] = max(locked[bot_idx].get("y1", 0), shared_y1)

    # ── Step 4: horizontal boundaries ─────────────────────────────────────────
    for row in rows.values():
        if len(row) < 2:
            continue
        for k in range(len(row) - 1):
            li, ri = row[k], row[k + 1]
            lc, rc = crops[li], crops[ri]

            y_lo = locked[li].get("y1", max(lc["y1"], rc["y1"]))
            y_hi = locked[li].get("y2", min(lc["y2"], rc["y2"]))
            if y_hi <= y_lo + 4:
                y_lo = min(lc["y1"], rc["y1"])
                y_hi = max(lc["y2"], rc["y2"])

            scan_x1 = (lc["x1"] + lc["x2"]) // 2
            scan_x2 = (rc["x1"] + rc["x2"]) // 2
            if scan_x2 <= scan_x1 + 4:
                continue

            strip = gray[y_lo:y_hi, scan_x1:scan_x2]
            cp = strip.mean(axis=0).astype(np.float32)
            n_c = len(cp)
            STEP_W = max(10, n_c // 10)
            best_step, best_cut = -1.0, n_c // 2
            for cut in range(n_c // 10, 9 * n_c // 10):
                step = abs(cp[max(0, cut - STEP_W):cut].mean() -
                           cp[cut:min(n_c, cut + STEP_W)].mean())
                if step > best_step:
                    best_step, best_cut = step, cut
            MIN_H_STEP = 15.0
            if best_step >= MIN_H_STEP:
                boundary_x = scan_x1 + best_cut
            else:
                # Weak signal — fall back to midpoint between Claude boxes to
                # prevent outer expansion from crossing into the adjacent card.
                boundary_x = (lc["x2"] + rc["x1"]) // 2
            locked[li]["x2"] = boundary_x
            locked[ri]["x1"] = boundary_x + 1
            logger.info(
                "Horizontal boundary cards %d/%d: x=%d (step=%.1f%s)",
                li + 1, ri + 1, boundary_x, best_step,
                "" if best_step >= MIN_H_STEP else " → fallback midpoint",
            )

    # ── Step 5: outer edge expansion ──────────────────────────────────────────
    OUTER_THRESH = 30.0   # brightness deviation that signals table or other card
    MAX_EXPAND   = 400    # maximum pixels to search outward on any free edge

    result = []
    for i, box in enumerate(crops):
        lk = locked[i]
        card_bg = bg[i]

        x1 = lk.get("x1", box["x1"])
        x2 = lk.get("x2", box["x2"])
        y1 = lk.get("y1", box["y1"])
        y2 = lk.get("y2", box["y2"])

        # Use 75th-percentile brightness per strip so dark card content
        # (text, logos) doesn't prematurely stop the expansion.
        def _col_bright(x: int) -> float:
            return float(np.percentile(gray[y1:y2, x], 75)) if y2 > y1 else card_bg

        def _row_bright(y: int) -> float:
            return float(np.percentile(gray[y, x1:x2], 75)) if x2 > x1 else card_bg

        # Right (free edge only)
        if "x2" not in lk:
            for x in range(x2 + 1, min(img_w, x2 + MAX_EXPAND)):
                if abs(_col_bright(x) - card_bg) > OUTER_THRESH:
                    break
                x2 = x

        # Left (free edge only)
        if "x1" not in lk:
            for x in range(x1 - 1, max(-1, x1 - MAX_EXPAND), -1):
                if abs(_col_bright(x) - card_bg) > OUTER_THRESH:
                    break
                x1 = x

        # Bottom (free edge only)
        if "y2" not in lk:
            for y in range(y2 + 1, min(img_h, y2 + MAX_EXPAND)):
                if abs(_row_bright(y) - card_bg) > OUTER_THRESH:
                    break
                y2 = y

        # Top (free edge only)
        if "y1" not in lk:
            for y in range(y1 - 1, max(-1, y1 - MAX_EXPAND), -1):
                if abs(_row_bright(y) - card_bg) > OUTER_THRESH:
                    break
                y1 = y

        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(img_w, x2), min(img_h, y2)

        result.append({**box, "x1": x1, "y1": y1, "x2": x2, "y2": y2})
        logger.info(
            "Card %d refined: x=%d..%d y=%d..%d (%dx%d, bg=%.0f)",
            i + 1, x1, x2, y1, y2, x2 - x1, y2 - y1, card_bg,
        )

    return result


def _squeeze_adjacent_pairs(crops: list[dict], img_rgb: np.ndarray) -> list[dict]:
    """
    For card pairs that are adjacent (gap < 60px) in X or Y, scan the narrow
    interface zone for the minimum-density row/column and use that as the
    actual card boundary, eliminating content bleed from neighboring cards.
    """
    n = len(crops)
    for i in range(n):
        for j in range(i + 1, n):
            ci, cj = crops[i], crops[j]

            # ── Vertical adjacency ────────────────────────────────────────────
            # Cards are SAME-LEVEL (horizontal neighbours) when each card's
            # y-centre falls inside the other's y-range.  Only classify as
            # vertical neighbours when they are clearly stacked (y-centres
            # differ by > 20 px AND one centre is outside the other's range).
            # Using only the raw y-centre delta caused same-row cards to be
            # misclassified as vertical when their raw y1 values differed
            # (before Y-propagation updates them).
            ci_cy = (ci["y1"] + ci["y2"]) / 2
            cj_cy = (cj["y1"] + cj["y2"]) / 2
            same_level = (
                ci["y1"] <= cj_cy <= ci["y2"] and
                cj["y1"] <= ci_cy <= cj["y2"]
            )
            if same_level:
                top, bot = None, None
            elif ci_cy < cj_cy - 20:
                top, bot = i, j
            elif cj_cy < ci_cy - 20:
                top, bot = j, i
            else:
                top, bot = None, None

            if top is not None:
                gap = crops[bot]["y1"] - crops[top]["y2"]
                top_h = max(1, crops[top]["y2"] - crops[top]["y1"])
                # Allow rough-box Y-overlap up to 50 % of the top card's height.
                # Claude often gives boxes that slightly overlap at the boundary
                # between a top-row card and a bottom-row card; the true gap is
                # still findable by scanning between the two card centres.
                if gap <= 60 and gap >= -top_h * 0.5:
                    # Use ORIGINAL (pre-fill_row_gaps) x-bounds for the overlap
                    # check.  A lone top-row card is extended to global_x2 by
                    # _fill_row_gaps, which would falsely make it "overlap" in X
                    # with every bottom-row card — including ones that are to the
                    # right of the physical card and should not be vertically
                    # squeezed against it.  orig_x reflects the card's actual
                    # content extent; if the two cards don't truly overlap in X
                    # they are not vertically adjacent and the squeeze must be
                    # skipped.
                    top_x1 = crops[top].get("orig_x1", crops[top]["x1"])
                    top_x2 = crops[top].get("orig_x2", crops[top]["x2"])
                    bot_x1 = crops[bot].get("orig_x1", crops[bot]["x1"])
                    bot_x2 = crops[bot].get("orig_x2", crops[bot]["x2"])
                    x_lo = max(top_x1, bot_x1)
                    x_hi = min(top_x2, bot_x2)
                    if x_hi <= x_lo:
                        continue
                    # Require meaningful X overlap: ≥ 20 % of the narrower card.
                    # A tiny overlap (e.g. 12 px from Claude's 8-px padding) can
                    # arise between a top card and a bottom-right card that are not
                    # truly vertically adjacent — filter those out.
                    top_w = top_x2 - top_x1
                    bot_w = bot_x2 - bot_x1
                    if (x_hi - x_lo) < max(40, min(top_w, bot_w) * 0.20):
                        continue

                    # Focused scan around the expected boundary zone.
                    # We scan from (top card's bottom edge − margin) to
                    # (bottom card's top edge + margin).  Using card centres
                    # caused the detector to find brightness steps INSIDE a
                    # card (e.g. a dark header band) rather than the real
                    # inter-card gap.  The margin is large enough to handle
                    # imprecise Claude boxes while keeping the search window
                    # away from strong in-card features.
                    total_span = crops[bot]["y2"] - crops[top]["y1"]
                    BOUNDARY_MARGIN = max(80, total_span // 6)
                    scan_y1 = max(crops[top]["y1"], crops[top]["y2"] - BOUNDARY_MARGIN)
                    scan_y2 = min(crops[bot]["y2"], crops[bot]["y1"] + BOUNDARY_MARGIN)
                    if scan_y2 <= scan_y1 + 4:
                        continue  # degenerate; skip
                    zone = img_rgb[scan_y1:scan_y2, x_lo:x_hi]
                    if zone.size == 0:
                        continue

                    gray_zone = cv2.cvtColor(zone, cv2.COLOR_RGB2GRAY)
                    row_brightness = gray_zone.mean(axis=1).astype(np.float32)
                    n_rows = len(row_brightness)

                    # ── Method A: brightness-step detector ────────────────────
                    # Finds the cut point where the lower half is brightest
                    # relative to the upper half (table→card-below transition).
                    # Works well when the two cards have different background
                    # colours (e.g. grey card above, white card below).
                    STEP_W = max(10, n_rows // 10)  # smoothing window
                    best_step = -1.0
                    best_cut = n_rows // 2
                    for cut in range(n_rows // 4, 3 * n_rows // 4):
                        lo = row_brightness[max(0, cut - STEP_W):cut].mean()
                        hi = row_brightness[cut:min(n_rows, cut + STEP_W)].mean()
                        step = abs(hi - lo)
                        if step > best_step:
                            best_step = step
                            best_cut = cut
                    boundary_a = scan_y1 + best_cut

                    # ── Method B: minimum Laplacian in middle 50 % of zone ────
                    # Works well when cards have similar colours but one region
                    # has less texture/text than the surrounding areas.
                    lap = np.abs(cv2.Laplacian(
                        gray_zone.astype(np.float32), cv2.CV_32F))
                    row_density = lap.mean(axis=1)
                    mid_lo = n_rows // 4
                    mid_hi = 3 * n_rows // 4
                    min_rel = int(np.argmin(row_density[mid_lo:mid_hi]))
                    boundary_b = scan_y1 + mid_lo + min_rel

                    # Pick whichever method gives the stronger signal.
                    # A strong brightness step (≥12 units) is more reliable than
                    # a Laplacian minimum when the cards have different colours.
                    boundary_y = boundary_a if best_step >= 12 else boundary_b

                    old_top_y2 = crops[top]["y2"]
                    old_bot_y1 = crops[bot]["y1"]
                    crops[top]["y2"] = boundary_y
                    crops[top]["_y2_squeezed"] = True
                    crops[bot]["y1"] = boundary_y + 1
                    crops[bot]["_y1_squeezed"] = True
                    logger.info(
                        "Squeezed Y boundary between cards %d/%d: "
                        "top y2 %d→%d, bot y1 %d→%d "
                        "(step=%.1f, method=%s)",
                        top + 1, bot + 1,
                        old_top_y2, crops[top]["y2"],
                        old_bot_y1, crops[bot]["y1"],
                        best_step, "brightness" if best_step >= 12 else "laplacian",
                    )
                continue  # skip horizontal check for the same pair

            # ── Horizontal adjacency ──────────────────────────────────────────
            left, right = (i, j) if ci["x2"] <= cj["x1"] else (j, i) if cj["x2"] <= ci["x1"] else (None, None)
            if left is not None:
                gap = crops[right]["x1"] - crops[left]["x2"]
                if 0 <= gap <= 60:
                    y_lo = max(crops[left]["y1"], crops[right]["y1"])
                    y_hi = min(crops[left]["y2"], crops[right]["y2"])
                    if y_hi <= y_lo:
                        continue

                    # Center-to-center scan for horizontal boundaries.
                    # Unlike vertical squeeze (where dark header bands inside a
                    # card can dominate), the inter-card gap in the horizontal
                    # direction is typically the strongest brightness transition
                    # in the row.  Scanning from card-centre to card-centre gives
                    # the best chance of capturing the real card edge even when
                    # Claude's box slightly under- or over-estimates card width.
                    scan_x1 = (crops[left]["x1"] + crops[left]["x2"]) // 2
                    scan_x2 = (crops[right]["x1"] + crops[right]["x2"]) // 2
                    if scan_x2 <= scan_x1 + 4:
                        continue

                    zone = img_rgb[y_lo:y_hi, scan_x1:scan_x2]
                    if zone.size == 0:
                        continue

                    gray = cv2.cvtColor(zone, cv2.COLOR_RGB2GRAY)
                    col_brightness = gray.mean(axis=0).astype(np.float32)
                    n_cols = len(col_brightness)

                    # Method A: brightness-step detector (column direction).
                    # Search the central 80 % (10%–90%) of the scan zone so we
                    # can capture boundaries that sit near the edges of the zone
                    # (e.g. when Claude's box underestimates one card's width).
                    STEP_W = max(10, n_cols // 10)
                    best_step = -1.0
                    best_cut = n_cols // 2
                    for cut in range(n_cols // 10, 9 * n_cols // 10):
                        lo = col_brightness[max(0, cut - STEP_W):cut].mean()
                        hi = col_brightness[cut:min(n_cols, cut + STEP_W)].mean()
                        step = abs(hi - lo)
                        if step > best_step:
                            best_step = step
                            best_cut = cut
                    boundary_x_a = scan_x1 + best_cut

                    # Method B: minimum Laplacian in central 80 % of zone
                    lap = np.abs(cv2.Laplacian(
                        gray.astype(np.float32), cv2.CV_32F))
                    col_density = lap.mean(axis=0)
                    mid_lo = n_cols // 10
                    mid_hi = 9 * n_cols // 10
                    min_rel = int(np.argmin(col_density[mid_lo:mid_hi]))
                    boundary_x_b = scan_x1 + mid_lo + min_rel

                    boundary_x = boundary_x_a if best_step >= 12 else boundary_x_b

                    old_left_x2 = crops[left]["x2"]
                    old_right_x1 = crops[right]["x1"]
                    crops[left]["x2"] = boundary_x
                    crops[right]["x1"] = boundary_x + 1
                    logger.info(
                        "Squeezed X boundary between cards %d/%d: "
                        "left x2 %d→%d, right x1 %d→%d "
                        "(step=%.1f, method=%s)",
                        left + 1, right + 1,
                        old_left_x2, crops[left]["x2"],
                        old_right_x1, crops[right]["x1"],
                        best_step, "brightness" if best_step >= 12 else "laplacian",
                    )

    # ── Same-level Y-boundary propagation ────────────────────────────────────
    # Cards in the same horizontal row should share consistent y1/y2 bounds
    # ONLY when those bounds were explicitly set by a vertical squeeze.
    # Do NOT use the intersection of Claude's raw boxes — Claude can give
    # slightly different y extents to same-row cards, and intersecting them
    # incorrectly shrinks all cards in a purely horizontal layout.
    changed = True
    while changed:
        changed = False
        for i in range(len(crops)):
            for j in range(i + 1, len(crops)):
                ci, cj = crops[i], crops[j]
                ci_c = (ci["y1"] + ci["y2"]) / 2
                cj_c = (cj["y1"] + cj["y2"]) / 2
                # Same level: each card's centre falls within the other's y range
                if not (ci["y1"] <= cj_c <= ci["y2"] and
                        cj["y1"] <= ci_c <= cj["y2"]):
                    continue
                # Propagate squeezed bounds from src → dst only
                for src, dst in [(i, j), (j, i)]:
                    if (crops[src].get("_y1_squeezed") and
                            crops[dst]["y1"] != crops[src]["y1"]):
                        crops[dst]["y1"] = crops[src]["y1"]
                        crops[dst]["_y1_squeezed"] = True
                        changed = True
                    if (crops[src].get("_y2_squeezed") and
                            crops[dst]["y2"] != crops[src]["y2"]):
                        crops[dst]["y2"] = crops[src]["y2"]
                        crops[dst]["_y2_squeezed"] = True
                        changed = True

    return crops


def _refine_with_contours(
    img_rgb: np.ndarray,
    rough: dict,
    img_w: int,
    img_h: int,
) -> tuple[dict, np.ndarray | None]:
    """
    Refine a rough bounding box by finding the card's actual content extent.

    Strategy:
    1. Column/row minimum brightness → "dark pixel" bounding box.
       Card text/logos have min brightness < DARK_THRESH; plain table/background
       surfaces have no truly dark pixels (min ≥ 88 for wood grain in practice).
       This reliably identifies the card's content columns/rows and trims excess
       background from any edge.
    2. Perspective warp path preserved: if Laplacian+contour detects a quad at
       angle > 3°, warp to flat before returning (keeps angled-card support).
    3. Always constrain to rough box — only tighten, never expand.

    Falls back to (rough, None) when detection is not confident.
    """
    x1, y1, x2, y2 = rough["x1"], rough["y1"], rough["x2"], rough["y2"]
    card_w = max(1, x2 - x1)
    card_h = max(1, y2 - y1)
    card_area_est = card_w * card_h

    # Work within the rough box (no extra margin — we only tighten)
    roi = img_rgb[y1:y2, x1:x2]
    if roi.size == 0:
        return rough, None

    gray = cv2.cvtColor(roi, cv2.COLOR_RGB2GRAY)

    # ── Perspective warp path (angle > 3°) ───────────────────────────────────
    # Use Laplacian + contour to detect a rotated quad.  If the card is
    # significantly tilted we warp it flat and return early.
    lap = cv2.Laplacian(gray.astype(np.float32), cv2.CV_32F)
    lap_norm = cv2.normalize(np.abs(lap), None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    _, text_mask = cv2.threshold(lap_norm, 15, 255, cv2.THRESH_BINARY)
    dil_r = max(20, min(card_w, card_h) // 3)
    k_dil = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dil_r * 2 + 1, dil_r * 2 + 1))
    dilated = cv2.dilate(text_mask, k_dil)

    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(dilated, connectivity=8)
    if n_labels >= 2:
        largest = max(range(1, n_labels), key=lambda lbl: stats[lbl, cv2.CC_STAT_AREA])
        comp_mask = (labels == largest).astype(np.uint8) * 255
        contours, _ = cv2.findContours(comp_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if contours:
            cnt = max(contours, key=cv2.contourArea)
            peri = cv2.arcLength(cnt, True)
            approx = cv2.approxPolyDP(cnt, 0.05 * peri, True)
            if len(approx) == 4:
                pts = approx.reshape(4, 2).astype(np.float32)
                pts_sorted = _sort_quad_points(pts)
                w_out = int(max(
                    np.linalg.norm(pts_sorted[1] - pts_sorted[0]),
                    np.linalg.norm(pts_sorted[2] - pts_sorted[3]),
                ))
                h_out = int(max(
                    np.linalg.norm(pts_sorted[3] - pts_sorted[0]),
                    np.linalg.norm(pts_sorted[2] - pts_sorted[1]),
                ))
                if w_out >= 40 and h_out >= 40:
                    rect = cv2.minAreaRect(cnt)
                    angle = abs(rect[2])
                    angle = min(angle, 90 - angle)
                    if angle > 3:
                        dst = np.array(
                            [[0, 0], [w_out - 1, 0], [w_out - 1, h_out - 1], [0, h_out - 1]],
                            dtype=np.float32,
                        )
                        M = cv2.getPerspectiveTransform(pts_sorted, dst)
                        warped = cv2.warpPerspective(roi, M, (w_out, h_out))
                        logger.info("Perspective-corrected card (angle=%.1f°): %dx%d", angle, w_out, h_out)
                        return rough, warped

    # No further trimming — the squeeze step already set correct boundaries.
    # Laplacian-based trimming was removed because it cuts blank card margins
    # (areas with no text but which are physically part of the card surface).
    return rough, None


def detect_corners_from_seed(
    temp_relative_path: str,
    seed_x: float,
    seed_y: float,
) -> tuple[list[dict], float]:
    """
    Find the 4 corners of the business card nearest the seed point.

    Uses OpenCV Canny edge detection + contour quadrilateral approximation.
    If no quad-shaped contour contains the seed, returns a default rectangle
    centered on the seed with confidence 0.0.

    Args:
        temp_relative_path: Relative path under TEMP_DIR (passed to read_temp_image).
        seed_x: Normalized [0, 1] x coordinate of the tap point.
        seed_y: Normalized [0, 1] y coordinate of the tap point.

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
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    # Adaptive Canny thresholds based on image brightness
    # Dark images need lower thresholds; bright images need higher thresholds
    gray_mean = float(blurred.mean())
    if gray_mean < 80:      # Dark image
        low, high = 20, 60
    elif gray_mean < 150:   # Medium image
        low, high = 30, 100
    else:                   # Bright image
        low, high = 50, 150

    edges = cv2.Canny(blurred, low, high)

    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    # Cards must be at least 1% of image area to filter out noise
    MIN_AREA = img_w * img_h * 0.01

    best_quad: np.ndarray | None = None
    best_area = 0.0

    for contour in contours:
        area = cv2.contourArea(contour)
        if area < MIN_AREA:
            continue
        # Only consider contours that actually contain the seed point
        if cv2.pointPolygonTest(contour, (float(seed_px), float(seed_py)), False) < 0:
            continue
        # Try successively looser epsilon until we get a 4-point approximation
        arc = cv2.arcLength(contour, True)
        for eps_factor in (0.02, 0.04, 0.06, 0.08):
            approx = cv2.approxPolyDP(contour, eps_factor * arc, True)
            if len(approx) == 4:
                if area > best_area:
                    best_area = area
                    best_quad = approx.reshape(4, 2).astype(np.float32)
                break

    if best_quad is not None:
        sorted_pts = _sort_quad_points(best_quad)
        corners = [
            {"x": float(p[0] / img_w), "y": float(p[1] / img_h)}
            for p in sorted_pts
        ]
        # Scale confidence: 20% of image area → 1.0
        confidence = float(min(1.0, best_area / (img_w * img_h) * 5))
        logger.info(
            "detect_corners_from_seed: seed=(%.3f,%.3f) → quad area=%.0f confidence=%.2f",
            seed_x, seed_y, best_area, confidence,
        )
        return corners, confidence

    # Fallback: axis-aligned rectangle centered on the seed,
    # assuming standard business card aspect ratio ~1.75:1.
    card_w = 0.35
    card_h = card_w / 1.75
    x1 = max(0.0, seed_x - card_w / 2)
    y1 = max(0.0, seed_y - card_h / 2)
    x2 = min(1.0, seed_x + card_w / 2)
    y2 = min(1.0, seed_y + card_h / 2)
    fallback_corners = [
        {"x": x1, "y": y1},
        {"x": x2, "y": y1},
        {"x": x2, "y": y2},
        {"x": x1, "y": y2},
    ]
    logger.info(
        "detect_corners_from_seed: no quad found for seed=(%.3f,%.3f) → fallback rectangle",
        seed_x, seed_y,
    )
    return fallback_corners, 0.0


async def detect_and_split(
    session_ext_id: str,
    temp_relative_path: str,
    original_filename: str,
) -> List[Tuple[str, str, str]]:
    """
    Detect individual business cards in an image and crop each one.

    Returns:
        List of (relative_path, filename, sha256) — one entry per detected card.
        If only 1 card is found, returns [(original_path, original_filename, sha)].
        If 0 cards found, raises ValueError.
    """
    raw = read_temp_image(temp_relative_path)

    # Resize to Claude's processing dimension so the returned coordinates
    # match what PIL will crop from (avoids coordinate-scaling mismatch on
    # high-res iPhone photos where Claude internally resizes the image).
    working = _resize_bytes(raw)

    # Detect bounding boxes via Claude Vision (two-turn: count first, then locate)
    b64 = base64.b64encode(working).decode()
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key or None)

    image_block = {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
    }

    # Turn 1: ask Claude to count and briefly describe the cards so it anchors on
    # the full physical layout before committing to pixel coordinates.
    count_resp = await client.messages.create(
        model=settings.claude_model,
        max_tokens=256,
        messages=[{
            "role": "user",
            "content": [
                image_block,
                {"type": "text", "text": (
                    "How many distinct business cards are visible in this photo? "
                    "For each card, briefly describe its position (e.g. 'top center', 'bottom left') "
                    "and name/company if visible. Reply in 1-3 lines."
                )},
            ],
        }],
    )
    card_description = count_resp.content[0].text.strip()
    logger.info("Card count/description: %s", card_description)

    # Turn 2: use the description as context to get precise bounding boxes
    locate_prompt = (
        f"You described the cards as:\n{card_description}\n\n"
        + _DETECT_PROMPT
    )
    resp = await client.messages.create(
        model=settings.claude_model,
        max_tokens=512,
        messages=[
            {
                "role": "user",
                "content": [
                    image_block,
                    {"type": "text", "text": (
                        "How many distinct business cards are visible in this photo? "
                        "For each card, briefly describe its position (e.g. 'top center', 'bottom left') "
                        "and name/company if visible. Reply in 1-3 lines."
                    )},
                ],
            },
            {"role": "assistant", "content": card_description},
            {"role": "user", "content": locate_prompt},
        ],
    )

    text = resp.content[0].text.strip()
    boxes = _parse_json_boxes(text)
    if boxes is None or not boxes:
        # Retry once with a minimal prompt to recover from formatting errors
        logger.warning(
            "Box detection failed or returned no valid boxes (response=%r); retrying...", text
        )
        retry_resp = await client.messages.create(
            model=settings.claude_model,
            max_tokens=512,
            messages=[{
                "role": "user",
                "content": [
                    image_block,
                    {"type": "text", "text": (
                        "Return ONLY a JSON array of bounding boxes for every business card "
                        "visible in this image. Format: "
                        '[{"x1":10,"y1":20,"x2":300,"y2":200},...]. '
                        "No explanation, no markdown."
                    )},
                ],
            }],
        )
        boxes = _parse_json_boxes(retry_resp.content[0].text.strip())
        if boxes is None:
            raise ValueError(
                f"Card detection returned invalid JSON after retry: "
                f"{retry_resp.content[0].text!r}"
            )
        if not boxes:
            raise ValueError("No business cards detected in the image")

    logger.info("Claude detected %d box(es): %s", len(boxes), boxes)

    if len(boxes) == 1:
        return [(temp_relative_path, original_filename, _sha256(raw))]

    # Drop large wrapper boxes that subsume smaller ones
    boxes = _drop_wrappers(boxes)
    logger.info("%d box(es) after dropping wrappers", len(boxes))

    if len(boxes) == 1:
        return [(temp_relative_path, original_filename, _sha256(raw))]

    # Use detected boxes directly, resolving overlaps at midpoints
    img = Image.open(io.BytesIO(working)).convert("RGB")
    img_w, img_h = img.size
    img_rgb = np.array(img)          # OpenCV works on numpy arrays

    crops = _resolve_box_crops(boxes, img_w, img_h)
    crops = _refine_by_card_color(crops, img_rgb, img_w, img_h)
    logger.info("Final crops: %s", crops)

    stem = original_filename.rsplit(".", 1)[0]
    results: List[Tuple[str, str, str]] = []

    for i, box in enumerate(crops):
        if box["x2"] <= box["x1"] or box["y2"] <= box["y1"]:
            logger.warning("Skipping degenerate crop %s", box)
            continue

        # Refine the rough box with contour tracing
        refined_box, warped_rgb = _refine_with_contours(img_rgb, box, img_w, img_h)

        if warped_rgb is not None:
            # warped_rgb is an RGB numpy array (same color space as img_rgb)
            card_img = Image.fromarray(warped_rgb)
        else:
            x1, y1, x2, y2 = refined_box["x1"], refined_box["y1"], refined_box["x2"], refined_box["y2"]
            card_img = img.crop((x1, y1, x2, y2))

        buf = io.BytesIO()
        card_img.save(buf, format="JPEG", quality=95)
        crop_bytes = buf.getvalue()

        filename = f"{stem}_card{i + 1}.jpg"
        rel_path, sha = save_temp_image(session_ext_id, filename, crop_bytes)
        results.append((rel_path, filename, sha))
        w, h = card_img.size
        logger.info("Saved card %d/%d: %s (%dx%d)", i + 1, len(crops), filename, w, h)

    if not results:
        raise ValueError("All detected crops were invalid")

    return results


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
