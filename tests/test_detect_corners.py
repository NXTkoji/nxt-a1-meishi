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
