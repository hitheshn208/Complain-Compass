"""CSV ingestion adapter."""
from __future__ import annotations

import hashlib
import io
from datetime import datetime, timezone

import pandas as pd

from app.ingestion.base import IngestionAdapter, IngestionError
from app.ingestion.validator import merge_profiles, missing_required_columns, validate_row
from app.schemas.complaint import CustomerProfile, IngestionResult, RowError, StandardComplaint

ALLOWED_EXTENSIONS = (".csv",)


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class CSVIngestionAdapter(IngestionAdapter):
    name = "csv"

    def __init__(self, max_bytes: int):
        self.max_bytes = max_bytes

    def parse(self, content: bytes, filename: str) -> IngestionResult:
        if not filename or not filename.lower().endswith(ALLOWED_EXTENSIONS):
            raise IngestionError("Only .csv files are accepted", status_code=400)
        if len(content) == 0:
            raise IngestionError("Uploaded file is empty", status_code=400)
        if len(content) > self.max_bytes:
            raise IngestionError(
                f"File exceeds maximum size of {self.max_bytes // (1024 * 1024)} MB", status_code=413
            )

        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = content.decode("latin-1")
            except UnicodeDecodeError as exc:  # pragma: no cover - latin-1 decodes anything
                raise IngestionError("File is not valid text") from exc

        try:
            df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, skipinitialspace=True)
        except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
            raise IngestionError(f"Could not parse CSV: {exc}") from exc

        df.columns = [str(c).strip().lower() for c in df.columns]
        missing = missing_required_columns(list(df.columns))
        if missing:
            raise IngestionError(f"Missing required column(s): {', '.join(sorted(missing))}", status_code=422)
        if len(df) == 0:
            raise IngestionError("CSV contains a header but no data rows", status_code=422)

        ingested_at = datetime.now(timezone.utc).replace(microsecond=0)
        complaints: list[StandardComplaint] = []
        customers: dict[str, CustomerProfile] = {}
        errors: list[RowError] = []

        for idx, record in enumerate(df.to_dict(orient="records"), start=1):
            complaint, customer, row_errors = validate_row(record, idx, ingested_at)
            if row_errors:
                errors.extend(row_errors)
                continue
            complaints.append(complaint)
            if customer is not None:
                existing = customers.get(customer.customer_id)
                customers[customer.customer_id] = merge_profiles(existing, customer) if existing else customer

        return IngestionResult(
            total_rows=len(df), complaints=complaints, customers=list(customers.values()), errors=errors
        )
