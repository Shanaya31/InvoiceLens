
from __future__ import annotations

from app.middleware import RequestBodyLimitMiddleware
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


def make_test_app(limit: int = 5) -> FastAPI:
    app = FastAPI()

    @app.post("/api/documents")
    async def upload(request: Request) -> dict[str, int]:
        body = await request.body()
        return {"received_bytes": len(body)}

    @app.post("/other")
    async def other(request: Request) -> dict[str, int]:
        body = await request.body()
        return {"received_bytes": len(body)}

    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_body_bytes=limit,
    )
    return app


def test_rejects_declared_content_length_over_limit():
    client = TestClient(make_test_app(limit=5))

    response = client.post("/api/documents", content=b"123456")

    assert response.status_code == 413
    assert response.json()["detail"] == (
        "Upload request exceeds the configured size limit."
    )


def test_accepts_body_within_limit():
    client = TestClient(make_test_app(limit=5))

    response = client.post("/api/documents", content=b"12345")

    assert response.status_code == 200
    assert response.json() == {"received_bytes": 5}


def test_rejects_streamed_body_without_content_length():
    client = TestClient(make_test_app(limit=5))

    response = client.post(
        "/api/documents",
        content=iter([b"123", b"456"]),
    )

    assert response.status_code == 413
    assert response.json()["detail"] == (
        "Upload request exceeds the configured size limit."
    )


def test_other_routes_are_not_subject_to_upload_limit():
    client = TestClient(make_test_app(limit=5))

    response = client.post("/other", content=b"123456")

    assert response.status_code == 200
    assert response.json() == {"received_bytes": 6}
