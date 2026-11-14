"""Priority settings: let a company tune the ranking formula from the UI."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import envelope, get_container
from app.core.lifespan import Container
from app.services.priority_settings import PrioritySettingsError

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


class PriorityUpdate(BaseModel):
    weights: dict[str, float] | None = None
    category_weights: dict[str, float] | None = None


def _rescore(c: Container) -> int:
    if c.pipeline.state.running:
        raise HTTPException(status_code=409, detail="Processing is running; try again when it has finished")
    return c.pipeline.rescore()["scored_issues"]


@router.get("/priority")
def get_priority(c: Container = Depends(get_container)):
    return envelope(c.priority.describe())


@router.put("/priority")
def update_priority(body: PriorityUpdate, c: Container = Depends(get_container)):
    """Save new weights / category values and immediately re-score every issue."""
    if c.pipeline.state.running:
        raise HTTPException(status_code=409, detail="Processing is running; try again when it has finished")
    try:
        c.priority.update(body.weights, body.category_weights)
    except PrioritySettingsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return envelope(c.priority.describe(), rescored_issues=_rescore(c))


@router.delete("/priority")
def reset_priority(c: Container = Depends(get_container)):
    """Back to the configured defaults."""
    if c.pipeline.state.running:
        raise HTTPException(status_code=409, detail="Processing is running; try again when it has finished")
    c.priority.reset()
    return envelope(c.priority.describe(), rescored_issues=_rescore(c))
