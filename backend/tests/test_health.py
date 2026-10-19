from __future__ import annotations

from app.config import Settings, get_settings
from app.main import app
from app.services.gemma import MockModelClient, ModelStatus, get_model_client


def test_health_in_gemma_mode_reports_model_status(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["extraction_mode"] == "gemma"
    assert body["model"]["name"] == "fake-model"
    assert body["model"]["reachable"] is True
    assert body["limits"] == {
        "max_upload_mb": 10.0,
        "max_request_mb": 25.0,
        "max_pdf_pages": 5,
    }
    assert "in-memory" in body["storage"]


def test_health_reports_unreachable_model_without_failing(client, fake_client):
    fake_client.check_model = lambda timeout=None: ModelStatus(
        "gemma3:4b", False, False, None, "Cannot reach Ollama at http://localhost:11434."
    )
    response = client.get("/api/health")
    assert response.status_code == 200
    model = response.json()["model"]
    assert model["reachable"] is False
    assert "Cannot reach Ollama" in model["detail"]


def test_mock_mode_is_never_reported_as_local_inference(client, base_settings):
    mock_settings = Settings(extraction_mode="mock")
    app.dependency_overrides[get_settings] = lambda: mock_settings
    app.dependency_overrides[get_model_client] = lambda: MockModelClient()
    body = client.get("/api/health").json()
    assert body["extraction_mode"] == "mock"
    assert body["local_processing"] is False


def test_local_processing_flag_depends_on_ollama_host():
    assert Settings(ollama_base_url="http://localhost:11434").is_local_inference is True
    assert Settings(ollama_base_url="http://127.0.0.1:11434").is_local_inference is True
    assert Settings(ollama_base_url="https://ollama.example.com").is_local_inference is False
