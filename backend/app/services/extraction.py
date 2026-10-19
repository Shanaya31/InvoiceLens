
"""Extraction pipeline: Gemma vision -> (if unusable) OCR text -> Gemma once more.

Rules enforced here:
- model and OCR output are untrusted: parsed defensively, validated field by field;
- values that cannot be read become ``None`` plus a review flag, never a guess;
- the OCR-assisted retry happens at most once.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from app.schemas.document import (
    EXTRACTED_FIELD_NAMES,
    KEY_FIELDS,
    DocumentRecord,
    ExtractedFields,
    ValidationResult,
)
from app.services.gemma import (
    GemmaError,
    ModelCapabilityError,
    ModelResponseError,
    ModelTimeoutError,
    ModelUnavailableError,
)
from app.services.ocr import OCRError, OCRUnavailableError, ocr_images
from app.services.validation import apply_validation, is_valid_gstin_format

# A result is "usable" (no OCR retry needed) when these are present.
REQUIRED_FOR_USABLE = ("vendor_name", "invoice_date", "total_amount")
_SCORE_FIELDS = (*KEY_FIELDS, "subtotal")

def _recover_vendor_gstin_from_ocr(
    ocr_text: str,
    vendor_name: str | None,
) -> str | None:
    """Recover a GSTIN only when OCR clearly associates it with the seller."""
    if not ocr_text.strip() or not vendor_name:
        return None

    gstin_pattern = re.compile(
        r"(?<![A-Z0-9])[0-9]{2}[A-Z]{5}[0-9]{4}"
        r"[A-Z][1-9A-Z]Z[0-9A-Z](?![A-Z0-9])",
        re.IGNORECASE,
    )

    lines = [line.strip() for line in ocr_text.splitlines() if line.strip()]
    vendor_lower = vendor_name.casefold()

    seller_candidates: set[str] = set()
    buyer_candidates: set[str] = set()
    ambiguous_candidates: set[str] = set()

    buyer_labels = ("buyer", "bill to", "ship to", "customer", "recipient")
    seller_labels = ("seller", "supplier", "vendor", "sold by")

    for index, line in enumerate(lines):
        for match in gstin_pattern.finditer(line):
            gstin = match.group(0).upper()
            if not is_valid_gstin_format(gstin):
                continue

            nearby = " ".join(lines[max(0, index - 1): index + 2])
            nearby_lower = nearby.casefold()

            is_buyer = any(label in nearby_lower for label in buyer_labels)
            is_seller = (
                vendor_lower in nearby_lower
                or any(label in nearby_lower for label in seller_labels)
            )

            if is_buyer and is_seller:
                ambiguous_candidates.add(gstin)
            elif is_buyer:
                buyer_candidates.add(gstin)
            elif is_seller:
                seller_candidates.add(gstin)
            else:
                ambiguous_candidates.add(gstin)

    # A GSTIN associated with both roles is ambiguous.
    if seller_candidates & buyer_candidates:
        return None

    # Never guess if another valid GSTIN cannot be confidently classified.
    if ambiguous_candidates:
        return None

    if len(seller_candidates) != 1:
        return None

    return next(iter(seller_candidates))


class MalformedModelResponse(ValueError):
    """The model's answer was not a JSON object."""


class ExtractionFailedError(Exception):
    """No usable result could be produced for this document."""

    code = "extraction_failed"


@dataclass
class _Attempt:
    fields: ExtractedFields
    flags: list[str]


@dataclass
class ExtractionResult:
    fields: ExtractedFields
    method: str  # gemma_vision | ocr_gemma | mock
    flags: list[str]
    ocr_attempted: bool


def parse_model_json(text: str) -> dict[str, Any]:
    """Parse the model's answer into a dict. Tolerates code fences and surrounding prose."""
    cleaned = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL | re.IGNORECASE)
    if fence:
        cleaned = fence.group(1)
    try:
        data = json.loads(cleaned, parse_float=Decimal)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise MalformedModelResponse("The model did not return JSON.") from None
        try:
            data = json.loads(cleaned[start : end + 1], parse_float=Decimal)
        except json.JSONDecodeError:
            raise MalformedModelResponse("The model returned malformed JSON.") from None
    if not isinstance(data, dict):
        raise MalformedModelResponse("The model did not return a JSON object.")
    return data


def sanitize_fields(raw: dict[str, Any]) -> tuple[ExtractedFields, list[str]]:
    """Validate each field independently so one bad value cannot sink the whole record."""
    values: dict[str, Any] = {}
    flags: list[str] = []
    for key in EXTRACTED_FIELD_NAMES:
        if key not in raw or raw[key] is None:
            continue  # absent stays None (or the schema default for type/currency)
        try:
            parsed = ExtractedFields.model_validate({key: raw[key]})
        except ValidationError:
            flags.append(f"{key}_ambiguous" if key == "invoice_date" else f"{key}_unreadable")
            continue
        values[key] = getattr(parsed, key)
    return ExtractedFields(**values), flags


def is_usable(fields: ExtractedFields) -> bool:
    if any(getattr(fields, name) is None for name in REQUIRED_FOR_USABLE):
        return False
    return not (
        fields.document_type == "invoice" and not fields.invoice_number
    )


def _score(fields: ExtractedFields) -> int:
    return sum(1 for name in _SCORE_FIELDS if getattr(fields, name) is not None)


def _attempt(client: Any, images: list[bytes], ocr_text: str | None) -> _Attempt:
    raw_text = client.extract(images, ocr_text)
    fields, flags = sanitize_fields(parse_model_json(raw_text))
    return _Attempt(fields, flags)



