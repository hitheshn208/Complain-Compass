"""Complaint upload and retrieval endpoints."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import JSONResponse

from app.api.deps import envelope, get_container, get_filters
from app.core.lifespan import Container
from app.database.repository import ComplaintFilters
from app.inference.similarity import ComplaintNotEmbeddedError
from app.ingestion.base import IngestionError
from app.services.ingestion_service import DuplicateFileError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/complaints", tags=["complaints"])


def run_batch_pipeline(container: Container, batch_id: str) -> None:
    """Background job wrapper. Failure is recorded on the batch by the orchestrator and logged here."""
    try:
        container.pipeline.process_batch(batch_id)
    except Exception:
        logger.exception("Background processing of batch %s failed (status set to FAILED)", batch_id)


@router.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_complaints(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    auto_process: bool = Query(True, description="Start the processing pipeline in the background"),
    c: Container = Depends(get_container),
):
    max_bytes = int(c.settings.max_upload_mb * 1024 * 1024)
    content = await file.read(max_bytes + 1)
    try:
        result = c.ingestion.ingest(content, file.filename or "")
    except DuplicateFileError as exc:
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "duplicate_file", "message": exc.message,
                               "details": {"batch_id": exc.existing_batch["batch_id"]}}},
        )
    except IngestionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    if auto_process and result["accepted_rows"] > 0:
        background.add_task(run_batch_pipeline, c, result["batch_id"])
    result["processing_started"] = bool(auto_process and result["accepted_rows"] > 0)
    return envelope(result)


@router.get("")
def list_complaints(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    issue_id: str | None = None,
    batch_id: str | None = None,
    search: str | None = Query(None, max_length=200),
    filters: ComplaintFilters = Depends(get_filters),
    c: Container = Depends(get_container),
):
    filters.issue_id, filters.batch_id, filters.search = issue_id, batch_id, search
    rows, total = c.repo.list_complaints(filters, page, page_size)
    return envelope(rows, page=page, page_size=page_size, total=total, pages=(total + page_size - 1) // page_size)


@router.get("/review/duplicates")
def duplicate_review_queue(limit: int = Query(100, ge=1, le=500), c: Container = Depends(get_container)):
    rows = c.repo.review_candidates(limit)
    for r in rows:
        r["evidence"] = json.loads(r["evidence"])
    return envelope(rows, total=len(rows))


@router.get("/{complaint_id}")
def get_complaint(complaint_id: str, c: Container = Depends(get_container)):
    row = c.repo.get_complaint_detail(complaint_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Complaint not found")
    for f in ("reference_ids", "entities", "dedup_evidence"):
        if isinstance(row.get(f), str):
            row[f] = json.loads(row[f])
    row["incident_reports"] = []
    if row.get("incident_id"):
        for r in c.repo.incident_complaints(row["incident_id"]):
            r["evidence"] = json.loads(r["evidence"])
            row["incident_reports"].append(r)
    return envelope(row)


@router.get("/{complaint_id}/similar")
def similar_complaints(
    complaint_id: str, top_k: int = Query(10, ge=1, le=50), c: Container = Depends(get_container)
):
    if c.repo.get_complaint_detail(complaint_id) is None:
        raise HTTPException(status_code=404, detail="Complaint not found")
    try:
        hits = c.similarity.find_similar_complaints(complaint_id, top_k)
    except ComplaintNotEmbeddedError as exc:
        raise HTTPException(status_code=409, detail="Complaint has not been embedded yet") from exc
    # SQLite is the source of truth for identity (customers can be relinked after embedding).
    current = c.repo.get_complaints([h["complaint_id"] for h in hits])
    for h in hits:
        if h["complaint_id"] in current:
            h["customer_id"] = current[h["complaint_id"]]["customer_id"]
    return envelope(hits, top_k=top_k, note="Semantic similarity only; does not imply the same customer or incident.")
