"""Company-adjustable priority settings: the 5 formula weights and the business value of each category.

Defaults come from configuration (WEIGHT_* env vars) and ``BUSINESS_CATEGORY_WEIGHTS``. An analyst can
override them from the UI; overrides are stored in the ``app_settings`` table and every score is
recomputed with them. Resetting deletes the overrides, so the defaults apply again.
"""
from __future__ import annotations

from typing import Any

from app.analytics.prioritization import BUSINESS_CATEGORY_WEIGHTS
from app.core.config import Settings
from app.database.models import CATEGORIES
from app.database.repository import Repository

WEIGHT_KEYS = ("impact", "severity", "recurrence", "growth", "business")
_WEIGHTS = "priority_weights"
_CATEGORIES = "category_weights"


class PrioritySettingsError(ValueError):
    pass


class PrioritySettings:
    def __init__(self, repo: Repository, settings: Settings):
        self.repo = repo
        self.settings = settings

    # ------------------------------------------------------------------ read
    def default_weights(self) -> dict[str, float]:
        return dict(self.settings.weights)

    def default_category_weights(self) -> dict[str, float]:
        return {c: float(BUSINESS_CATEGORY_WEIGHTS.get(c, BUSINESS_CATEGORY_WEIGHTS["Other"])) for c in CATEGORIES}

    def weights(self) -> dict[str, float]:
        return self.repo.get_setting(_WEIGHTS) or self.default_weights()

    def category_weights(self) -> dict[str, float]:
        return {**self.default_category_weights(), **(self.repo.get_setting(_CATEGORIES) or {})}

    def describe(self) -> dict[str, Any]:
        return {
            "weights": self.weights(),
            "category_weights": self.category_weights(),
            "defaults": {"weights": self.default_weights(), "category_weights": self.default_category_weights()},
            "customized": bool(self.repo.get_setting(_WEIGHTS) or self.repo.get_setting(_CATEGORIES)),
        }

    # ----------------------------------------------------------------- write
    def update(self, weights: dict[str, float] | None, category_weights: dict[str, float] | None) -> None:
        """Validate and store overrides. Weights must be 0-1 each and sum to 1; category values 0-100."""
        if weights is not None:
            if set(weights) != set(WEIGHT_KEYS):
                raise PrioritySettingsError(f"weights must have exactly these keys: {', '.join(WEIGHT_KEYS)}")
            if any(not 0 <= float(v) <= 1 for v in weights.values()):
                raise PrioritySettingsError("each weight must be between 0 and 1")
            total = sum(float(v) for v in weights.values())
            if abs(total - 1.0) > 1e-3:
                raise PrioritySettingsError(f"weights must add up to 1 (100%), got {total:.3f}")
            # store exactly normalised values (removes rounding noise from the sliders)
            self.repo.set_setting(_WEIGHTS, {k: round(float(weights[k]) / total, 4) for k in WEIGHT_KEYS})
        if category_weights is not None:
            unknown = set(category_weights) - set(CATEGORIES)
            if unknown:
                raise PrioritySettingsError(f"unknown categories: {', '.join(sorted(unknown))}")
            if any(not 0 <= float(v) <= 100 for v in category_weights.values()):
                raise PrioritySettingsError("each category value must be between 0 and 100")
            self.repo.set_setting(_CATEGORIES, {k: float(v) for k, v in category_weights.items()})

    def reset(self) -> None:
        self.repo.delete_setting(_WEIGHTS)
        self.repo.delete_setting(_CATEGORIES)
