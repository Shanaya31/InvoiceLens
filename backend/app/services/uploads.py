"""Upload validation: size, extension, *file signature*, and real parsing.

Filenames and MIME types are never trusted on their own. Nothing is written to disk.
"""
from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass

from PIL import Image

from app.services import pdf_renderer

ALLOWED_EXTENSIONS = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".pdf": "pdf"}


class UploadError(Exception):
    """A single file was rejected. ``code`` is stable and machine-readable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ValidatedUpload:
    filename: str
    kind: str  # "jpeg" | "png" | "pdf"
    data: bytes
    page_count: int


def safe_filename(name: str | None) -> str:
    """Strip any path and control characters; the name is only ever used as a label."""
    base = os.path.basename((name or "").replace("\\", "/"))
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()
    return base[:120] or "upload"


def detect_kind(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"%PDF-"):
        return "pdf"
    return None


def validate_upload(filename: str, data: bytes, *, max_bytes: int, max_pages: int) -> ValidatedUpload:
    if len(data) == 0:
        raise UploadError("empty_file", "The file is empty.")
    if len(data) > max_bytes:
        limit_mb = max_bytes / (1024 * 1024)
        raise UploadError("file_too_large", f"The file is larger than the {limit_mb:g} MB limit.")

    extension = os.path.splitext(filename)[1].lower()
    expected = ALLOWED_EXTENSIONS.get(extension)
    if expected is None:
        raise UploadError("unsupported_type", "Only JPG, JPEG, PNG and PDF files are supported.")

    kind = detect_kind(data)
    if kind is None:
        raise UploadError("unsupported_type", "The file content is not a JPG, PNG or PDF.")
    if kind != expected:
        raise UploadError(
            "type_mismatch", "The file extension does not match the file content."
        )

    if kind == "pdf":
        try:
            pages = pdf_renderer.count_pdf_pages(data)
        except pdf_renderer.PdfError as exc:
            raise UploadError("invalid_pdf", str(exc)) from exc
        if pages < 1:
            raise UploadError("invalid_pdf", "The PDF has no pages.")
        if pages > max_pages:
            raise UploadError(
                "too_many_pages", f"The PDF has {pages} pages; the limit is {max_pages}."
            )
        return ValidatedUpload(filename, kind, data, pages)

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()  # structural check; the image must be re-opened afterwards
        pdf_pages = 1
    except Exception as exc:  # Pillow raises many different error types
        raise UploadError("invalid_image", "The image could not be read; it may be corrupt.") from exc
    return ValidatedUpload(filename, kind, data, pdf_pages)


def prepare_images(upload: ValidatedUpload, max_pages: int) -> list[bytes]:
    """Return PNG bytes ready for the model and for OCR."""
    try:
        if upload.kind == "pdf":
            return pdf_renderer.render_pdf_pages(upload.data, max_pages)
        return [pdf_renderer.normalize_image(upload.data)]
    except pdf_renderer.PdfError as exc:
        raise UploadError("invalid_pdf", str(exc)) from exc
    except Exception as exc:
        raise UploadError("invalid_image", "The image could not be processed.") from exc
