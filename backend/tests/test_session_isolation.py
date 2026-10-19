"""Integration tests for session-partitioned document isolation, limits, and cleanup."""
from __future__ import annotations

import csv
import io
import time

from fakes import GOOD_INVOICE

SESSION_A = "visitor-session-aaaa-1111"
SESSION_B = "visitor-session-bbbb-2222"


def _headers(session_id: str) -> dict:
    return {"X-Session-ID": session_id}


def _upload(client, files, session_id: str):
    return client.post(
        "/api/documents",
        files=[("files", f) for f in files],
        headers=_headers(session_id),
    )


def _png(make_png, name="invoice.png"):
    return (name, make_png(), "image/png")


def _read_csv(response):
    return list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))


def test_two_visitors_listing_isolation(client, fake_client, make_png):
    fake_client.responses = [
        {**GOOD_INVOICE, "vendor_name": "Vendor A Pty"},
        {**GOOD_INVOICE, "vendor_name": "Vendor B Ltd"},
    ]
    # Visitor A uploads
    resp_a = _upload(client, [_png(make_png, "invoice_a.png")], SESSION_A)
    assert resp_a.status_code == 200
    doc_a_id = resp_a.json()["documents"][0]["document_id"]

    # Visitor B uploads
    resp_b = _upload(client, [_png(make_png, "invoice_b.png")], SESSION_B)
    assert resp_b.status_code == 200
    doc_b_id = resp_b.json()["documents"][0]["document_id"]
    assert client.get(
        f"/api/documents/{doc_b_id}", headers=_headers(SESSION_B)
    ).status_code == 200

    # Visitor B clears their batch

    # Visitor A lists documents: sees only doc_a
    list_a = client.get("/api/documents", headers=_headers(SESSION_A)).json()
    assert [d["document_id"] for d in list_a] == [doc_a_id]
    assert list_a[0]["vendor_name"] == "Vendor A Pty"

    # Visitor B lists documents: sees only doc_b
    list_b = client.get("/api/documents", headers=_headers(SESSION_B)).json()
    assert [d["document_id"] for d in list_b] == [doc_b_id]
    assert list_b[0]["vendor_name"] == "Vendor B Ltd"


def test_cross_session_read_rejected(client, fake_client, make_png):
    fake_client.responses = [GOOD_INVOICE]
    resp = _upload(client, [_png(make_png)], SESSION_A)
    doc_id = resp.json()["documents"][0]["document_id"]

    # Visitor B attempts to read Visitor A's document
    res_b = client.get(f"/api/documents/{doc_id}", headers=_headers(SESSION_B))
    assert res_b.status_code == 404
    assert res_b.json()["detail"] == "Document not found."


def test_cross_session_update_rejected(client, fake_client, make_png):
    fake_client.responses = [{**GOOD_INVOICE, "vendor_name": "Original Vendor"}]
    resp = _upload(client, [_png(make_png)], SESSION_A)
    doc_id = resp.json()["documents"][0]["document_id"]

    # Visitor B attempts to update Visitor A's document
    res_b = client.patch(
        f"/api/documents/{doc_id}",
        json={"vendor_name": "Malicious Tampering"},
        headers=_headers(SESSION_B),
    )
    assert res_b.status_code == 404

    # Verify Visitor A's document was not touched
    doc_a = client.get(f"/api/documents/{doc_id}", headers=_headers(SESSION_A)).json()
    assert doc_a["vendor_name"] == "Original Vendor"


def test_cross_session_delete_rejected(client, fake_client, make_png):
    fake_client.responses = [GOOD_INVOICE]
    resp = _upload(client, [_png(make_png)], SESSION_A)
    doc_id = resp.json()["documents"][0]["document_id"]

    # Visitor B attempts to delete Visitor A's document
    res_b = client.delete(f"/api/documents/{doc_id}", headers=_headers(SESSION_B))
    assert res_b.status_code == 404

    # Document still exists in Visitor A's session
    assert client.get(f"/api/documents/{doc_id}", headers=_headers(SESSION_A)).status_code == 200


def test_cross_session_clear_isolation(client, fake_client, make_png):
    fake_client.responses = [GOOD_INVOICE, GOOD_INVOICE]
    resp_a = _upload(client, [_png(make_png, "a.png")], SESSION_A)
    resp_b = _upload(client, [_png(make_png, "b.png")], SESSION_B)
    doc_a_id = resp_a.json()["documents"][0]["document_id"]
    doc_b_id = resp_b.json()["documents"][0]["document_id"]
    assert client.get(
        f"/api/documents/{doc_b_id}", headers=_headers(SESSION_B)
    ).status_code == 200

   

    # Visitor B clears their batch
    res_clear = client.delete("/api/documents", headers=_headers(SESSION_B))
    assert res_clear.status_code == 204

    # Visitor B has no documents
    assert client.get("/api/documents", headers=_headers(SESSION_B)).json() == []

    # Visitor A still has document A
    list_a = client.get("/api/documents", headers=_headers(SESSION_A)).json()
    assert [d["document_id"] for d in list_a] == [doc_a_id]


