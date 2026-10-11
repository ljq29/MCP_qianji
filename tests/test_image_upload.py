from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from qianji import image_upload
from qianji.image_upload import BillImageValidationError


def _image_bytes(fmt: str = "PNG", size: tuple[int, int] = (64, 48)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, (40, 120, 200)).save(buf, format=fmt)
    return buf.getvalue()


def test_process_bill_image_verifies_real_image_and_converts_to_jpeg():
    processed = image_upload.process_bill_image(
        filename="receipt.png",
        content=_image_bytes("PNG", (64, 48)),
        mime_type="image/png",
    )

    assert processed.filename == "receipt.jpg"
    assert processed.content_type == "image/jpeg"
    assert processed.original == {
        "format": "PNG",
        "width": 64,
        "height": 48,
        "size_bytes": len(_image_bytes("PNG", (64, 48))),
    }
    assert processed.uploaded["format"] == "JPEG"
    assert processed.uploaded["width"] == 64
    assert processed.uploaded["height"] == 48
    assert processed.uploaded["size_bytes"] == len(processed.content)
    with Image.open(BytesIO(processed.content)) as img:
        assert img.format == "JPEG"
        assert img.size == (64, 48)


def test_process_bill_image_rejects_non_image_bytes():
    with pytest.raises(BillImageValidationError) as exc:
        image_upload.process_bill_image(
            filename="receipt.jpg",
            content=b"not-an-image",
            mime_type="image/jpeg",
        )

    assert exc.value.code == "invalid_image"


def test_process_bill_image_resizes_to_bill_image_target():
    processed = image_upload.process_bill_image(
        filename="wide.png",
        content=_image_bytes("PNG", (2400, 1200)),
        mime_type="image/png",
    )

    assert max(processed.uploaded["width"], processed.uploaded["height"]) <= 1920
    assert min(processed.uploaded["width"], processed.uploaded["height"]) <= 1080


def test_process_bill_image_rejects_over_configured_raw_size(monkeypatch):
    monkeypatch.setattr(image_upload, "MAX_BILL_IMAGE_UPLOAD_BYTES", 3)

    with pytest.raises(BillImageValidationError) as exc:
        image_upload.process_bill_image(
            filename="receipt.jpg",
            content=b"1234",
            mime_type="image/jpeg",
        )

    assert exc.value.code == "image_too_large"


def test_process_bill_image_rejects_over_configured_pixel_limit(monkeypatch):
    monkeypatch.setattr(image_upload, "MAX_BILL_IMAGE_DIMENSION", 32)

    with pytest.raises(BillImageValidationError) as exc:
        image_upload.process_bill_image(
            filename="receipt.png",
            content=_image_bytes("PNG", (64, 48)),
            mime_type="image/png",
        )

    assert exc.value.code == "image_too_many_pixels"


def test_process_bill_image_rejects_over_total_pixel_limit(monkeypatch):
    monkeypatch.setattr(image_upload, "MAX_BILL_IMAGE_PIXELS", 1000)

    with pytest.raises(BillImageValidationError) as exc:
        image_upload.process_bill_image(
            filename="receipt.png",
            content=_image_bytes("PNG", (64, 48)),
            mime_type="image/png",
        )

    assert exc.value.code == "image_too_many_pixels"


def test_process_bill_image_maps_decompression_bomb_error(monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)

    with pytest.raises(BillImageValidationError) as exc:
        image_upload.process_bill_image(
            filename="bomb.png",
            content=_image_bytes("PNG", (64, 48)),
            mime_type="image/png",
        )

    assert exc.value.code == "image_too_many_pixels"


def test_process_bill_image_rejects_when_compressed_output_still_too_large(monkeypatch):
    monkeypatch.setattr(image_upload, "MAX_PROCESSED_BILL_IMAGE_BYTES", 16)

    with pytest.raises(BillImageValidationError) as exc:
        image_upload.process_bill_image(
            filename="receipt.png",
            content=_image_bytes("PNG", (64, 48)),
            mime_type="image/png",
        )

    assert exc.value.code == "compressed_image_too_large"


def test_validate_bill_image_keys_rejects_urls_paths_base64_and_too_many():
    assert image_upload.validate_bill_image_keys(["_a.jpg", "_b_hg"]) == ["_a.jpg", "_b_hg"]

    for bad_key in ("https://example.com/a.jpg", "data:image/png;base64,aaa", "dir/a.jpg", r"dir\a.jpg", "aW1hZ2U="):
        with pytest.raises(ValueError):
            image_upload.validate_bill_image_keys([bad_key])

    with pytest.raises(ValueError):
        image_upload.validate_bill_image_keys([f"_{i}.jpg" for i in range(7)])
