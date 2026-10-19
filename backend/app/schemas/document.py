
"""Schemas for extracted documents.

Design rules:
- Money is a decimal *string* (never a binary float), normalised to two places.
- Dates are ISO ``YYYY-MM-DD`` only. Anything else is rejected so that it can be
  flagged for review instead of guessed (``03/04/2026`` is ambiguous in general).
- ``None`` means "missing or unreadable". Nothing here ever invents a value.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DocumentType = Literal["invoice", "receipt", "unknown"]
ExtractionMethod = Literal["gemma_vision", "ocr_gemma", "mock"]

MONEY_FIELDS = (
    "subtotal",
    "discount_amount",
    "cgst_amount",
    "sgst_amount",
    "igst_amount",
    "other_tax_amount",
    "total_tax_amount",
    "total_amount",
)
TEXT_FIELDS = ("vendor_name", "vendor_gstin", "invoice_number", "expense_category")
# Fields shown with a per-field "needs review" marker in the UI.
KEY_FIELDS = (
    "vendor_name",
    "vendor_gstin",
    "invoice_number",
    "invoice_date",
    "total_tax_amount",
    "total_amount",
)
# Every field the model is asked to extract.
EXTRACTED_FIELD_NAMES = (
    "document_type",
    "vendor_name",
    "vendor_gstin",
    "invoice_number",
    "invoice_date",
    "currency",
    *MONEY_FIELDS,
    "expense_category",
)

_NULL_TOKENS = {"", "null", "none", "n/a", "na", "nil", "-", "--"}
_CURRENCY_NOISE = re.compile(r"(?i)(₹|rs\.?|inr)")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TWO_PLACES = Decimal("0.01")


def _clean_text(value: Any, max_len: int = 200) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("must be text")  # noqa: TRY004
    text = str(value).strip()
    if text.lower() in _NULL_TOKENS:
        return None
    if len(text) > max_len:
        raise ValueError(f"must be at most {max_len} characters")
    return text


def parse_money(value: Any) -> str | None:
    """Return a normalised decimal string such as ``'11800.00'`` or ``None``.

    Raises ``ValueError`` for values that cannot be read as an amount.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("must be an amount")  # noqa: TRY004
    try:
        if isinstance(value, Decimal):
            number = value
        elif isinstance(value, int):
            number = Decimal(value)
        elif isinstance(value, float):
            number = Decimal(str(value))  # go through the shortest repr, not binary bits
        elif isinstance(value, str):
            text = value.strip()
            if text.lower() in _NULL_TOKENS:
                return None
            text = _CURRENCY_NOISE.sub("", text).replace(",", "").replace(" ", "")
            if text == "":
                return None
            number = Decimal(text)
        else:
            raise ValueError("must be an amount")  # noqa: TRY004

        if not number.is_finite():
            raise ValueError("must be a finite amount")
        return str(number.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))
    except (InvalidOperation, ArithmeticError) as exc:
        raise ValueError("must be a valid decimal amount") from exc


def parse_iso_date(value: Any) -> str | None:
    """Return ``YYYY-MM-DD`` for a valid ISO date, ``None`` for empty input."""
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        raise ValueError("must be an ISO date (YYYY-MM-DD)")  # noqa: TRY004
    text = value.strip()
    if text.lower() in _NULL_TOKENS:
        return None
    if not _ISO_DATE.match(text):
        raise ValueError("must be an ISO date (YYYY-MM-DD)")
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError("must be a real calendar date") from exc


class _Normalizers(BaseModel):
    """Shared validators. ``check_fields=False`` lets subclasses declare the fields."""

    model_config = ConfigDict(extra="ignore")

    @field_validator(*MONEY_FIELDS, mode="before", check_fields=False)
    @classmethod
    def _money(cls, value: Any) -> str | None:
        return parse_money(value)

    @field_validator("invoice_date", mode="before", check_fields=False)
    @classmethod
    def _date(cls, value: Any) -> str | None:
        return parse_iso_date(value)

    @field_validator(
        "vendor_name",
        "invoice_number",
        "expense_category",
        mode="before",
        check_fields=False,
    )
    @classmethod
    def _text(cls, value: Any) -> str | None:
        return _clean_text(value)

    @field_validator("vendor_gstin", mode="before", check_fields=False)
    @classmethod
    def _gstin(cls, value: Any) -> str | None:
        text = _clean_text(value, max_len=30)
        return text.replace(" ", "").upper() if text else None

    @field_validator("document_type", mode="before", check_fields=False)
    @classmethod
    def _doc_type(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("currency", mode="before", check_fields=False)
    @classmethod
    def _currency(cls, value: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z]{3}", value.strip()):
            raise ValueError("must be a 3-letter currency code")
        return value.strip().upper()


class ExtractedFields(_Normalizers):
    """Business data pulled from a document. The backend never trusts the model for anything else."""

    document_type: DocumentType = "unknown"
    vendor_name: str | None = None
    vendor_gstin: str | None = None
    invoice_number: str | None = None
    invoice_date: str | None = None
    currency: str = "INR"
    subtotal: str | None = None
    discount_amount: str | None = None
    cgst_amount: str | None = None
    sgst_amount: str | None = None
    igst_amount: str | None = None
    other_tax_amount: str | None = None
    total_tax_amount: str | None = None
    total_amount: str | None = None
    expense_category: str | None = None


class ValidationResult(BaseModel):
    schema_valid: bool = True
    # None means "not enough data to check", which is different from False (a mismatch).
    arithmetic_valid: bool | None = None
    requires_human_review: bool = True


class DocumentRecord(ExtractedFields):
    document_id: str
    filename: str
    extraction_method: ExtractionMethod
    extraction_flags: list[str] = Field(default_factory=list)
    corrected_fields: list[str] = Field(default_factory=list)
    reviewed: bool = False
    field_review: dict[str, bool] = Field(default_factory=dict)
    review_flags: list[str] = Field(default_factory=list)
    validation: ValidationResult = Field(default_factory=ValidationResult)


class DocumentUpdate(_Normalizers):
    """Partial update. Only fields that were actually sent are applied (see ``exclude_unset``)."""

    document_type: DocumentType | None = None
    vendor_name: str | None = None
    vendor_gstin: str | None = None
    invoice_number: str | None = None
    invoice_date: str | None = None
    currency: str | None = None
    subtotal: str | None = None
    discount_amount: str | None = None
    cgst_amount: str | None = None
    sgst_amount: str | None = None
    igst_amount: str | None = None
    other_tax_amount: str | None = None
    total_tax_amount: str | None = None
    total_amount: str | None = None
    expense_category: str | None = None
    reviewed: bool | None = None

    @field_validator("document_type", "currency")
    @classmethod
    def _not_null(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("cannot be cleared; choose a value")
        return value


class UploadErrorItem(BaseModel):
    filename: str
    code: str
    message: str


class UploadResponse(BaseModel):
    documents: list[DocumentRecord] = Field(default_factory=list)
    errors: list[UploadErrorItem] = Field(default_factory=list)
