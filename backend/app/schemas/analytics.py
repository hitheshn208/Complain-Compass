"""Analytics schema helpers."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Dimension = Literal["country", "state", "age_group", "student_status", "income_bracket"]


class PopulationRow(BaseModel):
    """Segment population denominator used to turn counts into rates."""

    dimension: Dimension
    value: str = Field(min_length=1)
    population: int = Field(gt=0)
