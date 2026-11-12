"""Customer segmentation analytics.

Rules:
  * Counts are of *unique customers* (verified IDs) and complaints, never mixed up.
  * A complaint *rate* is reported only when a population denominator for that segment
    has been supplied (segment_populations table). Otherwise ``metric_type == "count"``.
  * Segments with fewer than ``min_customers`` unique customers are suppressed to avoid
    exposing individuals. No customer identifiers are ever returned.
  * Missing demographic values are grouped as "Unknown" (never inferred).
"""
from __future__ import annotations

from typing import Any

import pandas as pd

DIMENSIONS = ["country", "state", "age_group", "student_status", "income_bracket"]
UNKNOWN = "Unknown"


def segment_breakdown(
    df: pd.DataFrame,
    dimension: str,
    populations: dict[str, int] | None = None,
    min_customers: int = 3,
    top_issues: int = 5,
) -> dict[str, Any]:
    if dimension not in DIMENSIONS:
        raise ValueError(f"Unknown dimension '{dimension}'")
    populations = populations or {}
    has_population = bool(populations)
    rows: list[dict[str, Any]] = []
    if df.empty:
        return {"dimension": dimension, "metric_type": "rate" if has_population else "count", "segments": rows,
                "suppressed_segments": 0, "min_customers": min_customers}

    data = df.copy()
    data[dimension] = data[dimension].fillna(UNKNOWN)
    suppressed = 0
    for value, g in data.groupby(dimension):
        customers = int(g["customer_id"].dropna().nunique())
        complaints = int(len(g))
        incidents = int(g["incident_id"].fillna(g["complaint_id"]).nunique())
        if value != UNKNOWN and customers < min_customers:
            suppressed += 1
            continue
        issue_counts = (
            g[g["issue_id"].notna()]
            .groupby(["issue_id", "issue_title"])["customer_id"]
            .nunique()
            .sort_values(ascending=False)
            .head(top_issues)
        )
        row: dict[str, Any] = {
            "segment": value,
            "unique_customers": customers,
            "complaints": complaints,
            "incidents": incidents,
            "complaints_per_customer": round(complaints / customers, 2) if customers else None,
            "top_issues": [
                {"issue_id": iid, "title": title, "unique_customers": int(n)} for (iid, title), n in issue_counts.items()
            ],
            "population": populations.get(value),
            "affected_rate_per_1000": None,
            "complaint_rate_per_1000": None,
        }
        pop = populations.get(value)
        if pop:
            row["affected_rate_per_1000"] = round(1000 * customers / pop, 3)
            row["complaint_rate_per_1000"] = round(1000 * complaints / pop, 3)
        rows.append(row)
    rows.sort(key=lambda r: (r["segment"] == UNKNOWN, -r["unique_customers"], -r["complaints"]))
    return {
        "dimension": dimension,
        "metric_type": "rate" if has_population else "count",
        "rate_note": None if has_population else
            "No population denominators supplied for this dimension: values are counts, not rates.",
        "segments": rows,
        "suppressed_segments": suppressed,
        "min_customers": min_customers,
    }


def issue_segment_matrix(df: pd.DataFrame, dimension: str, min_customers: int = 3, top_n_issues: int = 10) -> dict[str, Any]:
    """Unique customers per (issue, segment) for the top issues - used for heatmaps/stacked bars."""
    if df.empty or dimension not in DIMENSIONS:
        return {"dimension": dimension, "issues": [], "segments": [], "cells": []}
    data = df[df["issue_id"].notna() & df["customer_id"].notna()].copy()
    data[dimension] = data[dimension].fillna(UNKNOWN)
    top = data.groupby(["issue_id", "issue_title"])["customer_id"].nunique().sort_values(ascending=False).head(top_n_issues)
    data = data[data["issue_id"].isin([i for i, _ in top.index])]
    cells = []
    for (iid, seg), g in data.groupby(["issue_id", dimension]):
        n = int(g["customer_id"].nunique())
        cells.append({"issue_id": iid, "segment": seg, "unique_customers": n if n >= min_customers or seg == UNKNOWN else None})
    segments = sorted(set(c["segment"] for c in cells))
    return {
        "dimension": dimension,
        "issues": [{"issue_id": i, "title": t} for i, t in top.index],
        "segments": segments,
        "cells": cells,
    }
