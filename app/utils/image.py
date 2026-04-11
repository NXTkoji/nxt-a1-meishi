import base64
import io

from PIL import Image

MAX_DIMENSION = 1568  # Claude Vision optimal max resolution


def resize_image_base64(b64_data: str, max_dim: int = MAX_DIMENSION) -> str:
    """Resize an image (base64) so its longest side is at most max_dim pixels."""
    raw = base64.b64decode(b64_data)
    img = Image.open(io.BytesIO(raw))

    if max(img.size) <= max_dim:
        return b64_data

    ratio = max_dim / max(img.size)
    new_size = (int(img.width * ratio), int(img.height * ratio))
    img = img.resize(new_size, Image.LANCZOS)

    buf = io.BytesIO()
    fmt = img.format or "JPEG"
    img.save(buf, format=fmt, quality=90)
    return base64.b64encode(buf.getvalue()).decode()


def bytes_to_base64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def base64_to_bytes(b64_data: str) -> bytes:
    return base64.b64decode(b64_data)
