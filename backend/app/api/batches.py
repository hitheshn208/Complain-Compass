"""Batch status and pipeline control endpoints."""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from app.api.complaints import run_batch_pipeline
from app.api.deps import envelope, get_container
from app.core.lifespan import Container

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["batches", "pipeline"])


@router.get("/batches")
def list_batches(limit: int = Query(50, ge=1, le=200), c: Container = Depends(get_container)):
    return envelope(c.repo.list_batches(limit))


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str, c: Container = Depends(get_container)):
    batch = c.repo.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    batch["row_errors"] = c.repo.list_row_errors(batch_id)
    batch["pipeline"] = c.pipeline.state.as_dict()
    return envelope(batch)


@router.post("/pipeline/process/{batch_id}", status_code=status.HTTP_202_ACCEPTED)
def process_batch(batch_id: str, background: BackgroundTasks, c: Container = Depends(get_container)):
    batch = c.repo.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    if batch["status"] == "PROCESSING" and c.pipeline.state.running:
        raise HTTPException(status_code=409, detail="Batch is already being processed")
    background.add_task(run_batch_pipeline, c, batch_id)
    return envelope({"batch_id": batch_id, "queued": True})


def _run_recluster(c: Container) -> None:
    try:
        c.pipeline.recluster()
    except Exception:
        logger.exception("Background recluster failed")


@router.post("/pipeline/recluster", status_code=status.HTTP_202_ACCEPTED)
def recluster(background: BackgroundTasks, c: Container = Depends(get_container)):
    if c.pipeline.state.running:
        raise HTTPException(status_code=409, detail="Pipeline is already running")
    background.add_task(_run_recluster, c)
    return envelope({"queued": True})


@router.get("/pipeline/status")
def pipeline_status(c: Container = Depends(get_container)):
    return envelope(c.pipeline.state.as_dict(), counts=c.repo.counts(), llm_available=c.classifier.available)