def test_cross_session_csv_export_isolation(client, fake_client, make_png):
    fake_client.responses = [
        {**GOOD_INVOICE, "vendor_name": "Unique Alpha Corp"},
        {**GOOD_INVOICE, "vendor_name": "Unique Beta LLC"},
    ]
    _upload(client, [_png(make_png, "alpha.png")], SESSION_A)
    _upload(client, [_png(make_png, "beta.png")], SESSION_B)

    # Visitor A exports via header
    resp_a = client.get("/api/export.csv", headers=_headers(SESSION_A))
    assert resp_a.status_code == 200
    rows_a = _read_csv(resp_a)
    assert len(rows_a) == 1
    assert rows_a[0]["vendor_name"] == "Unique Alpha Corp"

    # Visitor B exports via header
    resp_b = client.get("/api/export.csv", headers=_headers(SESSION_B))
    assert resp_b.status_code == 200
    rows_b = _read_csv(resp_b)
    assert len(rows_b) == 1
    assert rows_b[0]["vendor_name"] == "Unique Beta LLC"


def test_invalid_session_ids_rejected(client):
    invalid_cases = [
        "short",  # < 8 chars
        "a" * 129,  # > 128 chars
        "../../etc/passwd",  # path traversal
        "session with spaces",
        "sess@id!#$",  # invalid punctuation
    ]
    for bad_id in invalid_cases:
        res = client.get("/api/documents", headers={"X-Session-ID": bad_id})
        assert res.status_code == 400
        assert res.json()["detail"] == "Invalid session ID."


def test_hosted_mode_requires_session_id(client, override_settings):
    override_settings(require_session_id=True)

    # Missing session ID in hosted mode is rejected
    res = client.get("/api/documents")
    assert res.status_code == 400
    assert "X-Session-ID header is required" in res.json()["detail"]

    # Supplying a valid session ID succeeds
    res_valid = client.get("/api/documents", headers=_headers(SESSION_A))
    assert res_valid.status_code == 200
    assert res_valid.json() == []


def test_production_app_env_enforces_session_id(client, override_settings):
    override_settings(app_env="production")

    res = client.get("/api/documents")
    assert res.status_code == 400
    assert "X-Session-ID header is required" in res.json()["detail"]


def test_local_mode_allows_missing_session_id(client, fake_client, make_png):
    # In local development mode without headers, requests fall back to default session
    fake_client.responses = [GOOD_INVOICE]
    res_upload = client.post(
        "/api/documents",
        files=[("files", _png(make_png))],
    )
    assert res_upload.status_code == 200
    doc_id = res_upload.json()["documents"][0]["document_id"]

    res_list = client.get("/api/documents")
    assert res_list.status_code == 200
    assert [d["document_id"] for d in res_list.json()] == [doc_id]


def test_session_ttl_cleanup(client, fake_client, make_png, override_settings):
    override_settings(session_ttl_seconds=0.05)

    fake_client.responses = [GOOD_INVOICE]
    _upload(client, [_png(make_png)], SESSION_A)

    # Session A is active immediately
    assert len(client.get("/api/documents", headers=_headers(SESSION_A)).json()) == 1

    # Wait for TTL to expire
    time.sleep(0.08)

    # Session A should now be expired and cleared upon access
    assert client.get("/api/documents", headers=_headers(SESSION_A)).json() == []


def test_session_capacity_eviction_lru(client, fake_client, make_png, override_settings):
    override_settings(max_sessions=2, session_ttl_seconds=3600.0)

    fake_client.responses = [GOOD_INVOICE, GOOD_INVOICE, GOOD_INVOICE]
    sess1 = "session-first-111111"
    sess2 = "session-second-222222"
    sess3 = "session-third-3333333"

    _upload(client, [_png(make_png)], sess1)
    time.sleep(0.01)
    _upload(client, [_png(make_png)], sess2)
    time.sleep(0.01)

    # Now add a 3rd session, exceeding max_sessions=2
    _upload(client, [_png(make_png)], sess3)

    # The oldest (sess1) was evicted
    assert client.get("/api/documents", headers=_headers(sess1)).json() == []
    # sess2 and sess3 remain
    assert len(client.get("/api/documents", headers=_headers(sess2)).json()) == 1
    assert len(client.get("/api/documents", headers=_headers(sess3)).json()) == 1


def test_session_record_limit_enforced(client, fake_client, make_png, override_settings):
    override_settings(max_records_per_session=2)

    fake_client.responses = [GOOD_INVOICE, GOOD_INVOICE, GOOD_INVOICE]
    # Upload first 2 files (allowed)
    resp = _upload(
        client,
        [_png(make_png, "1.png"), _png(make_png, "2.png")],
        SESSION_A,
    )
    assert resp.status_code == 200
    assert len(resp.json()["documents"]) == 2
    assert resp.json()["errors"] == []

    # Upload 3rd file (exceeds max_records_per_session=2)
    resp3 = _upload(client, [_png(make_png, "3.png")], SESSION_A)
    assert resp3.status_code == 422
    assert len(resp3.json()["documents"]) == 0
    assert resp3.json()["errors"][0]["code"] == "session_limit_exceeded"
    assert "limit reached" in resp3.json()["errors"][0]["message"]

