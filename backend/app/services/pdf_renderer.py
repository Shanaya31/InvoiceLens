"""PDF page counting/rendering and image normalisation. Everything happens in memory."""
from __future__ import annotations

import io

try:  # PyMuPDF >= 1.24 exposes ``pymupdf``; older releases only ``fitz``.
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz  # type: ignore

from PIL import Image, ImageOps

# Refuse "decompression bombs": tiny files that expand into enormous bitmaps.
Image.MAX_IMAGE_PIXELS = 50_000_000

MAX_IMAGE_SIDE = 2000  # longest side sent to the model / OCR, in pixels
RENDER_DPI = 150
MAX_RENDER_SIDE = 2400


class PdfError(ValueError):
    """The PDF could not be opened or is not usable (corrupt, encrypted, empty...)."""


def _open_pdf(data: bytes):
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as exc:  # PyMuPDF raises several error types
        raise PdfError("The file is not a readable PDF.") from exc
    if doc.needs_pass:
        doc.close()
        raise PdfError("Password-protected PDFs are not supported.")
    return doc


def count_pdf_pages(data: bytes) -> int:
    doc = _open_pdf(data)
    try:
        return doc.page_count
    finally:
        doc.close()


def render_pdf_pages(data: bytes, max_pages: int) -> list[bytes]:
    """Render up to ``max_pages`` pages to PNG bytes."""
    doc = _open_pdf(data)
    try:
        images: list[bytes] = []
        for index in range(min(doc.page_count, max_pages)):
            page = doc.load_page(index)
            longest = max(page.rect.width, page.rect.height) or 1.0
            scale = min(RENDER_DPI / 72.0, MAX_RENDER_SIDE / longest)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            images.append(pixmap.tobytes("png"))
        return images
    except PdfError:
        raise
    except Exception as exc:
        raise PdfError("A PDF page could not be rendered.") from exc
    finally:
        doc.close()


def normalize_image(data: bytes) -> bytes:
    """Decode, auto-rotate (EXIF), convert to RGB, downscale and re-encode as PNG."""
    with Image.open(io.BytesIO(data)) as image:
        image = ImageOps.exif_transpose(image)
        image = image.convert("RGB")
        image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
        out = io.BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()
