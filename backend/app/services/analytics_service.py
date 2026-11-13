"""Analytics use-cases composing repository data with deterministic analytics functions.
No LLM is used here: every number is computed from stored records."""
from __future__ import annotations

import json
from typing import Any

import pandas as pd

from app.analytics.prioritization import compute_issue_scores, unique_affected_customers
from app.analytics.segmentation import DIMENSIONS, issue_segment_matrix, segment_breakdown
from app.analytics.severity import severity_distribution
from app.analytics.trends import detect_emerging_issues, issue_daily_series, volume_over_time
from app.core.config import Settings
from app.database.repository import ComplaintFilters, Repository

_JSON_FIELDS = ("symptoms", "root_cause_hypotheses", "supporting_complaint_ids", "representative_ids", "keywords",
                "breakdown")


def _decode_issue(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out.pop("centroid", None)
    out.pop("label_signature", None)
    for f in _JSON_FIELDS:
        if isinstance(out.get(f), str):
            try:
                out[f] = json.loads(out[f])
            except ValueError:
                pass
    return out


class AnalyticsService:
    def __init__(self, repo: Repository, settings: Settings):
        self.repo = repo
        self.settings = settings

    # ---------------------------------------------------------------- helpers
    def _frame(self, filters: ComplaintFilters) -> pd.DataFrame:
        return self.repo.complaint_frame(filters)

    def _emerging(self, df: pd.DataFrame) -> dict[str, Any]:
        s = self.settings
        return detect_emerging_issues(df, s.trend_baseline_days, s.trend_recent_days, s.trend_z_threshold,
                                      s.trend_min_recent_count)

    def _scores(self, df: pd.DataFrame, filters: ComplaintFilters) -> list[dict[str, Any]]:
        """Stored (global) metrics when unfiltered; recomputed on the filtered subset otherwise."""
        issues = {i["issue_id"]: i for i in self.repo.issues_with_metrics()}
        if filters.is_empty():
            return [_decode_issue(i) for i in issues.values() if i.get("calculated_at")]
        scores = compute_issue_scores(df, list(issues.values()), self.settings.weights)
        out = []
        for s in scores:
            base = _decode_issue(issues[s.issue_id]) if s.issue_id in issues else {"issue_id": s.issue_id}
            base.update(s.as_metrics_row())
            base["severity"] = s.severity
            base["filtered"] = True
            out.append(base)
        return out

    # ---------------------------------------------------------------- views
    def overview(self, filters: ComplaintFilters) -> dict[str, Any]:
        df = self._frame(filters)
        scores = self._scores(df, filters)
        scores.sort(key=lambda r: -(r.get("priority_score") or 0))
        emerging = self._emerging(df)
        u, known, anon = unique_affected_customers(df)
        incidents = int(df["incident_id"].fillna(df["complaint_id"]).nunique()) if not df.empty else 0
        issue_ids = set(df["issue_id"].dropna()) if not df.empty else set()
        return {
            "total_reports": int(len(df)),
            "total_incidents": incidents,
            "duplicate_reports": int(len(df)) - incidents,
            "unique_affected_customers": u,
            "verified_customers": known,
            "anonymous_incidents": anon,
            "issue_clusters": len(issue_ids),
            "noise_complaints": int(df["issue_id"].isna().sum()) if not df.empty else 0,
            "critical_high_issues": sum(1 for s in scores if s.get("severity") in ("CRITICAL", "HIGH")),
            "emerging_issues": sum(1 for i in emerging["issues"] if i["is_emerging"]),
            "emerging_limited_data": emerging["limited_data"],
            "severity_distribution": severity_distribution(df["severity"].tolist() if not df.empty else []),
            "by_source": self._count_by(df, "source"),
            "by_country": self._count_by(df, "country", unknown="Unknown"),
            "by_category": self._count_by(df, "category", unknown="Unclassified"),
            "top_issues": [self._issue_summary(s) for s in scores[:10]],
            "llm_enabled": self.settings.llm_available,
            "filtered": not filters.is_empty(),
            **self.grouping_info(),
        }

    def grouping_info(self) -> dict[str, Any]:
        """How issues are currently formed (mirrors PipelineOrchestrator._cluster)."""
        n = self.repo.embedded_complaint_count()
        threshold = self.settings.semantic_clustering_min_complaints
        return {"grouping_mode": "category" if n < threshold else "semantic",
                "semantic_min_complaints": threshold, "processed_complaints": n}

    @staticmethod
    def _count_by(df: pd.DataFrame, col: str, unknown: str | None = None) -> list[dict[str, Any]]:
        if df.empty:
            return []
        series = df[col].fillna(unknown) if unknown else df[col].dropna()
        out = []
        for value, g in df.assign(_k=series).groupby("_k"):
            out.append({"key": value, "reports": int(len(g)),
                        "unique_customers": int(g["customer_id"].dropna().nunique())})
        out.sort(key=lambda r: -r["reports"])
        return out

    @staticmethod
    def _issue_summary(s: dict[str, Any]) -> dict[str, Any]:
        keys = ("issue_id", "title", "category", "severity", "status", "priority_score", "unique_affected_customers",
                "total_reports", "total_incidents", "impact_score", "severity_score", "recurrence_score",
                "growth_score", "business_impact_score", "label_source", "explanation")
        return {k: s.get(k) for k in keys}

    def list_issues(self, filters: ComplaintFilters, include_stale: bool = False) -> list[dict[str, Any]]:
        if filters.is_empty():
            rows = [_decode_issue(i) for i in self.repo.issues_with_metrics(include_stale=include_stale)]
        else:
            rows = self._scores(self._frame(filters), filters)
            rows.sort(key=lambda r: -(r.get("priority_score") or 0))
        emerging = {i["issue_id"]: i for i in self._emerging(self._frame(filters))["issues"]}
        out = []
        for r in rows:
            item = self._issue_summary(r)
            e = emerging.get(r["issue_id"])
            item["is_emerging"] = bool(e and e["is_emerging"])
            item["z_score"] = e["z_score"] if e else None
            out.append(item)
        return out

    def top10(self, filters: ComplaintFilters) -> list[dict[str, Any]]:
        return self.list_issues(filters)[:10]

    def issue_detail(self, issue_id: str, filters: ComplaintFilters) -> dict[str, Any] | None:
        issue = self.repo.issue_with_metrics(issue_id)
        if issue is None:
            return None
        detail = _decode_issue(issue)
        issue_filters = ComplaintFilters(**{**filters.__dict__, "issue_id": issue_id})
        df = self._frame(issue_filters)
        if not filters.is_empty():
            scored = [s for s in self._scores(self._frame(filters), filters) if s["issue_id"] == issue_id]
            if scored:
                detail.update({k: scored[0][k] for k in scored[0] if k not in ("title", "description")})
        reps = []
        for cid in detail.get("representative_ids") or []:
            c = self.repo.get_complaint_detail(cid)
            if c:
                reps.append({
                    "complaint_id": cid, "text": c["normalized_text"], "source": c["source"],
                    "timestamp": c["timestamp"], "severity": c["severity"], "category": c["category"],
                    "classification_evidence": c["classification_evidence"],
                    "classification_source": c["classification_source"],
                    "assignment_confidence": c["assignment_confidence"],
                })
        detail["representative_complaints"] = reps
        detail["trend"] = issue_daily_series(df, issue_id) if not df.empty else []
        detail["severity_distribution"] = severity_distribution(df["severity"].tolist() if not df.empty else [])
        detail["by_source"] = self._count_by(df, "source")
        detail["demographics"] = {
            d: segment_breakdown(df, d, self.repo.populations(d), self.settings.segment_min_customers, top_issues=0)
            for d in DIMENSIONS
        }
        emerging = {i["issue_id"]: i for i in self._emerging(self._frame(filters))["issues"]}
        detail["emerging"] = emerging.get(issue_id)
        detail["evidence"] = {
            "label_source": detail.get("label_source"),
            "supporting_complaint_ids": detail.get("supporting_complaint_ids"),
            "keywords": detail.get("keywords"),
            "classification_sources": df["category"].notna().sum() if not df.empty else 0,
            "category_distribution": self._count_by(df, "category", unknown="Unclassified"),
        }
        detail["evidence"]["classification_sources"] = int(detail["evidence"]["classification_sources"])
        return detail

    def trends(self, filters: ComplaintFilters, granularity: str = "D") -> dict[str, Any]:
        df = self._frame(filters)
        return {
            "granularity": granularity,
            "series": volume_over_time(df, granularity),
            "emerging": self._emerging(df),
        }

    def segments(self, filters: ComplaintFilters, dimension: str | None = None) -> dict[str, Any]:
        df = self._frame(filters)
        dims = [dimension] if dimension else DIMENSIONS
        return {
            "segments": {
                d: segment_breakdown(df, d, self.repo.populations(d), self.settings.segment_min_customers) for d in dims
            },
            "issue_matrix": issue_segment_matrix(df, dims[0], self.settings.segment_min_customers),
        }
