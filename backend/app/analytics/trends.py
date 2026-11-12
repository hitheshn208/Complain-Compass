"""Time-series aggregation and emerging-issue detection.

Emerging-issue detector (documented method):
  * as_of = latest complaint timestamp in the data.
  * recent window = last ``recent_days`` days; baseline = the ``baseline_days`` days before it.
  * Daily incident counts per issue (duplicates collapsed). Baseline mean mu, std sigma.
  * sigma_eff = max(sigma, sqrt(mu), 0.5)  (Poisson-style floor so sparse baselines don't explode)
  * z = (recent_mean - mu) / (sigma_eff / sqrt(recent_days))
  * Flag as EMERGING if z >= z_threshold AND recent incident count >= min_recent_count.
  * If the dataset has less than ``baseline_days`` of history before the recent window, no
    issue is declared emerging; results are returned with ``limited_data = True``.
"""
from __future__ import annotations

import math
from datetime import timedelta
from typing import Any

import numpy as np
import pandas as pd


def volume_over_time(df: pd.DataFrame, freq: str = "D") -> list[dict[str, Any]]:
    """Complaint reports, incidents and unique customers per period, with severity split."""
    if df.empty:
        return []
    data = df.copy()
    data["period"] = data["timestamp"].dt.tz_convert("UTC").dt.tz_localize(None).dt.to_period(freq).dt.start_time
    data["_incident"] = data["incident_id"].fillna(data["complaint_id"])
    out = []
    full = pd.date_range(data["period"].min(), data["period"].max(), freq=freq if freq != "W" else "W-MON")
    grouped = {p: g for p, g in data.groupby("period")}
    for p in full if freq == "D" else sorted(grouped):
        g = grouped.get(p)
        if g is None:
            out.append({"period": p.date().isoformat(), "reports": 0, "incidents": 0, "unique_customers": 0,
                        "CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0})
            continue
        sev = g["severity"].value_counts()
        out.append({
            "period": p.date().isoformat(),
            "reports": int(len(g)),
            "incidents": int(g["_incident"].nunique()),
            "unique_customers": int(g["customer_id"].dropna().nunique()),
            **{s: int(sev.get(s, 0)) for s in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]},
        })
    return out


def issue_daily_series(df: pd.DataFrame, issue_id: str) -> list[dict[str, Any]]:
    sub = df[df["issue_id"] == issue_id]
    return volume_over_time(sub, "D")


def detect_emerging_issues(
    df: pd.DataFrame,
    baseline_days: int = 14,
    recent_days: int = 3,
    z_threshold: float = 2.0,
    min_recent_count: int = 3,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "method": "rolling-baseline z-score on daily incident counts",
        "params": {"baseline_days": baseline_days, "recent_days": recent_days, "z_threshold": z_threshold,
                   "min_recent_count": min_recent_count},
        "limited_data": True,
        "history_days": 0.0,
        "as_of": None,
        "issues": [],
    }
    data = df[df["issue_id"].notna()].copy() if not df.empty else df
    if df.empty or data.empty:
        result["note"] = "No clustered complaints available."
        return result
    as_of = df["timestamp"].max()
    start = df["timestamp"].min()
    history_days = (as_of - start).total_seconds() / 86400.0
    result["as_of"] = as_of.isoformat()
    result["history_days"] = round(history_days, 1)
    limited = history_days < baseline_days + recent_days
    result["limited_data"] = limited
    if limited:
        result["note"] = (f"Only {history_days:.1f} days of history; at least {baseline_days + recent_days} days are "
                          "needed before any issue is declared emerging.")

    data["_incident"] = data["incident_id"].fillna(data["complaint_id"])
    firsts = data.groupby(["issue_id", "issue_title", "_incident"])["timestamp"].min().reset_index()
    recent_start = as_of - timedelta(days=recent_days)
    for (issue_id, title), g in firsts.groupby(["issue_id", "issue_title"]):
        recent_count = int((g["timestamp"] > recent_start).sum())
        base = g[(g["timestamp"] > recent_start - timedelta(days=baseline_days)) & (g["timestamp"] <= recent_start)]
        daily = np.zeros(baseline_days)
        for ts in base["timestamp"]:
            idx = int((recent_start - ts).total_seconds() // 86400)
            if 0 <= idx < baseline_days:
                daily[idx] += 1
        mu = float(daily.mean())
        sigma = float(daily.std(ddof=1)) if baseline_days > 1 else 0.0
        sigma_eff = max(sigma, math.sqrt(mu), 0.5)
        recent_mean = recent_count / recent_days
        z = (recent_mean - mu) / (sigma_eff / math.sqrt(recent_days))
        emerging = (not limited) and z >= z_threshold and recent_count >= min_recent_count
        result["issues"].append({
            "issue_id": issue_id,
            "title": title,
            "recent_incidents": recent_count,
            "recent_daily_mean": round(recent_mean, 3),
            "baseline_daily_mean": round(mu, 3),
            "baseline_std": round(sigma, 3),
            "z_score": round(z, 2),
            "is_emerging": bool(emerging),
            "limited_data": limited,
        })
    result["issues"].sort(key=lambda r: -r["z_score"])
    return result
