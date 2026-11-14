"""Issue endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import envelope, get_container, get_filters
from app.core.lifespan import Container
from app.database.repository import ComplaintFilters
from app.schemas.issue import IssuePatch
from app.services.analytics_service import _decode_issue

router = APIRouter(prefix="/api/v1/issues", tags=["issues"])


@router.get("")
def list_issues(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    include_stale: bool = False,
    filters: ComplaintFilters = Depends(get_filters),
    c: Container = Depends(get_container),
):
    rows = c.analytics.list_issues(filters, include_stale=include_stale)
    total = len(rows)
    start = (page - 1) * page_size
    return envelope(rows[start : start + page_size], page=page, page_size=page_size, total=total,
                    pages=(total + page_size - 1) // page_size)


@router.get("/top10")
def top10(filters: ComplaintFilters = Depends(get_filters), c: Container = Depends(get_container)):
    return envelope(c.analytics.top10(filters), weights=c.analytics.weights(), filtered=not filters.is_empty())


@router.get("/{issue_id}")
def get_issue(issue_id: str, filters: ComplaintFilters = Depends(get_filters), c: Container = Depends(get_container)):
    detail = c.analytics.issue_detail(issue_id, filters)
    if detail is None:
        raise HTTPException(status_code=404, detail="Issue not found")
    return envelope(detail, weights=c.analytics.weights())


@router.patch("/{issue_id}")
def patch_issue(issue_id: str, body: IssuePatch, c: Container = Depends(get_container)):
    if c.repo.get_issue(issue_id) is None:
        raise HTTPException(status_code=404, detail="Issue not found")
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=422, detail="No fields to update")
    c.repo.patch_issue(issue_id, fields)
    return envelope(_decode_issue(c.repo.get_issue(issue_id)))
