"""Tesseract OCR fallback. Output is untrusted text and is validated downstream."""
from __future__ import annotations

import io

from PIL import Image

from app.config import get_settings

OCR_TIMEOUT_SECONDS = 30


class OCRError(Exception):
    """OCR ran but failed."""


class OCRUnavailableError(OCRError):
    """The Tesseract executable is not installed or not on PATH."""


def _tesseract():
    import pytesseract  # imported lazily so the app starts without Tesseract installed

    cmd = get_settings().tesseract_cmd
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    return pytesseract


def get_ocr_fn():
    """FastAPI dependency returning the OCR callable. Tests override this with a fake."""
    return ocr_images


def ocr_available() -> bool:
    try:
        pytesseract = _tesseract()
        pytesseract.get_tesseract_version()
        return True
    except (ImportError, OSError):
        return False
    except Exception as exc:
        # Handle the exception classes exposed by the real client or test fake.
        try:
            pytesseract = _tesseract()
        except (ImportError, OSError):
            return False

        if isinstance(
            exc,
            (
                pytesseract.TesseractNotFoundError,
                pytesseract.TesseractError,
            ),
        ):
            return False
        raise


def ocr_images(images: list[bytes]) -> str:
    """Run OCR over PNG/JPEG bytes and return the concatenated text."""
    pytesseract = _tesseract()
    pages: list[str] = []
    try:
        for data in images:
            with Image.open(io.BytesIO(data)) as image:
                pages.append(
                    pytesseract.image_to_string(
                        image,
                        lang="eng",
                        timeout=OCR_TIMEOUT_SECONDS,
                    )
                )
    except pytesseract.TesseractNotFoundError as exc:
        raise OCRUnavailableError(
            "Tesseract is not installed or not on PATH (set TESSERACT_CMD to its full path)."
        ) from exc
    except (pytesseract.TesseractError, RuntimeError) as exc:
        raise OCRError("Tesseract failed to read the document.") from exc
    return "\n\n".join(page.strip() for page in pages).strip()