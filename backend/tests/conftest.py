from __future__ import annotations

import io
from dataclasses import replace

import pymupdf
import pytest
from app.config import Settings, get_settings
from app.main import app
from app.services.gemma import get_model_client
from app.services.ocr import get_ocr_fn
from app.services.store import get_root_store
from fakes import FakeClient, FakeOCR
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw


@pytest.fixture(autouse=True)
def _clean_state():
    get_root_store().clear_all()
    yield
    get_root_store().clear_all()
    app.dependency_overrides.clear()


@pytest.fixture
def base_settings() -> Settings:
    # Explicit values so a developer's local .env cannot change test behaviour.
    return Settings(extraction_mode="gemma", max_upload_mb=10.0, max_pdf_pages=5)


@pytest.fixture
def fake_client() -> FakeClient:
    return FakeClient()


@pytest.fixture
def fake_ocr() -> FakeOCR:
    return FakeOCR()


@pytest.fixture
def client(fake_client: FakeClient, fake_ocr: FakeOCR, base_settings: Settings) -> TestClient:
    app.dependency_overrides[get_model_client] = lambda: fake_client
    app.dependency_overrides[get_ocr_fn] = lambda: fake_ocr
    app.dependency_overrides[get_settings] = lambda: base_settings
    return TestClient(app)


@pytest.fixture
def override_settings(base_settings: Settings):
    def _apply(**changes) -> Settings:
        new = replace(base_settings, **changes)
        app.dependency_overrides[get_settings] = lambda: new
        return new

    return _apply


@pytest.fixture
def make_png():
    def _make(width: int = 240, height: int = 120, text: str = "SYNTHETIC") -> bytes:
        image = Image.new("RGB", (width, height), "white")
        ImageDraw.Draw(image).text((10, 10), text, fill="black")
        out = io.BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()

    return _make


@pytest.fixture
def make_jpeg():
    def _make(width: int = 240, height: int = 120) -> bytes:
        image = Image.new("RGB", (width, height), "white")
        out = io.BytesIO()
        image.save(out, format="JPEG")
        return out.getvalue()

    return _make


@pytest.fixture
def make_pdf():
    def _make(pages: int = 1, **save_kwargs) -> bytes:
        doc = pymupdf.open()
        for number in range(pages):
            page = doc.new_page()
            page.insert_text((72, 72), f"Synthetic invoice page {number + 1}")
        data = doc.tobytes(**save_kwargs)
        doc.close()
        return data

    return _make
