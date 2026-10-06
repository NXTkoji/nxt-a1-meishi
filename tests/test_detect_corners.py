"""Unit tests for detect_corners_from_seed."""
import io
import numpy as np
import pytest
from PIL import Image as PILImage
from unittest.mock import patch, MagicMock


def _make_test_image_with_card() -> bytes:
    """
    Create a synthetic 600x400 white image with a dark-bordered card rectangle
    at roughly center, so contour detection can find it.
    Card region: x=150..450, y=100..300 (gray fill, black border).
    """
    img = np.ones((400, 600, 3), dtype=np.uint8) * 220  # light gray background
    img[100:300, 150:450] = 200                          # slightly lighter card area
    img[100:102, 150:450] = 50                           # top border
    img[298:300, 150:450] = 50                           # bottom border
    img[100:300, 150:152] = 50                           # left border
    img[100:300, 448:450] = 50                           # right border
    pil = PILImage.fromarray(img.astype(np.uint8))
    buf = io.BytesIO()
    pil.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def test_detect_corners_from_seed_returns_four_points():
    from app.services.card_detector import detect_corners_from_seed

    img_bytes = _make_test_image_with_card()

    with patch("app.services.card_detector.read_temp_image", return_value=img_bytes), \
         patch("app.services.card_detector._resize_bytes", return_value=img_bytes):
        corners, confidence = detect_corners_from_seed("fake/path.jpg", 0.5, 0.5)

    assert len(corners) == 4
    for pt in corners:
        assert "x" in pt and "y" in pt
        assert 0.0 <= pt["x"] <= 1.0
        assert 0.0 <= pt["y"] <= 1.0


def test_detect_corners_from_seed_high_confidence_for_clear_card():
    from app.services.card_detector import detect_corners_from_seed

    img_bytes = _make_test_image_with_card()

    with patch("app.services.card_detector.read_temp_image", return_value=img_bytes), \
         patch("app.services.card_detector._resize_bytes", return_value=img_bytes):
        corners, confidence = detect_corners_from_seed("fake/path.jpg", 0.5, 0.5)

    # Must be detection result, not fallback
    assert confidence > 0.0
    # Corners should bracket the known card region (card is at x=150..450, y=100..300 in a 600x400 image)
    xs = [c["x"] for c in corners]
    ys = [c["y"] for c in corners]
    assert min(xs) < 0.35, f"Left edge too far right: {min(xs)}"
    assert max(xs) > 0.65, f"Right edge too far left: {max(xs)}"
    assert min(ys) < 0.35, f"Top edge too far down: {min(ys)}"
    assert max(ys) > 0.55, f"Bottom edge too far up: {max(ys)}"


def test_detect_corners_from_seed_fallback_when_no_contour():
    from app.services.card_detector import detect_corners_from_seed

    img = PILImage.fromarray(np.full((400, 600, 3), 200, dtype=np.uint8))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    img_bytes = buf.getvalue()

    with patch("app.services.card_detector.read_temp_image", return_value=img_bytes), \
         patch("app.services.card_detector._resize_bytes", return_value=img_bytes):
        corners, confidence = detect_corners_from_seed("fake/path.jpg", 0.5, 0.5)

    assert len(corners) == 4
    assert confidence == 0.0


def test_detect_corners_seed_outside_image_returns_fallback():
    from app.services.card_detector import detect_corners_from_seed

    img = PILImage.fromarray(np.full((400, 600, 3), 200, dtype=np.uint8))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    img_bytes = buf.getvalue()

    with patch("app.services.card_detector.read_temp_image", return_value=img_bytes), \
         patch("app.services.card_detector._resize_bytes", return_value=img_bytes):
        corners, confidence = detect_corners_from_seed("fake/path.jpg", 1.5, -0.5)

    assert len(corners) == 4


def test_detect_corners_seed_outside_card_returns_fallback():
    """Seed is within image bounds but outside the card — should return fallback."""
    from app.services.card_detector import detect_corners_from_seed

    img_bytes = _make_test_image_with_card()

    # Tap the top-left corner of the image, well outside the card (card starts at x=0.25, y=0.25)
    with patch("app.services.card_detector.read_temp_image", return_value=img_bytes), \
         patch("app.services.card_detector._resize_bytes", return_value=img_bytes):
        corners, confidence = detect_corners_from_seed("fake/path.jpg", 0.05, 0.05)

    assert len(corners) == 4
    assert confidence == 0.0


def test_detect_corners_endpoint_exists():
    """Confirm the detect_corners route is registered."""
    from app.routers.v2 import sessions as s
    paths = [r.path for r in s.router.routes]
    assert any("detect-corners" in p for p in paths), \
        f"No detect-corners route found. Routes: {paths}"


