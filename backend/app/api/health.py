from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.config import Settings, get_settings
from app.services.gemma import get_model_client
from app.services.ocr import ocr_available

router = APIRouter(tags=["health"])


@router.get("/api/health")
def health(
    settings: Settings = Depends(get_settings),
    client=Depends(get_model_client),
) -> dict[str, Any]:
    """Always answers 200 when the app is up; model problems are reported in the body."""
    status = client.check_model()
    return {
        "status": "ok",
        "extraction_mode": settings.extraction_mode,
        "local_processing": settings.is_local_inference,
        "model": {
            "name": status.name,
            "reachable": status.reachable,
            "available": status.available,
            "supports_vision": status.supports_vision,
            "detail": status.detail,
        },
        "ocr": {"available": ocr_available()},
        "limits": {
            "max_upload_mb": settings.max_upload_mb,
            "max_request_mb": settings.max_request_mb,
            "max_pdf_pages": settings.max_pdf_pages,
        },
        "storage": "in-memory (records are lost when the server restarts)",
    }
