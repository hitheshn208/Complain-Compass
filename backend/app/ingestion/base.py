"""Abstract ingestion interfaces.

Every input channel implements an adapter that emits ``StandardComplaint`` objects:

* ``IngestionAdapter``  - one tabular file (CSV).
* ``MultiFileAdapter``  - one or more files of a channel (email .eml/.mbox, chat transcripts,
  social media exports, voice recordings). Each email / conversation / post / call is one
  "item" (the equivalent of a CSV row).

Downstream inference never sees the raw input format, so adding a channel requires no change
to the inference engine.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from app.database.models import Source
from app.ingestion.errors import IngestionError
from app.ingestion.validator import merge_profiles, validate_row
from app.schemas.complaint import CustomerProfile, IngestionResult, ItemReport, RowError, StandardComplaint

__all__ = ["IngestionAdapter", "IngestionError", "MultiFileAdapter", "UploadedFile", "ResultBuilder"]


class IngestionAdapter(ABC):
    """Converts a raw payload into standardized complaints + row-level errors."""

    name: str = "base"

    @abstractmethod
    def parse(self, content: bytes, filename: str) -> IngestionResult:
        """Parse ``content``. Raise ``IngestionError`` for file-level problems; collect
        row-level problems in ``IngestionResult.errors`` and keep the valid rows."""


@dataclass(frozen=True)
class UploadedFile:
    filename: str
    content: bytes

    @property
    def ext(self) -> str:
        name = self.filename.lower()
        return name[name.rfind("."):] if "." in name else ""


class MultiFileAdapter(ABC):
    """Adapter for channels that arrive as one or more files."""

    name: str = "base"
    source: Source = Source.OTHER
    extensions: tuple[str, ...] = ()
    description: str = ""

    def check_extensions(self, files: list[UploadedFile], extra: tuple[str, ...] = ()) -> None:
        if not files:
            raise IngestionError("No files uploaded", status_code=400)
        allowed = self.extensions + extra
        bad = [f.filename for f in files if f.ext not in allowed]
        if bad:
            raise IngestionError(
                f"Unsupported file type for {self.name}: {', '.join(bad[:5])}. Allowed: {', '.join(allowed)}",
                status_code=400,
            )
        empty = [f.filename for f in files if not f.content]
        if empty:
            raise IngestionError(f"Empty file(s): {', '.join(empty[:5])}", status_code=400)

    @abstractmethod
    def parse_files(self, files: list[UploadedFile]) -> IngestionResult:
        """Parse all files. File-level problems raise ``IngestionError``; item-level problems
        are recorded as row errors (row_number = item number) and the valid items are kept."""


@dataclass
class ResultBuilder:
    """Accumulates items through the same row validator the CSV adapter uses, so every
    channel obeys identical text / timestamp / customer_id rules."""

    source: Source
    ingested_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc).replace(microsecond=0))
    complaints: list[StandardComplaint] = field(default_factory=list)
    customers: dict[str, CustomerProfile] = field(default_factory=dict)
    errors: list[RowError] = field(default_factory=list)
    items: list[ItemReport] = field(default_factory=list)
    count: int = 0

    def next_item(self) -> int:
        self.count += 1
        return self.count

    def reject(self, item: int, label: str, message: str, field_name: str | None = None) -> None:
        self.errors.append(RowError(row_number=item, field=field_name, message=f"[{label}] {message}"))
        self.items.append(ItemReport(item=item, label=label, accepted=False, customer_link="-", detail=message))

    def add(self, item: int, label: str, row: Mapping[str, Any], customer_link: str, detail: str | None = None) -> bool:
        """Validate a row dict (complaint_text, timestamp, customer_id, demographics...)."""
        record = dict(row)
        record["source"] = self.source.value
        complaint, customer, row_errors = validate_row(record, item, self.ingested_at)
        if row_errors:
            for e in row_errors:
                self.errors.append(RowError(row_number=item, field=e.field, message=f"[{label}] {e.message}"))
            self.items.append(ItemReport(item=item, label=label, accepted=False, customer_link="-",
                                         detail="; ".join(e.message for e in row_errors)))
            return False
        self.complaints.append(complaint)
        if customer is not None:
            existing = self.customers.get(customer.customer_id)
            self.customers[customer.customer_id] = merge_profiles(existing, customer) if existing else customer
        if complaint.timestamp_inferred:
            detail = f"{detail + '; ' if detail else ''}no timestamp, upload time used"
        self.items.append(ItemReport(item=item, label=label, accepted=True, customer_link=customer_link, detail=detail))
        return True

    def result(self) -> IngestionResult:
        return IngestionResult(total_rows=self.count, complaints=self.complaints,
                               customers=list(self.customers.values()), errors=self.errors, items=self.items)
