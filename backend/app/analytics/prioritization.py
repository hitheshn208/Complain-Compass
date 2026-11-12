"""Deterministic weighted priority scoring.

    P = wI*I + wS*S + wR*R + wG*G + wB*B      (default 0.35, 0.25, 0.20, 0.10, 0.10)

Every component is normalized to 0-100:

I  Impact     = 100 * ln(1 + u) / ln(1 + u_max)
              u = unique affected customers (distinct verified customer IDs + anonymous
              incidents, each anonymous incident counted as one possible customer).
              Uses customers, not raw reports, and log scaling, so a cluster with many
              duplicate reports or a few very large clusters cannot dominate.
S  Severity   = 0.6 * mean + 0.4 * p90 of complaint severity points
              (CRITICAL 100, HIGH 75, MEDIUM 45, LOW 15) - see analytics.severity.
R  Recurrence = 100 * (0.5 * repeat_share + 0.5 * persistence)
              repeat_share = share of affected verified customers with >= 2 distinct incidents
              persistence  = share of weeks (in the observed data span) with >= 1 incident.
G  Growth     = 100 * sigmoid(1.5 * log2((recent + 1) / (prior_weekly_avg + 1)))
              recent = incidents in the last 7 days before the as-of date, prior = weekly
              average over the 21 days before that. 50 = flat. If < 14 days of history
              exist the score is the neutral 50 and flagged ``insufficient_history``.
B  Business   = 0.7 * category_weight + 0.3 * 100 * financial_share
              category_weight from BUSINESS_CATEGORY_WEIGHTS; financial_share = share of
              reports mentioning amounts / transaction or order IDs.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import numpy as np
import pandas as pd

from app.analytics.severity import severity_label, severity_score

BUSINESS_CATEGORY_WEIGHTS: dict[str, float] = {
    "Payments": 90, "Billing": 85, "Refunds": 80, "Account Status": 75, "Account Access": 70,
    "Subscription": 65, "App Stability": 60, "Delivery": 55, "Product Quality": 55,
    "Customer Support": 45, "Other": 30,
}
COMPONENT_LABELS = {
    "impact": "Unique customer impact",
    "severity": "Severity",
    "recurrence": "Recurrence",
    "growth": "Growth",
    "business": "Business impact",
}
MIN_HISTORY_DAYS_FOR_GROWTH = 14


@dataclass
class IssueScore:
    issue_id: str
    unique_affected_customers: int
    known_customers: int
    anonymous_incidents: int
    total_reports: int
    total_incidents: int
    impact_score: float
    severity_score: float
    recurrence_score: float
    growth_score: float
    business_impact_score: float
    priority_score: float
    severity: str
    breakdown: dict[str, Any]
    explanation: str

    def as_metrics_row(self) -> dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "unique_affected_customers": self.unique_affected_customers,
            "total_reports": self.total_reports,
            "total_incidents": self.total_incidents,
            "severity_score": self.severity_score,
            "recurrence_score": self.recurrence_score,
            "growth_score": self.growth_score,
            "business_impact_score": self.business_impact_score,
            "impact_score": self.impact_score,
            "priority_score": self.priority_score,
            "explanation": self.explanation,
            "breakdown": self.breakdown,
        }


def impact_score(u: int, u_max: int) -> float:
    if u <= 0 or u_max <= 0:
        return 0.0
    return round(100.0 * math.log1p(u) / math.log1p(u_max), 2)


def growth_score(recent: float, prior_weekly: float) -> float:
    ratio = (recent + 1.0) / (prior_weekly + 1.0)
    return round(100.0 / (1.0 + math.exp(-1.5 * math.log2(ratio))), 2)


def _has_financial_entity(entities: Any, refs: Any) -> bool:
    for raw in (entities, refs):
        if not raw:
            continue
        try:
            items = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            continue
        for e in items:
            if isinstance(e, dict) and e.get("type") in {"amount", "transaction_id", "order_id", "payment_method"}:
                return True
            if isinstance(e, str) and e:
                return True
    return False


def _incident_key(df: pd.DataFrame) -> pd.Series:
    """Incident id, or the complaint id when dedup has not run yet (1 complaint = 1 incident)."""
    return df["incident_id"].where(df["incident_id"].notna(), df["complaint_id"])


def unique_affected_customers(df: pd.DataFrame) -> tuple[int, int, int]:
    """(total, verified distinct customers, anonymous incidents)."""
    if df.empty:
        return 0, 0, 0
    known = int(df["customer_id"].dropna().nunique())
    anon = int(_incident_key(df[df["customer_id"].isna()]).nunique())
    return known + anon, known, anon


def compute_issue_scores(
    df: pd.DataFrame,
    issues: list[dict[str, Any]],
    weights: dict[str, float],
    as_of: pd.Timestamp | None = None,
) -> list[IssueScore]:
    """Score every issue that has at least one complaint in ``df`` (the analytics frame)."""
    if df.empty:
        return []
    data = df[df["issue_id"].notna()].copy()
    if data.empty:
        return []
    data["_incident"] = _incident_key(data)
    as_of = as_of or df["timestamp"].max()
    data_start = df["timestamp"].min()
    history_days = (as_of - data_start).total_seconds() / 86400.0
    total_weeks = max(1, math.ceil((history_days + 1) / 7))
    issue_meta = {i["issue_id"]: i for i in issues}

    per_issue: dict[str, dict[str, Any]] = {}
    for issue_id, g in data.groupby("issue_id"):
        u, known, anon = unique_affected_customers(g)
        inc_first = g.groupby("_incident")["timestamp"].min()
        recent = int(((inc_first > as_of - timedelta(days=7)) & (inc_first <= as_of)).sum())
        prior = int(((inc_first > as_of - timedelta(days=28)) & (inc_first <= as_of - timedelta(days=7))).sum()) / 3.0
        if history_days >= MIN_HISTORY_DAYS_FOR_GROWTH:
            g_score, insufficient = growth_score(recent, prior), False
        else:
            g_score, insufficient = 50.0, True
        known_g = g[g["customer_id"].notna()]
        inc_per_cust = known_g.groupby("customer_id")["_incident"].nunique()
        repeat_share = float((inc_per_cust >= 2).mean()) if len(inc_per_cust) else 0.0
        weeks_active = int(((as_of - inc_first).dt.total_seconds() // (7 * 86400)).nunique())
        persistence = min(1.0, weeks_active / total_weeks)
        r_score = round(100.0 * (0.5 * repeat_share + 0.5 * persistence), 2)

        s_score = severity_score(g["severity"].tolist())
        meta = issue_meta.get(issue_id, {})
        cats = Counter(c for c in g["category"].dropna())
        category = meta.get("category") or (cats.most_common(1)[0][0] if cats else "Other")
        cat_weight = BUSINESS_CATEGORY_WEIGHTS.get(category, BUSINESS_CATEGORY_WEIGHTS["Other"])
        fin_share = float(np.mean([_has_financial_entity(e, r) for e, r in zip(g["entities"], g["reference_ids"])]))
        b_score = round(0.7 * cat_weight + 0.3 * 100 * fin_share, 2)

        per_issue[issue_id] = dict(
            u=u, known=known, anon=anon, reports=len(g), incidents=int(g["_incident"].nunique()),
            severity=s_score, recurrence=r_score, growth=g_score, business=b_score, recent=recent, prior=prior,
            insufficient=insufficient, repeat_share=repeat_share, persistence=persistence, weeks_active=weeks_active,
            category=category, cat_weight=cat_weight, fin_share=fin_share,
            title=meta.get("title", issue_id),
        )

    u_max = max(v["u"] for v in per_issue.values())
    scores: list[IssueScore] = []
    for issue_id, v in per_issue.items():
        comps = {
            "impact": impact_score(v["u"], u_max),
            "severity": v["severity"],
            "recurrence": v["recurrence"],
            "growth": v["growth"],
            "business": v["business"],
        }
        priority = round(sum(weights[k] * comps[k] for k in comps), 2)
        breakdown = {
            "weights": weights,
            "components": {
                k: {
                    "label": COMPONENT_LABELS[k],
                    "score": comps[k],
                    "weight": weights[k],
                    "contribution": round(weights[k] * comps[k], 2),
                }
                for k in comps
            },
            "inputs": {
                "unique_affected_customers": v["u"],
                "verified_customers": v["known"],
                "anonymous_incidents": v["anon"],
                "max_unique_customers_any_issue": u_max,
                "total_reports": v["reports"],
                "total_incidents": v["incidents"],
                "duplicate_reports_collapsed": v["reports"] - v["incidents"],
                "repeat_customer_share": round(v["repeat_share"], 3),
                "weeks_active": v["weeks_active"],
                "persistence": round(v["persistence"], 3),
                "incidents_last_7d": v["recent"],
                "prior_weekly_avg": round(v["prior"], 2),
                "growth_insufficient_history": v["insufficient"],
                "category": v["category"],
                "category_business_weight": v["cat_weight"],
                "financial_reference_share": round(v["fin_share"], 3),
                "as_of": as_of.isoformat(),
            },
        }
        scores.append(
            IssueScore(
                issue_id=issue_id,
                unique_affected_customers=v["u"],
                known_customers=v["known"],
                anonymous_incidents=v["anon"],
                total_reports=v["reports"],
                total_incidents=v["incidents"],
                impact_score=comps["impact"],
                severity_score=comps["severity"],
                recurrence_score=comps["recurrence"],
                growth_score=comps["growth"],
                business_impact_score=comps["business"],
                priority_score=priority,
                severity=severity_label(comps["severity"]),
                breakdown=breakdown,
                explanation=explain(v, comps, weights, priority),
            )
        )
    scores.sort(key=lambda s: (-s.priority_score, s.issue_id))
    return scores


def explain(v: dict[str, Any], comps: dict[str, float], weights: dict[str, float], priority: float) -> str:
    """Template-based (non-LLM) explanation of the score."""
    contrib = sorted(((weights[k] * comps[k], k) for k in comps), reverse=True)
    top = [COMPONENT_LABELS[k].lower() for _, k in contrib[:2]]
    parts = [
        f"Priority {priority:.1f}/100, driven mainly by {top[0]} and {top[1]}.",
        f"{v['u']} unique affected customer(s) ({v['known']} verified, {v['anon']} anonymous) across "
        f"{v['reports']} report(s); {v['reports'] - v['incidents']} duplicate report(s) collapsed into incidents.",
        f"Severity score {comps['severity']:.0f} ({severity_label(comps['severity'])}).",
        f"Recurrence {comps['recurrence']:.0f}: active in {v['weeks_active']} week(s), "
        f"{v['repeat_share']:.0%} of verified customers hit it more than once.",
    ]
    if v["insufficient"]:
        parts.append("Growth set to neutral 50: less than 14 days of history.")
    else:
        trend = "rising" if comps["growth"] > 60 else "falling" if comps["growth"] < 40 else "stable"
        parts.append(
            f"Growth {comps['growth']:.0f} ({trend}): {v['recent']} new incident(s) in the last 7 days vs "
            f"{v['prior']:.1f}/week before."
        )
    parts.append(f"Business impact {comps['business']:.0f}: category '{v['category']}', "
                 f"{v['fin_share']:.0%} of reports reference money/orders.")
    return " ".join(parts)
