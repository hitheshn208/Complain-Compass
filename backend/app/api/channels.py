"""Multi-channel ingestion (email, chat, social media, voice) and the customer directory.

CSV upload stays at ``POST /api/v1/complaints/upload``.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.api.complaints import run_batch_pipeline
from app.api.deps import envelope, get_container
from app.core.lifespan import Container
from app.ingestion.base import IngestionError, UploadedFile
from app.services.ingestion_service import DuplicateFileError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["channels"])


def run_recluster(container: Container) -> None:
    try:
        container.pipeline.recluster()
    except Exception:
        logger.exception("Background recluster after directory import failed")


@router.get("/ingest/channels")
def list_channels(c: Container = Depends(get_container)):
    out = [{"channel": "csv", "source": "CSV", "extensions": [".csv"], "multiple_files": False, "available": True,
            "endpoint": "/api/v1/complaints/upload",
            "description": "Tabular export with a complaint_text column (source column may name the channel)."}]
    for name, a in c.ingestion.channels.items():
        info = {"channel": name, "source": a.source.value, "extensions": list(a.extensions), "multiple_files": True,
                "available": True, "endpoint": f"/api/v1/ingest/{name}", "description": a.description}
        if name == "voice":
            info["extensions"] = info["extensions"] + [".csv"]
            info["transcriber"] = c.transcriber.describe()
            info["available"] = c.transcriber.available
        out.append(info)
    return envelope(out, directory=c.repo.directory_stats())


async def _read_files(files: list[UploadFile], c: Container) -> list[UploadedFile]:
    s = c.settings
    if len(files) > s.max_files_per_upload:
        raise HTTPException(status_code=413, detail=f"At most {s.max_files_per_upload} files per upload")
    per_file = int(s.max_upload_mb * 1024 * 1024)
    budget = int(s.max_batch_upload_mb * 1024 * 1024)
    out: list[UploadedFile] = []
    total = 0
    for f in files:
        data = await f.read(per_file + 1)
        if len(data) > per_file:
            raise HTTPException(status_code=413, detail=f"{f.filename} exceeds {s.max_upload_mb:g} MB")
        total += len(data)
        if total > budget:
            raise HTTPException(status_code=413, detail=f"Upload exceeds {s.max_batch_upload_mb:g} MB in total")
        out.append(UploadedFile(filename=(f.filename or "upload").replace("\\", "/").rsplit("/", 1)[-1], content=data))
    return out


@router.post("/ingest/{channel}", status_code=status.HTTP_201_CREATED)
async def ingest_channel(
    channel: str,
    background: BackgroundTasks,
    files: list[UploadFile] = File(..., description="One or more files for this channel"),
    auto_process: bool = Query(True),
    c: Container = Depends(get_container),
):
    if channel not in c.ingestion.channels:
        raise HTTPException(status_code=404, detail=f"Unknown channel '{channel}'. "
                                                    f"Available: {', '.join(c.ingestion.channels)} (CSV: /complaints/upload)")
    uploaded = await _read_files(files, c)
    try:
        # Parsing (and voice transcription) is CPU-bound: keep it off the event loop.
        result = await run_in_threadpool(c.ingestion.ingest_files, channel, uploaded)
    except DuplicateFileError as exc:
        return JSONResponse(status_code=409, content={"error": {
            "code": "duplicate_file", "message": "These file(s) were already imported as batch "
                                                 f"{exc.existing_batch['batch_id']}",
            "details": {"batch_id": exc.existing_batch["batch_id"]}}})
    except IngestionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    started = bool(auto_process and result["accepted_rows"] > 0)
    if started:
        background.add_task(run_batch_pipeline, c, result["batch_id"])
    result["processing_started"] = started
    return envelope(result)


@router.post("/customers/directory", status_code=status.HTTP_201_CREATED)
async def import_directory(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    auto_recluster: bool = Query(True, description="Re-run dedup/scoring if existing complaints were relinked"),
    c: Container = Depends(get_container),
):
    data = await file.read(int(c.settings.max_upload_mb * 1024 * 1024) + 1)
    if len(data) > c.settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large")
    try:
        result = c.ingestion.import_directory(data, file.filename or "")
    except IngestionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    started = bool(auto_recluster and result["complaints_relinked"] > 0)
    if started:
        background.add_task(run_recluster, c)
    result["recluster_started"] = started
    return envelope(result)


@router.get("/customers/directory")
def directory_stats(c: Container = Depends(get_container)):
    return envelope(c.repo.directory_stats())