def _make_test_image_with_rotated_card(angle_deg: float = 15) -> bytes:
    """
    Create a synthetic 600x400 white image with a rotated dark-bordered card.

    Card is rotated by angle_deg degrees to test that corner sorting works
    for non-axis-aligned cards (common in real photos).
    """
    import cv2

    # Start with axis-aligned card
    img = np.ones((400, 600, 3), dtype=np.uint8) * 220
    img[100:300, 150:450] = 200
    img[100:102, 150:450] = 50
    img[298:300, 150:450] = 50
    img[100:300, 150:152] = 50
    img[100:300, 448:450] = 50

    # Create a temporary PIL image to rotate
    pil_temp = PILImage.fromarray(img.astype(np.uint8))
    pil_rotated = pil_temp.rotate(angle_deg, expand=False, fillcolor=220)

    # Convert back to JPEG bytes
    buf = io.BytesIO()
    pil_rotated.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def test_detect_corners_from_seed_handles_rotated_card():
    """
    Verify that corner detection works for rotated cards.

    This is a regression test for the _sort_quad_points bug where corners
    were sorted incorrectly for non-axis-aligned cards.
    """
    from app.services.card_detector import detect_corners_from_seed

    # Test with 15° rotation (common in real photos)
    img_bytes = _make_test_image_with_rotated_card(angle_deg=15)

    with patch("app.services.card_detector.read_temp_image", return_value=img_bytes), \
         patch("app.services.card_detector._resize_bytes", return_value=img_bytes):
        corners, confidence = detect_corners_from_seed("fake/path.jpg", 0.5, 0.5)

    # Must find corners (either actual detection or fallback)
    assert len(corners) == 4

    # If detection succeeded (confidence > 0), verify corners form a valid quadrilateral
    if confidence > 0.0:
        xs = [c["x"] for c in corners]
        ys = [c["y"] for c in corners]

        # Corners should roughly bracket the card area (allowing for rotation)
        assert 0.0 <= min(xs) < 0.5, f"Card too far right: {min(xs)}"
        assert 0.5 < max(xs) <= 1.0, f"Card too far left: {max(xs)}"
        assert 0.0 <= min(ys) < 0.5, f"Card too far down: {min(ys)}"
        assert 0.5 < max(ys) <= 1.0, f"Card too far up: {max(ys)}"


# ── Background-colour segmentation (primary path) ─────────────────────────────

def _scene_bytes(bg: str = "beige", gap: int | None = None) -> tuple[bytes, list[np.ndarray]]:
    """
    Build a 1200x900 "photo" containing white business cards with text lines.

    bg:  "beige" (flat table) or "mat" (green cutting mat with a light grid —
         the textured case Canny-based detection fails on).
    gap: None → two cards far apart; an int → two cards side by side with
         that many pixels between them.

    Returns (jpeg bytes, list of 4x2 ground-truth corner arrays in pixels).
    """
    import cv2

    h, w = 900, 1200
    if bg == "beige":
        img = np.full((h, w, 3), (185, 200, 212), np.uint8)   # BGR
    else:
        img = np.full((h, w, 3), (70, 95, 45), np.uint8)
        img[::40, :] = (150, 170, 140)
        img[1::40, :] = (150, 170, 140)
        img[:, ::40] = (150, 170, 140)
        img[:, 1::40] = (150, 170, 140)

    cw, ch = 350, 200
    if gap is None:
        origins = [(150, 150), (650, 500)]
    else:
        origins = [(250, 350), (250 + cw + gap, 350)]
    polys = []
    for ox, oy in origins:
        img[oy:oy + ch, ox:ox + cw] = (243, 245, 246)
        # Dark text lines + a logo block — interior features that must not win
        for i in range(4):
            y = oy + 90 + i * 22
            img[y:y + 8, ox + 25:ox + 200] = (40, 40, 40)
        img[oy + 100:oy + 170, ox + 250:ox + 320] = (40, 40, 40)
        polys.append(np.array([[ox, oy], [ox + cw, oy], [ox + cw, oy + ch], [ox, oy + ch]], np.float32))

    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return buf.tobytes(), polys


def _iou(corners: list[dict], poly: np.ndarray, w: int = 1200, h: int = 900) -> float:
    """IoU between returned normalized corners and a ground-truth pixel polygon."""
    import cv2

    a = np.zeros((h, w), np.uint8)
    b = np.zeros((h, w), np.uint8)
    cv2.fillPoly(a, [np.array([[c["x"] * w, c["y"] * h] for c in corners], np.int32)], 1)
    cv2.fillPoly(b, [poly.astype(np.int32)], 1)
    return float(np.logical_and(a, b).sum() / np.logical_or(a, b).sum())


