from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.config import Settings, get_settings
from app.schemas.document import (
    DocumentRecord,
    DocumentUpdate,
    UploadErrorItem,
    UploadResponse,
)
from app.services.extraction import (
    ExtractionFailedError,
    build_record,
    extract_document,
)
from app.services.gemma import (
    ModelCapabilityError,
    ModelUnavailableError,
    get_model_client,
)
from app.services.ocr import get_ocr_fn
from app.services.store import DocumentStore, SessionLimitError, get_store
from app.services.uploads import (
    UploadError,
    prepare_images,
    safe_filename,
    validate_upload,
)
from app.services.validation import apply_validation

# NOTE: only error *codes* are logged below, never filenames, contents or extracted values.
logger = logging.getLogger("invoicelens.documents")

router = APIRouter(prefix="/api/documents", tags=["documents"])

MAX_FILES_PER_REQUEST = 20


def _describe_error(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, UploadError):
        return exc.code, exc.message
    if isinstance(exc, ModelUnavailableError):
        return "model_unavailable", str(exc)
    if isinstance(exc, ModelCapabilityError):
        return "model_no_vision", str(exc)
    if isinstance(exc, ExtractionFailedError):
        return exc.code, str(exc)
    if isinstance(exc, SessionLimitError):
        return "session_limit_exceeded", str(exc)
    return "processing_error", "Unexpected error while processing this file. Nothing was saved."


@router.post("")
async def upload_documents(
    files: list[UploadFile] = File(...),
    client=Depends(get_model_client),
    ocr_fn=Depends(get_ocr_fn),
    store: DocumentStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
):
    """Extract one or more files. Each file succeeds or fails on its own."""
    if len(files) > MAX_FILES_PER_REQUEST:
        raise HTTPException(400, f"Upload at most {MAX_FILES_PER_REQUEST} files at a time.")

    documents: list[DocumentRecord] = []
    errors: list[UploadErrorItem] = []

    for upload in files:
        filename = safe_filename(upload.filename)
        try:
            # Read one byte past the limit so oversized files are detected without loading more.
            data = await upload.read(settings.max_upload_bytes + 1)
            validated = validate_upload(
                filename,
                data,
                max_bytes=settings.max_upload_bytes,
                max_pages=settings.max_pdf_pages,
            )
            images = await run_in_threadpool(prepare_images, validated, settings.max_pdf_pages)
            result = await run_in_threadpool(
                lambda images=images: extract_document(images, client, ocr_fn=ocr_fn)
            )
            record = build_record(result, filename, settings.arithmetic_tolerance)
            store.add(record)
            documents.append(record)
        except Exception as exc:  # noqa: BLE001 — isolate failures per uploaded file
            code, message = _describe_error(exc)
            if code == "processing_error":
                logger.error("unexpected processing error type=%s", type(exc).__name__)
            else:
                logger.info("upload rejected code=%s", code)
            errors.append(UploadErrorItem(filename=filename, code=code, message=message))
        finally:
            await upload.close()  # all processing is in memory; no temp files to clean up

    status_code = 200
    if errors and not documents:
        status_code = 503 if all(e.code == "model_unavailable" for e in errors) else 422
    body = UploadResponse(documents=documents, errors=errors)
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


@router.get("", response_model=list[DocumentRecord])
def list_documents(store: DocumentStore = Depends(get_store)):
    return store.list()


@router.delete("", status_code=204)
def clear_documents(store: DocumentStore = Depends(get_store)) -> None:
    """Delete the whole current batch."""
    store.clear()


@router.get("/{document_id}", response_model=DocumentRecord)
def get_document(document_id: str, store: DocumentStore = Depends(get_store)):
    record = store.get(document_id)
    if record is None:
        raise HTTPException(404, "Document not found.")
    return record


@router.patch("/{document_id}", response_model=DocumentRecord)
def update_document(
    document_id: str,
    body: DocumentUpdate,
    store: DocumentStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
):
    """Save corrections. Only fields present in the body change; validation is re-run."""
    record = store.get(document_id)
    if record is None:
        raise HTTPException(404, "Document not found.")

    changes = body.model_dump(exclude_unset=True)
    reviewed = changes.pop("reviewed", None)

    corrected = set(record.corrected_fields)
    applied = {}
    for key, value in changes.items():
        if getattr(record, key) != value:
            applied[key] = value
            corrected.add(key)

    updated = record.model_copy(
        update={
            **applied,
            "corrected_fields": sorted(corrected),
            # Saving counts as reviewing, unless the client explicitly says otherwise.
            "reviewed": True if reviewed is None else reviewed,
        }
    )
    updated = apply_validation(updated, settings.arithmetic_tolerance)
    if not store.update(updated):
        raise HTTPException(404, "Document not found.")
    return updated


@router.delete("/{document_id}", status_code=204)
def delete_document(document_id: str, store: DocumentStore = Depends(get_store)) -> None:
    if not store.delete(document_id):
        raise HTTPException(404, "Document not found.")