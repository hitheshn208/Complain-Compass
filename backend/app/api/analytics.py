"""Analytics endpoints. All numbers are computed deterministically from stored data."""
from __future__ import annotations

import io

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import ValidationError

from app.analytics.segmentation import DIMENSIONS
from app.api.deps import envelope, get_container, get_filters
from app.core.lifespan import Container
from app.database.repository import ComplaintFilters
from app.schemas.analytics import PopulationRow

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


@router.get("/overview")
def overview(filters: ComplaintFilters = Depends(get_filters), c: Container = Depends(get_container)):
    return envelope(c.analytics.overview(filters))


@router.get("/trends")
def trends(
    granularity: str = Query("D", pattern="^(D|W)$"),
    filters: ComplaintFilters = Depends(get_filters),
    c: Container = Depends(get_container),
):
    return envelope(c.analytics.trends(filters, granularity))


@router.get("/segments")
def segments(
    dimension: str | None = Query(None, description=f"One of {', '.join(DIMENSIONS)}; omit for all"),
    filters: ComplaintFilters = Depends(get_filters),
    c: Container = Depends(get_container),
):
    if dimension and dimension not in DIMENSIONS:
        raise HTTPException(status_code=422, detail=f"dimension must be one of {DIMENSIONS}")
    return envelope(c.analytics.segments(filters, dimension))


@router.get("/filters")
def filter_options(c: Container = Depends(get_container)):
    return envelope(c.repo.filter_options())


@router.post("/populations")
async def upload_populations(file: UploadFile = File(...), c: Container = Depends(get_container)):
    """Optional segment population denominators (CSV: dimension,value,population). Enables complaint rates."""
    content = await file.read(2 * 1024 * 1024 + 1)
    if len(content) > 2 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Population file too large")
    try:
        df = pd.read_csv(io.BytesIO(content), dtype=str)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse CSV: {exc}") from exc
    df.columns = [col.strip().lower() for col in df.columns]
    if not {"dimension", "value", "population"} <= set(df.columns):
        raise HTTPException(status_code=422, detail="Columns required: dimension,value,population")
    rows, errors = [], []
    for i, r in enumerate(df.to_dict(orient="records"), start=1):
        try:
            pop = int(float(r["population"]))
            row = PopulationRow(dimension=str(r["dimension"]).strip(), value=str(r["value"]).strip(), population=pop)
        except (TypeError, ValueError, ValidationError) as exc:
            errors.append({"row_number": i, "message": str(exc).splitlines()[0]})
            continue
        rows.append((row.dimension, row.value, row.population))
    c.repo.replace_populations(rows)
    return envelope({"accepted": len(rows), "errors": errors})
