
"""FastAPI application entry point: ``uvicorn app.main:app --reload``."""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import documents, export, health
from app.config import get_settings
from app.middleware import RequestBodyLimitMiddleware
from app.services.store import SESSION_HEADER


def create_app() -> FastAPI:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    settings = get_settings()

    app = FastAPI(
        title="InvoiceLens",
        version="0.1.0",
        description="Local-first invoice and receipt extraction with human review.",
    )

    # Add the body limiter first so CORS remains outside it and browser
    # clients can read 413 responses.
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_body_bytes=settings.max_request_bytes,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", SESSION_HEADER, "Accept"],
    )

    app.include_router(health.router)
    app.include_router(documents.router)
    app.include_router(export.router)
    return app


app = create_app()
