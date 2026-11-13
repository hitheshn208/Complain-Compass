"""Ingestion use-cases: parse uploads with a channel adapter and persist them as a batch."""
from __future__ import annotations

import hashlib
import uuid
from typing import Any

from app.database.models import BatchStatus
from app.database.repository import Repository
from app.ingestion.base import IngestionAdapter, IngestionError, MultiFileAdapter, UploadedFile
from app.ingestion.csv_ingestion import sha256_bytes
from app.ingestion.directory import parse_directory
from app.ingestion.identity import ContactResolver
from app.schemas.complaint import IngestionResult


class DuplicateFileError(IngestionError):
    def __init__(self, existing_batch: dict[str, Any]):
        super().__init__(f"This file was already imported as batch {existing_batch['batch_id']}", status_code=409)
        self.existing_batch = existing_batch


def files_fingerprint(channel: str, files: list[UploadedFile]) -> str:
    """Order-independent hash of a multi-file upload (same set of files => same fingerprint)."""
    digests = sorted(sha256_bytes(f.content) for f in files)
    return hashlib.sha256(f"{channel}:{','.join(digests)}".encode()).hexdigest()


class IngestionService:
    def __init__(
        self,
        repo: Repository,
        adapter: IngestionAdapter,
        channels: dict[str, MultiFileAdapter] | None = None,
        resolver: ContactResolver | None = None,
    ):
        self.repo = repo
        self.adapter = adapter
        self.channels = channels or {}
        self.resolver = resolver

    # ---------------------------------------------------------------- CSV (single file)
    def ingest(self, content: bytes, filename: str) -> dict[str, Any]:
        """Validate and persist. Valid rows are stored even if other rows are rejected.
        Raises DuplicateFileError (409) for a previously imported identical file."""
        file_hash = sha256_bytes(content)
        self._check_duplicate(file_hash)
        result = self.adapter.parse(content, filename)
        return self._persist(result, filename, file_hash, "CSV")

    # ---------------------------------------------------------------- other channels
    def ingest_files(self, channel: str, files: list[UploadedFile]) -> dict[str, Any]:
        adapter = self.channels.get(channel)
        if adapter is None:
            raise IngestionError(f"Unknown channel '{channel}'. Available: {', '.join(self.channels)}", 404)
        if not files:
            raise IngestionError("No files uploaded")
        fingerprint = files_fingerprint(channel, files)
        self._check_duplicate(fingerprint)
        result = adapter.parse_files(files)
        if result.total_rows == 0:
            raise IngestionError("No complaints found in the uploaded file(s)", 422)
        label = files[0].filename if len(files) == 1 else f"{files[0].filename} + {len(files) - 1} more"
        return self._persist(result, label, fingerprint, adapter.source.value)

    def _check_duplicate(self, file_hash: str) -> None:
        """409 for a file that was already imported. A previous attempt where every row was rejected
        imported nothing, so it is replaced instead of blocking the re-upload."""
        existing = self.repo.find_batch_by_hash(file_hash)
        if existing is None:
            return
        if not existing["accepted_rows"]:
            self.repo.delete_empty_batch(existing["batch_id"])
            return
        raise DuplicateFileError(existing)

    def _persist(self, result: IngestionResult, filename: str, file_hash: str, channel: str) -> dict[str, Any]:
        batch_id = str(uuid.uuid4())
        self.repo.create_batch_with_rows(
            batch_id=batch_id,
            filename=filename,
            file_hash=file_hash,
            total_rows=result.total_rows,
            complaints=result.complaints,
            customers=result.customers,
            errors=result.errors,
            channel=channel,
        )
        if not result.complaints:
            # Nothing to process: close the batch now, otherwise it would stay UPLOADED forever.
            self.repo.update_batch(batch_id, status=BatchStatus.FAILED, progress=0,
                                   error_message=no_valid_rows_message(result))
        links: dict[str, int] = {}
        for it in result.items:
            if it.accepted:
                links[it.customer_link] = links.get(it.customer_link, 0) + 1
        return {
            "batch_id": batch_id,
            "channel": channel,
            "file_hash": file_hash,
            "total_rows": result.total_rows,
            "accepted_rows": len(result.complaints),
            "rejected_rows": len(result.rejected_row_numbers),
            "row_errors": [e.model_dump() for e in result.errors[:500]],
            "timestamps_inferred": sum(c.timestamp_inferred for c in result.complaints),
            "customer_links": links,
            "items": [i.model_dump() for i in result.items[:500]],
        }

    # ---------------------------------------------------------------- customer directory
    def import_directory(self, content: bytes, filename: str) -> dict[str, Any]:
        if self.resolver is None:
            raise IngestionError("Identity resolver not configured", 500)
        parsed = parse_directory(content, filename, self.resolver)
        self.repo.save_directory(parsed.customers, parsed.contacts)
        relinked = self.repo.relink_customers(parsed.pseudonym_mapping())
        return {
            "rows": parsed.rows,
            "customers": len(parsed.customers),
            "contacts_stored": len(parsed.contacts),
            "complaints_relinked": relinked,
            "row_errors": [e.model_dump() for e in parsed.errors[:500]],
            "stats": self.repo.directory_stats(),
        }


def no_valid_rows_message(result: IngestionResult) -> str:
    n = result.total_rows
    reasons: dict[str, int] = {}
    for e in result.errors:
        msg = e.message.split("] ", 1)[1] if e.message.startswith("[") and "] " in e.message else e.message
        reasons[msg] = reasons.get(msg, 0) + 1
    top = max(reasons, key=reasons.get) if reasons else "unknown"
    return (f"No valid complaints: all {n} item(s) were rejected. Most common problem: {top}. "
            "Check that the file was uploaded on the right tab.")
