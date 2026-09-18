"""Photo handling: validate, rotate, thumbnail, and shrink for the model."""
import base64
import hashlib
import io
from dataclasses import dataclass

from PIL import Image, ImageOps

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
THUMBNAIL_EDGE = 400


@dataclass
class PreparedImage:
    data: bytes
    mime_type: str
    sha256: str
    width: int | None
    height: int | None
    thumbnail: bytes | None
    model_data_url: str


def prepare(data: bytes, mime_type: str, max_edge: int = 1600) -> PreparedImage:
    """Keep the original bytes for the record; derive a thumbnail and a
    downscaled JPEG for the model."""
    if not data:
        raise ValueError("The uploaded file is empty.")

    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
    except Exception as exc:  # Pillow raises a grab-bag of exception types here.
        raise ValueError("That file does not look like an image Pillow can read.") from exc

    with Image.open(io.BytesIO(data)) as image:
        image = ImageOps.exif_transpose(image)
        width, height = image.size
        rgb = image.convert("RGB")
        thumbnail = _encode(ImageOps.contain(rgb.copy(), (THUMBNAIL_EDGE, THUMBNAIL_EDGE)))
        for_model = rgb if max(rgb.size) <= max_edge else ImageOps.contain(rgb, (max_edge, max_edge))
        model_bytes = _encode(for_model, quality=90)

    return PreparedImage(
        data=data,
        mime_type=mime_type if mime_type in ALLOWED_MIME else "application/octet-stream",
        sha256=hashlib.sha256(data).hexdigest(),
        width=width,
        height=height,
        thumbnail=thumbnail,
        model_data_url="data:image/jpeg;base64," + base64.b64encode(model_bytes).decode("ascii"),
    )


def _encode(image: Image.Image, quality: int = 80) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()
