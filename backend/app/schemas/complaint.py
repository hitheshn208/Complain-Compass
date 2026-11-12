"""Standardized complaint objects. The inference engine only ever sees these."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.database.models import Source


class CustomerProfile(BaseModel):
    """Demographics supplied explicitly in the input. Never inferred from text."""

    customer_id: str
    country: str | None = None
    state: str | None = None
    age_group: str | None = None
    student_status: str | None = None
    income_bracket: str | None = None


class StandardComplaint(BaseModel):
    """Source-agnostic complaint produced by any ingestion adapter."""

    complaint_id: str
    customer_id: str | None = None
    source: Source = Source.CSV
    original_text: str
    timestamp: datetime
    timestamp_inferred: bool = False
    source_row: int | None = None


class RowError(BaseModel):
    row_number: int = Field(description="1-based data row number (header excluded)")
    field: str | None = None
    message: str


class ItemReport(BaseModel):
    """Per-item outcome for multi-file channels (one email, conversation, post or call)."""

    item: int
    label: str = Field(description="file name or message/conversation/post identifier")
    accepted: bool
    customer_link: str = Field(description="explicit | directory | pseudonymous | anonymous | -")
    detail: str | None = None


class IngestionResult(BaseModel):
    total_rows: int
    complaints: list[StandardComplaint]
    customers: list[CustomerProfile]
    errors: list[RowError]
    items: list[ItemReport] = Field(default_factory=list)

    @property
    def rejected_row_numbers(self) -> set[int]:
        return {e.row_number for e in self.errors}


class ComplaintOut(BaseModel):
    complaint_id: str
    customer_id: str | None
    source: str
    original_text: str
    normalized_text: str | None
    timestamp: str
    batch_id: str
    processing_status: str
    created_at: str
    category: str | None = None
    severity: str | None = None
    summary: str | None = None
    issue_id: str | None = None
    issue_title: str | None = None
    incident_id: str | None = None
    link_type: str | None = None


class SimilarComplaint(BaseModel):
    complaint_id: str
    similarity: float
    text: str
    customer_id: str | None
    source: str | None
    timestamp: str | None