def _verify_vendor_gstin(
    client: Any,
    images: list[bytes],
    attempt: _Attempt,
) -> None:
    """Keep a GSTIN only when focused verification agrees and format is valid."""
    original = attempt.fields.vendor_gstin
    vendor_name = attempt.fields.vendor_name
    verifier = getattr(client, "verify_vendor_gstin", None)

    # Existing fake clients and mock mode remain unchanged unless explicitly opted in.
    if not callable(verifier) or not images or not original or not vendor_name:
        return

    try:
        data = parse_model_json(verifier(images, vendor_name))
        verified_fields, _ = sanitize_fields(
            {"vendor_gstin": data.get("vendor_gstin")}
        )
        verified = verified_fields.vendor_gstin
    except (
        GemmaError,
        MalformedModelResponse,
        ValueError,
        TypeError,
        AttributeError,
    ):
        attempt.flags.append("vendor_gstin_verification_failed")
        return

    if (
        not verified
        or verified != original
        or not is_valid_gstin_format(original)
        or not is_valid_gstin_format(verified)
    ):
        flag = (
            "vendor_gstin_verification_mismatch"
            if verified and verified != original
            else "vendor_gstin_verification_unreadable"
        )
        attempt.flags.append(flag)



def _result(attempt: _Attempt, method: str, extra_flags: list[str], ocr_attempted: bool) -> ExtractionResult:
    flags = [*attempt.flags, *extra_flags]
    if method == "mock":
        flags.append("mock_extraction")
    return ExtractionResult(attempt.fields, method, list(dict.fromkeys(flags)), ocr_attempted)


def extract_document(
    images: list[bytes],
    client: Any,
    *,
    ocr_fn: Callable[[list[bytes]], str] = ocr_images,
    allow_ocr_fallback: bool = True,
) -> ExtractionResult:
    """Run the extraction workflow for one prepared document.

    Raises ``ModelUnavailableError`` / ``ModelCapabilityError`` when the model cannot be used at
    all (retrying with OCR would not help), and ``ExtractionFailedError`` when nothing usable
    could be produced.
    """
    vision_method = getattr(client, "vision_method", "gemma_vision")
    ocr_method = getattr(client, "ocr_method", "ocr_gemma")
    process_flags: list[str] = []

    first: _Attempt | None = None
    try:
        first = _attempt(client, images, None)
    except (ModelUnavailableError, ModelCapabilityError):
        raise
    except ModelTimeoutError:
        process_flags.append("model_timeout")
    except (ModelResponseError, MalformedModelResponse):
        process_flags.append("model_response_invalid")

    if first is not None and is_usable(first.fields):
        if (
            vision_method != "mock"
            and allow_ocr_fallback
            and not first.fields.vendor_gstin
            and first.fields.vendor_name
        ):
            try:
                text = ocr_fn(images)
            except OCRError as exc:
                flag = (
                    "ocr_unavailable"
                    if isinstance(exc, OCRUnavailableError)
                    else "ocr_failed"
                )
                return _result(first, vision_method, [*process_flags, flag], True)

            if not text.strip():
                return _result(
                    first, vision_method, [*process_flags, "ocr_no_text"], True
                )

            recovered_gstin = _recover_vendor_gstin_from_ocr(
                text, first.fields.vendor_name
            )
            if recovered_gstin:
                first.fields = first.fields.model_copy(
                    update={"vendor_gstin": recovered_gstin}
                )
                process_flags.append("vendor_gstin_recovered_from_ocr")

            return _result(first, vision_method, process_flags, True)

        return _result(first, vision_method, process_flags, False)

    if first is not None and not allow_ocr_fallback:
        return _result(first, vision_method, process_flags, False)
    if first is None and not allow_ocr_fallback:
        raise ExtractionFailedError("The model did not return a usable answer for this document.")

    # ---- single OCR-assisted retry --------------------------------------------------------
    try:
        text = ocr_fn(images)
    except OCRError as exc:
        flag = "ocr_unavailable" if isinstance(exc, OCRUnavailableError) else "ocr_failed"
        if first is None:
            raise ExtractionFailedError(
                f"The model gave no usable answer and the OCR fallback failed: {exc}"
            ) from exc
        return _result(first, vision_method, [*process_flags, flag], True)

    if not text.strip():
        if first is None:
            raise ExtractionFailedError("The model gave no usable answer and OCR found no text.")
        return _result(first, vision_method, [*process_flags, "ocr_no_text"], True)

    second: _Attempt | None = None
    try:
        second = _attempt(client, [], text)
    except (ModelUnavailableError, ModelCapabilityError):
        raise
    except (ModelTimeoutError, ModelResponseError, MalformedModelResponse):
        process_flags.append("ocr_retry_failed")

    if second is None and first is None:
        raise ExtractionFailedError("Neither the image nor the OCR text produced a usable answer.")
    if first is None:
        return _result(second, ocr_method, process_flags, True)  # type: ignore[arg-type]
    if second is None:
        return _result(first, vision_method, process_flags, True)
    if _score(second.fields) >= _score(first.fields):
        return _result(second, ocr_method, process_flags, True)
    return _result(first, vision_method, process_flags, True)


def build_record(result: ExtractionResult, filename: str, tolerance: Decimal) -> DocumentRecord:
    """Create the stored record. IDs and validation come from the backend, never the model."""
    record = DocumentRecord(
        **result.fields.model_dump(),
        document_id=f"doc_{uuid.uuid4().hex[:12]}",
        filename=filename,
        extraction_method=result.method,
        extraction_flags=result.flags,
        validation=ValidationResult(schema_valid=True, arithmetic_valid=None, requires_human_review=True),
    )
    return apply_validation(record, tolerance)
