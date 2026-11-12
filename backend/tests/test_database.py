import sqlite3
from datetime import datetime, timezone

import pytest

from app.database.models import Source
from app.ingestion.base import IngestionError
from app.ingestion.csv_ingestion import CSVIngestionAdapter
from app.schemas.complaint import CustomerProfile, RowError, StandardComplaint
from app.services.ingestion_service import DuplicateFileError, IngestionService


def _complaint(cid="11111111-1111-1111-1111-111111111111", customer="C1", text="Original  TEXT"):
    return StandardComplaint(complaint_id=cid, customer_id=customer, source=Source.EMAIL, original_text=text,
                             timestamp=datetime(2026, 10, 1, tzinfo=timezone.utc))


def test_foreign_keys_enforced(repo):
    assert repo.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        with repo.db.transaction() as conn:
            conn.execute(
                "INSERT INTO complaints (complaint_id, source, original_text, timestamp, batch_id, created_at) "
                "VALUES ('x', 'CSV', 't', '2026-01-01', 'missing-batch', '2026-01-01')"
            )


def test_check_constraint_on_source(repo):
    repo.create_batch_with_rows("b1", "f.csv", "h1", 0, [], [], [])
    with pytest.raises(sqlite3.IntegrityError):
        with repo.db.transaction() as conn:
            conn.execute(
                "INSERT INTO complaints (complaint_id, source, original_text, timestamp, batch_id, created_at) "
                "VALUES ('x', 'FAX', 't', '2026-01-01', 'b1', '2026-01-01')"
            )


def test_batch_persistence_and_original_text(repo):
    errors = [RowError(row_number=2, field="complaint_text", message="empty")]
    repo.create_batch_with_rows("b1", "f.csv", "hash1", 2, [_complaint()], [CustomerProfile(customer_id="C1",
                                country="India")], errors)
    b = repo.get_batch("b1")
    assert (b["total_rows"], b["accepted_rows"], b["rejected_rows"], b["status"]) == (2, 1, 1, "UPLOADED")
    assert repo.list_row_errors("b1")[0]["message"] == "empty"
    row = repo.get_complaint_detail("11111111-1111-1111-1111-111111111111")
    assert row["original_text"] == "Original  TEXT"


def test_customer_upsert_never_overwrites_with_null(repo):
    repo.upsert_customers([CustomerProfile(customer_id="C1", country="India", age_group="18-24")])
    repo.upsert_customers([CustomerProfile(customer_id="C1", country=None, age_group="25-34", state="Delhi")])
    c = repo.get_customer("C1")
    assert c["country"] == "India"  # kept
    assert c["age_group"] == "25-34"  # updated with a real value
    assert c["state"] == "Delhi"  # filled in


def test_duplicate_file_import_rejected(repo):
    svc = IngestionService(repo, CSVIngestionAdapter(1024 * 1024))
    content = b"complaint_text,customer_id\nPayment failed again,C1\n"
    first = svc.ingest(content, "a.csv")
    with pytest.raises(DuplicateFileError) as e:
        svc.ingest(content, "renamed.csv")
    assert e.value.status_code == 409
    assert e.value.existing_batch["batch_id"] == first["batch_id"]
    # a different file is fine
    svc.ingest(content + b"Another complaint here,C2\n", "b.csv")


def test_file_level_error_creates_no_batch(repo):
    svc = IngestionService(repo, CSVIngestionAdapter(1024 * 1024))
    with pytest.raises(IngestionError):
        svc.ingest(b"wrong_column\nabc\n", "a.csv")
    assert repo.list_batches() == []
