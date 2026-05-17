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

    assert confidence > 0.0


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
