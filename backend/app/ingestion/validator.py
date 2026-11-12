"""Column- and row-level validation for tabular complaint input."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

from app.ingestion.normalizer import (
    clean_optional_str,
    normalize_category_value,
    normalize_customer_id,
    normalize_source,
    normalize_student_status,
    normalize_timestamp,
)
from app.schemas.complaint import CustomerProfile, RowError, StandardComplaint

REQUIRED_COLUMNS = {"complaint_text"}
OPTIONAL_COLUMNS = {
    "customer_id",
    "source",
    "timestamp",
    "country",
    "state",
    "age_group",
    "student_status",
    "income_bracket",
}
DEMOGRAPHIC_COLUMNS = ["country", "state", "age_group", "student_status", "income_bracket"]
MAX_TEXT_LENGTH = 10_000
MIN_TEXT_LENGTH = 3


def missing_required_columns(columns: list[str]) -> set[str]:
    return REQUIRED_COLUMNS - set(columns)


def validate_row(
    row: Mapping[str, Any], row_number: int, ingested_at: datetime | None = None
) -> tuple[StandardComplaint | None, CustomerProfile | None, list[RowError]]:
    """Validate one input row. Returns (complaint, customer, errors). On any error the
    complaint is ``None`` and the row is rejected as a whole."""
    errors: list[RowError] = []
    ingested_at = ingested_at or datetime.now(timezone.utc)

    text_raw = row.get("complaint_text")
    text = None if text_raw is None else str(text_raw)
    stripped = clean_optional_str(text)
    if stripped is None:
        errors.append(RowError(row_number=row_number, field="complaint_text", message="complaint_text is empty"))
    elif len(stripped) < MIN_TEXT_LENGTH:
        errors.append(RowError(row_number=row_number, field="complaint_text", message="complaint_text is too short"))
    elif len(text) > MAX_TEXT_LENGTH:
        errors.append(
            RowError(row_number=row_number, field="complaint_text", message=f"complaint_text exceeds {MAX_TEXT_LENGTH} characters")
        )

    try:
        source = normalize_source(row.get("source"))
    except ValueError as exc:
        errors.append(RowError(row_number=row_number, field="source", message=str(exc)))
        source = None

    timestamp_inferred = False
    try:
        ts = normalize_timestamp(row.get("timestamp"))
        if ts is None:
            ts, timestamp_inferred = ingested_at, True
    except ValueError as exc:
        errors.append(RowError(row_number=row_number, field="timestamp", message=str(exc)))
        ts = None

    customer_id = normalize_customer_id(row.get("customer_id"))
    if customer_id is not None and len(customer_id) > 128:
        errors.append(RowError(row_number=row_number, field="customer_id", message="customer_id too long"))

    if errors:
        return None, None, errors

    customer = None
    if customer_id is not None:
        customer = CustomerProfile(
            customer_id=customer_id,
            country=normalize_category_value(row.get("country")),
            state=normalize_category_value(row.get("state")),
            age_group=normalize_category_value(row.get("age_group")),
            student_status=normalize_student_status(row.get("student_status")),
            income_bracket=normalize_category_value(row.get("income_bracket")),
        )

    complaint = StandardComplaint(
        complaint_id=str(uuid.uuid4()),
        customer_id=customer_id,
        source=source,
        original_text=text,  # preserved exactly as received
        timestamp=ts,
        timestamp_inferred=timestamp_inferred,
        source_row=row_number,
    )
    return complaint, customer, []


def merge_profiles(a: CustomerProfile, b: CustomerProfile) -> CustomerProfile:
    """Merge two profiles for the same id; later non-null values win, nulls never erase."""
    data = a.model_dump()
    for key, value in b.model_dump().items():
        if value is not None:
            data[key] = value
    return CustomerProfile(**data)
