"""Severity scoring helpers (deterministic)."""
from __future__ import annotations

from typing import Iterable

import numpy as np

# Documented mapping of severity labels to a 0-100 scale.
SEVERITY_POINTS = {"CRITICAL": 100.0, "HIGH": 75.0, "MEDIUM": 45.0, "LOW": 15.0}
SEVERITY_LEVELS = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]


def severity_score(severities: Iterable[str | None]) -> float:
    """S in [0,100] = 0.6 * mean(points) + 0.4 * p90(points).

    The mean reflects typical severity; the 90th percentile lets a meaningful tail of
    severe reports raise the score without one outlier dominating (unlike max).
    Unclassified complaints are ignored; returns 0 if none are classified.
    """
    pts = np.array([SEVERITY_POINTS[s] for s in severities if s in SEVERITY_POINTS], dtype=float)
    if pts.size == 0:
        return 0.0
    return float(round(0.6 * pts.mean() + 0.4 * np.percentile(pts, 90), 2))


def severity_label(score: float) -> str:
    """Map a 0-100 severity score back to a label (thresholds halfway between level points)."""
    if score >= 87.5:
        return "CRITICAL"
    if score >= 60:
        return "HIGH"
    if score >= 30:
        return "MEDIUM"
    return "LOW"


def severity_distribution(severities: Iterable[str | None]) -> dict[str, int]:
    counts = {s: 0 for s in SEVERITY_LEVELS}
    counts["UNCLASSIFIED"] = 0
    for s in severities:
        if s in counts:
            counts[s] += 1
        else:
            counts["UNCLASSIFIED"] += 1
    return counts