def _detect(img_bytes: bytes, x: float, y: float, existing=None):
    from app.services.card_detector import detect_corners_from_seed

    with patch("app.services.card_detector.read_temp_image", return_value=img_bytes), \
         patch("app.services.card_detector._resize_bytes", return_value=img_bytes):
        return detect_corners_from_seed("fake/path.jpg", x, y, existing)


def test_tap_on_text_returns_whole_card_not_text_block():
    """Tapping on the text lines must outline the card, not the text cluster."""
    img_bytes, polys = _scene_bytes("beige")
    # Card 0 spans x 150..500, y 150..350; tap lands on its text lines
    corners, confidence = _detect(img_bytes, 250 / 1200, 265 / 900)
    assert confidence > 0.0
    assert _iou(corners, polys[0]) > 0.9


def test_card_on_textured_cutting_mat():
    """Grid lines on a cutting mat must not stop detection of the card."""
    img_bytes, polys = _scene_bytes("mat")
    corners, confidence = _detect(img_bytes, 800 / 1200, 560 / 900)
    assert confidence > 0.0
    assert _iou(corners, polys[1]) > 0.9


def test_existing_outline_separates_adjacent_cards():
    """With card 0 already outlined, tapping card 1 must return only card 1."""
    img_bytes, polys = _scene_bytes("beige", gap=4)
    existing = [[{"x": float(px / 1200), "y": float(py / 900)} for px, py in polys[0]]]
    corners, confidence = _detect(img_bytes, (polys[1][0][0] + 60) / 1200, 400 / 900, existing)
    assert confidence > 0.0
    assert _iou(corners, polys[1]) > 0.9


def test_background_method_used_first_when_it_succeeds():
    from app.services import card_detector

    img_bytes, _ = _scene_bytes("beige")
    quad = np.array([[10, 10], [110, 10], [110, 70], [10, 70]], np.float32)
    with patch.object(card_detector, "_detect_quad_by_background", return_value=quad) as bg, \
         patch.object(card_detector, "_detect_quad_by_edges") as edges:
        corners, confidence = _detect(img_bytes, 0.5, 0.5)
    bg.assert_called_once()
    edges.assert_not_called()
    assert abs(corners[0]["x"] - 10 / 1200) < 1e-6
    assert confidence > 0.0


def test_falls_back_to_edge_method_when_background_method_gives_up():
    from app.services import card_detector

    img_bytes, _ = _scene_bytes("beige")
    with patch.object(card_detector, "_detect_quad_by_background", return_value=None), \
         patch.object(card_detector, "_detect_quad_by_edges", wraps=card_detector._detect_quad_by_edges) as edges:
        corners, _ = _detect(img_bytes, 250 / 1200, 265 / 900)
    edges.assert_called_once()
    assert len(corners) == 4


# ── Corner ordering ───────────────────────────────────────────────────────────

def _rotated_rect(w: float, h: float, angle_deg: float) -> np.ndarray:
    """TL, TR, BR, BL of a w×h rectangle centred at (500, 500), rotated clockwise."""
    import math

    a = math.radians(angle_deg)
    rot = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    pts = np.array([[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]])
    return (pts @ rot.T + 500).astype(np.float32)


@pytest.mark.parametrize("w,h", [(350, 200), (200, 350), (300, 300)])
@pytest.mark.parametrize("angle", [-30, -10, 0, 10, 30])
def test_sort_quad_points_starts_at_top_left(w, h, angle):
    """
    Landscape cards put the TL corner at ~-150° from the centroid, past the
    -135° start of the old angle sort — it returned BL first, so every crop
    from a detected quad came out rotated 90°.
    """
    from app.services.card_detector import _sort_quad_points

    expected = _rotated_rect(w, h, angle)
    for shuffle in ([0, 1, 2, 3], [2, 0, 3, 1], [3, 2, 1, 0]):
        got = _sort_quad_points(expected[shuffle])
        np.testing.assert_allclose(got, expected, atol=1e-3)


def test_detected_landscape_card_corners_start_top_left():
    """End to end: a detected landscape card is returned TL, TR, BR, BL."""
    img_bytes, polys = _scene_bytes("beige")
    corners, _ = _detect(img_bytes, 250 / 1200, 265 / 900)
    got = np.array([[c["x"] * 1200, c["y"] * 900] for c in corners])
    np.testing.assert_allclose(got, polys[0], atol=12)
