"""Upload hardening: size, signatures, real parsing, filenames, logging."""
from __future__ import annotations

import logging
import struct
import zlib

import pymupdf
import pytest
from app.services.uploads import UploadError, safe_filename, validate_upload
from fakes import GOOD_INVOICE

LIMITS = {"max_bytes": 10 * 1024 * 1024, "max_pages": 5}


def reject(filename: str, data: bytes, **overrides) -> str:
    with pytest.raises(UploadError) as info:
        validate_upload(filename, data, **{**LIMITS, **overrides})
    return info.value.code


def upload(client, name, data, mime="application/octet-stream"):
    return client.post("/api/documents", files=[("files", (name, data, mime))])


# ---- validate_upload ---------------------------------------------------------------------------------------
def test_valid_files_pass(make_png, make_jpeg, make_pdf):
    assert validate_upload("a.png", make_png(), **LIMITS).kind == "png"
    assert validate_upload("a.JPG", make_jpeg(), **LIMITS).kind == "jpeg"
    assert validate_upload("a.jpeg", make_jpeg(), **LIMITS).kind == "jpeg"
    pdf = validate_upload("a.pdf", make_pdf(5), **LIMITS)
    assert (pdf.kind, pdf.page_count) == ("pdf", 5)


def test_empty_file():
    assert reject("a.png", b"") == "empty_file"


def test_oversized_file_is_rejected_before_anything_else():
    assert reject("a.png", b"\x89PNG\r\n\x1a\n" + b"0" * 5000, max_bytes=1000) == "file_too_large"


@pytest.mark.parametrize("name", ["a.gif", "a.txt", "a.exe", "a.svg", "noextension", "a.png.exe"])
def test_unsupported_extensions(name, make_png):
    assert reject(name, make_png()) == "unsupported_type"


@pytest.mark.parametrize(
    "data",
    [b"hello world", b"MZ\x90\x00\x03\x00\x00\x00", b"<html><script>alert(1)</script></html>", b"GIF89a....."],
)
def test_right_extension_wrong_content(data):
    assert reject("invoice.png", data) == "unsupported_type"


def test_extension_must_match_content(make_png, make_pdf):
    assert reject("invoice.pdf", make_png()) == "type_mismatch"
    assert reject("invoice.png", make_pdf()) == "type_mismatch"


def test_corrupt_png_with_valid_signature():
    assert reject("a.png", b"\x89PNG\r\n\x1a\n" + b"garbage" * 20) == "invalid_image"


def test_truncated_jpeg():
    assert reject("a.jpg", b"\xff\xd8\xff\xe0" + b"\x00" * 10) == "invalid_image"


def test_decompression_bomb_is_rejected():
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))

    # A ~70 byte PNG header that *claims* to be 30000 x 30000 pixels.
    ihdr = struct.pack(">IIBBBBB", 30000, 30000, 8, 2, 0, 0, 0)
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")
    assert reject("bomb.png", data) == "invalid_image"


def test_corrupt_pdf():
    assert reject("a.pdf", b"%PDF-1.4\nthis is not a pdf") == "invalid_pdf"


def test_password_protected_pdf(make_pdf):
    encrypted = make_pdf(1, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")
    assert reject("a.pdf", encrypted) == "invalid_pdf"


def test_too_many_pages(make_pdf):
    assert reject("a.pdf", make_pdf(6)) == "too_many_pages"


def test_page_limit_is_configurable(make_pdf):
    assert reject("a.pdf", make_pdf(3), max_pages=2) == "too_many_pages"


# ---- filenames --------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd.png", "passwd.png"),
        ("..\\..\\windows\\evil.png", "evil.png"),
        ("/abs/path/inv.pdf", "inv.pdf"),
        ("in\x00voice\n.png", "invoice.png"),
        ("", "upload"),
        (None, "upload"),
        ("a" * 500 + ".png", ("a" * 500 + ".png")[:120]),
    ],
)
def test_safe_filename(raw, expected):
    assert safe_filename(raw) == expected


# ---- through the API ---------------------------------------------------------------------------------------
def test_api_rejects_oversized_upload(client, override_settings):
    override_settings(max_upload_mb=0.001)  # about 1 KB
    response = upload(client, "big.png", b"\x89PNG\r\n\x1a\n" + b"0" * 5000, "image/png")
    assert response.status_code == 422
    assert response.json()["errors"][0]["code"] == "file_too_large"


def test_api_rejects_spoofed_mime_type(client):
    response = upload(client, "evil.png", b"MZ\x90\x00 not an image", "image/png")
    assert response.json()["errors"][0]["code"] == "unsupported_type"


def test_api_rejects_pdf_over_page_limit(client, make_pdf):
    response = upload(client, "long.pdf", make_pdf(6), "application/pdf")
    assert response.status_code == 422
    assert response.json()["errors"][0]["code"] == "too_many_pages"


def test_api_limits_files_per_request(client, make_png):
    files = [("files", (f"{i}.png", make_png(), "image/png")) for i in range(21)]
    assert client.post("/api/documents", files=files).status_code == 400


def test_api_reports_sanitised_filename(client):
    response = upload(client, "../../secret/notes.txt", b"hello", "text/plain")
    assert response.json()["errors"][0]["filename"] == "notes.txt"


def test_nothing_sensitive_is_logged(client, fake_client, make_png, caplog):
    caplog.set_level(logging.DEBUG)
    secret_name = "acme-client-invoice-SECRET.png"
    fake_client.responses = [{**GOOD_INVOICE, "vendor_name": "Confidential Vendor Pty"}]
    upload(client, secret_name, make_png(), "image/png")  # success path
    upload(client, "SECRET-notes.txt", b"hello", "text/plain")  # rejection path
    fake_client.responses = []  # unexpected-error path
    upload(client, "SECRET-crash.png", make_png(), "image/png")
    logged = caplog.text
    assert "SECRET" not in logged
    assert "Confidential Vendor" not in logged
    assert "INV-1042" not in logged
