"""Ingestion use-case: parse an upload with an adapter and persist it as a batch."""
from __future__ import annotations

import uuid
from typing import Any

from app.database.repository import Repository
from app.ingestion.base import IngestionAdapter, IngestionError
from app.ingestion.csv_ingestion import sha256_bytes


class DuplicateFileError(IngestionError):
    def __init__(self, existing_batch: dict[str, Any]):
        super().__init__(f"This file was already imported as batch {existing_batch['batch_id']}", status_code=409)
        self.existing_batch = existing_batch


class IngestionService:
    def __init__(self, repo: Repository, adapter: IngestionAdapter):
        self.repo = repo
        self.adapter = adapter

    def ingest(self, content: bytes, filename: str) -> dict[str, Any]:
        """Validate and persist. Valid rows are stored even if other rows are rejected.
        Raises DuplicateFileError (409) for a previously imported identical file."""
        file_hash = sha256_bytes(content)
        existing = self.repo.find_batch_by_hash(file_hash)
        if existing is not None:
            raise DuplicateFileError(existing)
        result = self.adapter.parse(content, filename)
        batch_id = str(uuid.uuid4())
        self.repo.create_batch_with_rows(
            batch_id=batch_id,
            filename=filename,
            file_hash=file_hash,
            total_rows=result.total_rows,
            complaints=result.complaints,
            customers=result.customers,
            errors=result.errors,
        )
        return {
            "batch_id": batch_id,
            "file_hash": file_hash,
            "total_rows": result.total_rows,
            "accepted_rows": len(result.complaints),
            "rejected_rows": len(result.rejected_row_numbers),
            "row_errors": [e.model_dump() for e in result.errors[:500]],
            "timestamps_inferred": sum(c.timestamp_inferred for c in result.complaints),
        }
