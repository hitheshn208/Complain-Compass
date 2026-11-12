"""FastAPI dependencies and shared response helpers."""
from __future__ import annotations

from typing import Any

from fastapi import Query, Request

from app.core.lifespan import Container
from app.database.repository import ComplaintFilters


def get_container(request: Request) -> Container:
    return request.app.state.container


def _split(value: str | None) -> list[str] | None:
    if not value:
        return None
    items = [v.strip() for v in value.split(",") if v.strip()]
    return items or None


def get_filters(
    date_from: str | None = Query(None, description="ISO date (inclusive)"),
    date_to: str | None = Query(None, description="ISO date (inclusive)"),
    country: str | None = Query(None, description="Comma-separated values"),
    state: str | None = Query(None),
    age_group: str | None = Query(None),
    student_status: str | None = Query(None),
    income_bracket: str | None = Query(None),
    source: str | None = Query(None),
    severity: str | None = Query(None),
) -> ComplaintFilters:
    return ComplaintFilters(
        date_from=date_from or None,
        date_to=date_to or None,
        country=_split(country),
        state=_split(state),
        age_group=_split(age_group),
        student_status=_split(student_status),
        income_bracket=_split(income_bracket),
        source=[s.upper() for s in _split(source) or []] or None,
        severity=[s.upper() for s in _split(severity) or []] or None,
    )


def envelope(data: Any, **meta: Any) -> dict[str, Any]:
    """Consistent response shape: {"data": ..., "meta": {...}}."""
    return {"data": data, "meta": meta}
