"""Issue request schemas."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.inference.classifier import CategoryLiteral


class IssuePatch(BaseModel):
    """Manual edits from analysts. Editing title/description/category marks the label as 'manual'
    so automatic relabeling never overwrites it."""

    title: str | None = Field(None, min_length=3, max_length=120)
    description: str | None = Field(None, max_length=2000)
    category: CategoryLiteral | None = None
    severity: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"] | None = None
    status: Literal["OPEN", "INVESTIGATING", "RESOLVED"] | None = None
