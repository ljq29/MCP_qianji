from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_BILL_IMAGE_KEYS = 6
MAX_BILL_IMAGE_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_PROCESSED_BILL_IMAGE_BYTES = 3 * 1024 * 1024
MAX_BILL_IMAGE_DIMENSION = 8_192
MAX_BILL_IMAGE_PIXELS = 40_000_000
BILL_IMAGE_TARGET_SHORT = 1080
BILL_IMAGE_TARGET_LONG = 1920
SUPPORTED_BILL_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}

_IMAGE_KEY_RE = re.compile(r"^[A-Za-z0-9._-]{1,256}$")
_JPEG_QUALITIES = (85, 80, 75, 70, 65, 60)


class BillImageValidationError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_detail(self) -> dict[str, Any]:
        detail: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            detail["details"] = self.details
        return detail


@dataclass(frozen=True)
class ProcessedBillImage:
    filename: str
    content: bytes
    content_type: str
    original: dict[str, Any]
    uploaded: dict[str, Any]


def validate_bill_image_keys(images: list[str] | None) -> list[str] | None:
    if images is None:
        return None
    if not isinstance(images, list):
        raise ValueError("images must be a list of Qianji image keys")
    if len(images) > MAX_BILL_IMAGE_KEYS:
        raise ValueError(f"images can contain at most {MAX_BILL_IMAGE_KEYS} image keys")

    normalized: list[str] = []
    for image in images:
        if not isinstance(image, str):
            raise ValueError("images must contain only Qianji image keys")
        key = image.strip()
        lower_key = key.lower()
        if (
            not key
            or lower_key.startswith(("http://", "https://", "data:"))
            or "://" in key
            or "/" in key
            or "\\" in key
            or not _IMAGE_KEY_RE.fullmatch(key)
        ):
            raise ValueError("images must contain Qianji image keys, not URLs, paths, or inline content")
        normalized.append(key)
    return normalized


def process_bill_image(
    *,
    filename: str,
    content: bytes,
    mime_type: str = "application/octet-stream",
) -> ProcessedBillImage:
    if not isinstance(content, bytes):
        raise BillImageValidationError("invalid_image", "Image content must be bytes")
    if len(content) > MAX_BILL_IMAGE_UPLOAD_BYTES:
        raise BillImageValidationError(
            "image_too_large",
            f"Image file is larger than {MAX_BILL_IMAGE_UPLOAD_BYTES} bytes",
            {"size_bytes": len(content), "max_bytes": MAX_BILL_IMAGE_UPLOAD_BYTES},
        )

    image = _open_supported_image(content)
    original_format = str(image.format or "").upper()
    original_width, original_height = image.size
    _validate_dimensions(original_width, original_height)

    original = {
        "format": original_format,
        "width": original_width,
        "height": original_height,
        "size_bytes": len(content),
    }

    image = ImageOps.exif_transpose(image)
    image.load()
    image = _to_rgb(image)
    target_size = _target_size(image.size)
    if target_size != image.size:
        image = image.resize(target_size, Image.Resampling.LANCZOS)

    uploaded_content = _encode_jpeg_under_limit(image)
    uploaded = {
        "format": "JPEG",
        "width": image.width,
        "height": image.height,
        "size_bytes": len(uploaded_content),
    }
    return ProcessedBillImage(
        filename=_jpeg_filename(filename),
        content=uploaded_content,
        content_type="image/jpeg",
        original=original,
        uploaded=uploaded,
    )


def _open_supported_image(content: bytes) -> Image.Image:
    try:
        image = Image.open(BytesIO(content))
        image_format = str(image.format or "").upper()
        if image_format not in SUPPORTED_BILL_IMAGE_FORMATS:
            raise BillImageValidationError(
                "unsupported_image_type",
                "Only JPEG, PNG, and WebP bill images are supported",
                {"format": image_format or None},
            )
        return image
    except BillImageValidationError:
        raise
    except Image.DecompressionBombError as exc:
        raise BillImageValidationError(
            "image_too_many_pixels",
            "Image exceeds the pixel safety limit",
        ) from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise BillImageValidationError("invalid_image", "Image content could not be decoded") from exc


def _validate_dimensions(width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise BillImageValidationError("image_too_small", "Image dimensions must be positive")
    if max(width, height) > MAX_BILL_IMAGE_DIMENSION:
        raise BillImageValidationError(
            "image_too_many_pixels",
            f"Image dimensions exceed {MAX_BILL_IMAGE_DIMENSION}px",
            {"width": width, "height": height, "max_dimension": MAX_BILL_IMAGE_DIMENSION},
        )
    if width * height > MAX_BILL_IMAGE_PIXELS:
        raise BillImageValidationError(
            "image_too_many_pixels",
            f"Image exceeds {MAX_BILL_IMAGE_PIXELS} pixels",
            {"width": width, "height": height, "max_pixels": MAX_BILL_IMAGE_PIXELS},
        )


def _target_size(size: tuple[int, int]) -> tuple[int, int]:
    width, height = size
    short_side = min(width, height)
    long_side = max(width, height)
    scale = min(BILL_IMAGE_TARGET_SHORT / short_side, BILL_IMAGE_TARGET_LONG / long_side, 1.0)
    if scale >= 1.0:
        return size
    return max(1, round(width * scale)), max(1, round(height * scale))


def _to_rgb(image: Image.Image) -> Image.Image:
    if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")


def _encode_jpeg_under_limit(image: Image.Image) -> bytes:
    last_content = b""
    for quality in _JPEG_QUALITIES:
        buf = BytesIO()
        image.save(buf, format="JPEG", quality=quality, optimize=True)
        last_content = buf.getvalue()
        if len(last_content) <= MAX_PROCESSED_BILL_IMAGE_BYTES:
            return last_content
    raise BillImageValidationError(
        "compressed_image_too_large",
        f"Compressed image is larger than {MAX_PROCESSED_BILL_IMAGE_BYTES} bytes",
        {"size_bytes": len(last_content), "max_bytes": MAX_PROCESSED_BILL_IMAGE_BYTES},
    )


def _jpeg_filename(filename: str) -> str:
    stem = Path(filename or "bill").stem.strip() or "bill"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "bill"
    return f"{safe}.jpg"
