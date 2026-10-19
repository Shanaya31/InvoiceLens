"""OCR wrapper tests use a controlled fake Tesseract. One optional test uses the real binary."""
from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from app.services import ocr
from app.services.ocr import OCRError, OCRUnavailableError, ocr_available, ocr_images
from PIL import Image, ImageDraw, ImageFont


class _NotFound(Exception):
    pass


class _TessError(Exception):
    pass


def fake_tesseract(image_to_string=None, version=None):
    def default(image, lang=None, timeout=None):
        return "page text  \n"

    def get_version():
        if version is not None:
            raise version
        return "5.0"

    return SimpleNamespace(
        image_to_string=image_to_string or default,
        get_tesseract_version=get_version,
        TesseractNotFoundError=_NotFound,
        TesseractError=_TessError,
        pytesseract=SimpleNamespace(tesseract_cmd="tesseract"),
    )


def png_bytes() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(out, format="PNG")
    return out.getvalue()


def test_pages_are_joined_and_trimmed(monkeypatch):
    monkeypatch.setattr(ocr, "_tesseract", lambda: fake_tesseract())
    assert ocr_images([png_bytes(), png_bytes()]) == "page text\n\npage text"


def test_missing_tesseract_is_reported_as_unavailable(monkeypatch):
    def raise_not_found(image, lang=None, timeout=None):
        raise _NotFound()

    monkeypatch.setattr(ocr, "_tesseract", lambda: fake_tesseract(raise_not_found))
    with pytest.raises(OCRUnavailableError, match="TESSERACT_CMD"):
        ocr_images([png_bytes()])


@pytest.mark.parametrize("error", [_TessError("bad"), RuntimeError("timeout")])
def test_tesseract_failures_become_ocr_error(monkeypatch, error):
    def fail(image, lang=None, timeout=None):
        raise error

    monkeypatch.setattr(ocr, "_tesseract", lambda: fake_tesseract(fail))
    with pytest.raises(OCRError) as info:
        ocr_images([png_bytes()])
    assert not isinstance(info.value, OCRUnavailableError)


def test_ocr_available_false_when_binary_missing(monkeypatch):
    monkeypatch.setattr(ocr, "_tesseract", lambda: fake_tesseract(version=_NotFound()))
    assert ocr_available() is False


def test_ocr_available_true_with_working_binary(monkeypatch):
    monkeypatch.setattr(ocr, "_tesseract", lambda: fake_tesseract())
    assert ocr_available() is True


@pytest.mark.skipif(not ocr_available(), reason="Tesseract is not installed on this machine")
def test_real_tesseract_reads_a_synthetic_image():
    image = Image.new("RGB", (600, 120), "white")
    ImageDraw.Draw(image).text((20, 30), "INVOICE 1042", fill="black", font=ImageFont.load_default(size=56))
    out = io.BytesIO()
    image.save(out, format="PNG")
    assert "INVOICE" in ocr_images([out.getvalue()]).upper()
