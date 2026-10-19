
"""Application settings, read from environment variables (and optional .env files)."""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _BACKEND_DIR.parent

# Real environment variables always win over .env files (override=False).
for _env_file in (_REPO_ROOT / ".env", _BACKEND_DIR / ".env"):
    if _env_file.exists():
        load_dotenv(_env_file, override=False)

VALID_EXTRACTION_MODES = ("gemma", "mock")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc

    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number.")

    return value


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _env_decimal(name: str, default: str) -> Decimal:
    raw = os.getenv(name) or default
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a decimal number, got {raw!r}") from exc

    if not value.is_finite():
        raise ValueError(f"{name} must be a finite decimal number.")

    return value


@dataclass(frozen=True)
class Settings:
    app_env: str = "development"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "gemma3:4b"
    ollama_timeout_seconds: float = 90.0
    max_upload_mb: float = 10.0
    max_pdf_pages: int = 5
    frontend_origin: str = "http://localhost:5173"
    extraction_mode: str = "gemma"
    arithmetic_tolerance: Decimal = Decimal("1.00")
    tesseract_cmd: str | None = None
    session_ttl_seconds: float = 3600.0
    max_sessions: int = 100
    max_records_per_session: int = 50
    require_session_id: bool | None = None
    max_request_mb: float = 25.0

    def __post_init__(self) -> None:
        if self.is_hosted and self.frontend_origin.strip() == "*":
            raise ValueError(
                "FRONTEND_ORIGIN cannot be '*' in hosted/production environments."
            )

        for name, value in (
            ("MAX_UPLOAD_MB", self.max_upload_mb),
            ("MAX_REQUEST_MB", self.max_request_mb),
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive, finite number.")

        if self.max_pdf_pages < 1:
            raise ValueError("MAX_PDF_PAGES must be at least 1.")

    @property
    def max_upload_bytes(self) -> int:
        return int(self.max_upload_mb * 1024 * 1024)

    @property
    def max_request_bytes(self) -> int:
        return int(self.max_request_mb * 1024 * 1024)

    @property
    def is_local_inference(self) -> bool:
        """True only when real inference is configured and points at this machine."""
        if self.extraction_mode != "gemma":
            return False
        host = urlparse(self.ollama_base_url).hostname or ""
        return host in _LOCAL_HOSTS

    @property
    def is_hosted(self) -> bool:
        """True when running in a hosted / production environment."""
        return self.app_env.strip().lower() in (
            "production",
            "staging",
            "hosted",
            "demo",
        )

    @property
    def session_id_required(self) -> bool:
        """Whether requests must provide a session identifier."""
        if self.require_session_id is not None:
            return self.require_session_id
        return self.is_hosted

    @classmethod
    def from_env(cls) -> Settings:
        mode = (os.getenv("EXTRACTION_MODE") or "gemma").strip().lower()
        if mode not in VALID_EXTRACTION_MODES:
            raise ValueError(
                f"EXTRACTION_MODE must be one of {VALID_EXTRACTION_MODES}, got {mode!r}"
            )

        raw_req = os.getenv("REQUIRE_SESSION_ID")
        require_session_id = (
            raw_req.strip().lower() in ("1", "true", "yes")
            if raw_req is not None
            else None
        )

        return cls(
            app_env=os.getenv("APP_ENV", "development"),
            ollama_base_url=os.getenv(
                "OLLAMA_BASE_URL", "http://localhost:11434"
            ).rstrip("/"),
            ollama_model=os.getenv("OLLAMA_MODEL", "gemma3:4b"),
            ollama_timeout_seconds=_env_float("OLLAMA_TIMEOUT_SECONDS", 90.0),
            max_upload_mb=_env_float("MAX_UPLOAD_MB", 10.0),
            max_request_mb=_env_float("MAX_REQUEST_MB", 25.0),
            max_pdf_pages=_env_int("MAX_PDF_PAGES", 5),
            frontend_origin=os.getenv(
                "FRONTEND_ORIGIN", "http://localhost:5173"
            ),
            extraction_mode=mode,
            arithmetic_tolerance=_env_decimal(
                "ARITHMETIC_TOLERANCE", "1.00"
            ),
            tesseract_cmd=os.getenv("TESSERACT_CMD") or None,
            session_ttl_seconds=_env_float("SESSION_TTL_SECONDS", 3600.0),
            max_sessions=_env_int("MAX_SESSIONS", 100),
            max_records_per_session=_env_int(
                "MAX_RECORDS_PER_SESSION", 50
            ),
            require_session_id=require_session_id,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
