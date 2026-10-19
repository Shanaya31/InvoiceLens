"""Test doubles. Nothing here talks to Ollama or Tesseract."""
from __future__ import annotations

import json
from typing import Any

from app.services.gemma import ModelStatus

# Entirely synthetic. The GSTIN has a valid *format* but is not a real registration.
GOOD_INVOICE = {
    "document_type": "invoice",
    "vendor_name": "Sample Office Supplies",
    "vendor_gstin": "27AAPFU0939F1ZV",
    "invoice_number": "INV-1042",
    "invoice_date": "2025-06-15",
    "currency": "INR",
    "subtotal": "10000.00",
    "discount_amount": None,
    "cgst_amount": "900.00",
    "sgst_amount": "900.00",
    "igst_amount": "0.00",
    "other_tax_amount": None,
    "total_tax_amount": "1800.00",
    "total_amount": "11800.00",
    "expense_category": "office_supplies",
}


class FakeOCR:
    """Stand-in for Tesseract so tests never need it installed."""

    def __init__(self, text: str = "Sample Office Supplies\nInvoice INV-1042", error: Exception | None = None):
        self.text = text
        self.error = error
        self.calls = 0

    def __call__(self, images: list[bytes]) -> str:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.text


class FakeClient:
    """Scripted model client. Each ``extract`` call pops the next scripted response.

    A response may be a dict/str (returned) or an Exception (raised).
    """

    vision_method = "gemma_vision"
    ocr_method = "ocr_gemma"

    def __init__(self, responses: list[Any] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[tuple] = []  # (number_of_images, ocr_text)

    def extract(self, images: list[bytes], ocr_text: str | None = None) -> str:
        self.calls.append((len(images), ocr_text))
        if not self.responses:
            raise AssertionError("FakeClient received an unexpected extra call")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, str) else json.dumps(item)

    def check_model(self, timeout: float | None = None) -> ModelStatus:
        return ModelStatus("fake-model", True, True, True, "Fake model is ready.")
