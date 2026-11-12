"""Incident deduplication: collapse duplicate reports of ONE customer's ONE problem.

Distinct from issue clustering:
  * duplicate report  = same customer, same underlying incident, possibly several channels
  * recurring issue   = different customers, same underlying problem (handled by clustering)

Matching is only attempted *within a verified customer identity*. Different customers are
never merged, however similar their text. Embedding similarity alone never triggers a
merge; it must be combined with identity + time window + issue/category agreement, or a
shared business reference (order / transaction ID).

Decision tiers for a complaint vs. an existing incident of the same customer:
  T1  shared reference ID (order/txn)                                   -> merge, conf 0.95
  --  conflicting reference IDs (same type, e.g. two different orders)  -> never merge
  T2  within window AND sim >= threshold AND same issue/category        -> merge, conf 0.75-0.90
  T3  partial evidence (two of: window, sim >= threshold-0.1, same issue/category)
                                                                         -> new incident + review candidate
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Sequence

import numpy as np

from app.deduplication.identity_resolution import group_by_customer

INCIDENT_NS = uuid.UUID("6f1c2a9e-4b0e-4c55-9a51-2f0c3e7d8a11")


def incident_id_for(canonical_complaint_id: str) -> str:
    """Deterministic incident id so recomputation keeps IDs stable."""
    return str(uuid.uuid5(INCIDENT_NS, canonical_complaint_id))


@dataclass
class DedupConfig:
    time_window_days: float = 7.0
    semantic_threshold: float = 0.55
    auto_merge_confidence: float = 0.75
    review_confidence: float = 0.45


@dataclass
class _Incident:
    incident_id: str
    customer_id: str | None
    members: list[dict[str, Any]] = field(default_factory=list)

    @property
    def first(self) -> datetime:
        return self.members[0]["_ts"]

    @property
    def last(self) -> datetime:
        return max(m["_ts"] for m in self.members)

    @property
    def refs(self) -> set[str]:
        out: set[str] = set()
        for m in self.members:
            out |= m["_refs"]
        return out


@dataclass
class DedupResult:
    incidents: list[dict[str, Any]]
    links: list[dict[str, Any]]
    review: list[dict[str, Any]]

    @property
    def duplicate_count(self) -> int:
        return sum(1 for l in self.links if l["link_type"] == "DUPLICATE")


def _parse_refs(raw: Any) -> set[str]:
    if not raw:
        return set()
    if isinstance(raw, (list, set, tuple)):
        return {str(r).upper() for r in raw}
    try:
        return {str(r).upper() for r in json.loads(raw)}
    except (TypeError, ValueError):
        return set()


def _ref_type(ref: str) -> str:
    return ref.split("-", 1)[0] if "-" in ref else "OTHER"


def conflicting_references(a: set[str], b: set[str]) -> bool:
    """True when both sides cite a reference of the same type (e.g. two order IDs) and none match.
    An order ID on one report and a transaction ID on another is NOT a conflict."""
    if a & b:
        return False
    types_a = {_ref_type(r) for r in a}
    types_b = {_ref_type(r) for r in b}
    return bool(types_a & types_b)


def _ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def evaluate_match(
    complaint: dict[str, Any], incident: _Incident, sims: dict[str, float], cfg: DedupConfig
) -> tuple[float, dict[str, Any], bool]:
    """Score a complaint against an existing incident of the same customer.
    Returns (confidence, evidence, auto_merge)."""
    evidence: dict[str, Any] = {"customer_match": True}
    c_refs = complaint["_refs"]
    i_refs = incident.refs
    shared = sorted(c_refs & i_refs)
    gap_days = abs((complaint["_ts"] - incident.last).total_seconds()) / 86400.0
    evidence["days_since_last_report"] = round(gap_days, 2)
    max_sim = max((sims.get(m["complaint_id"], 0.0) for m in incident.members), default=0.0)
    evidence["max_semantic_similarity"] = round(max_sim, 3)
    issue_ids = {m.get("issue_id") for m in incident.members if m.get("issue_id")}
    categories = {m.get("category") for m in incident.members if m.get("category")}
    same_issue = bool(complaint.get("issue_id")) and complaint.get("issue_id") in issue_ids
    same_category = bool(complaint.get("category")) and complaint.get("category") in categories
    evidence["same_issue_cluster"] = same_issue
    evidence["same_category"] = same_category

    if shared:
        evidence["shared_reference_ids"] = shared
        evidence["rule"] = "T1_shared_reference"
        return 0.95, evidence, True
    if conflicting_references(c_refs, i_refs):
        evidence["conflicting_reference_ids"] = {"complaint": sorted(c_refs), "incident": sorted(i_refs)}
        evidence["rule"] = "conflicting_references"
        return 0.0, evidence, False

    within = gap_days <= cfg.time_window_days
    evidence["within_time_window"] = within
    topical = same_issue or same_category
    if within and max_sim >= cfg.semantic_threshold and topical:
        closeness = 1.0 - gap_days / cfg.time_window_days
        conf = 0.75 + 0.1 * min(1.0, (max_sim - cfg.semantic_threshold) / max(1e-6, 1 - cfg.semantic_threshold)) \
            + 0.05 * closeness
        evidence["rule"] = "T2_identity_time_semantic_topic"
        conf = round(min(conf, 0.9), 3)
        return conf, evidence, conf >= cfg.auto_merge_confidence

    signals = [within or gap_days <= 2 * cfg.time_window_days, max_sim >= cfg.semantic_threshold - 0.1, topical]
    if sum(signals) >= 2:
        conf = round(0.3 + 0.1 * sum(signals) + 0.1 * max(0.0, max_sim - 0.5), 3)
        evidence["rule"] = "T3_partial_evidence_review"
        return conf, evidence, False
    evidence["rule"] = "no_match"
    return 0.0, evidence, False


def deduplicate(
    complaints: Sequence[dict[str, Any]],
    embeddings: dict[str, np.ndarray],
    cfg: DedupConfig | None = None,
) -> DedupResult:
    """Assign every complaint to exactly one incident.

    ``complaints`` items: complaint_id, customer_id, timestamp, reference_ids, issue_id, category.
    ``embeddings``: complaint_id -> L2-normalized vector (used only within one customer).
    """
    cfg = cfg or DedupConfig()
    prepared = []
    for c in complaints:
        prepared.append({**c, "_ts": _ts(c["timestamp"]), "_refs": _parse_refs(c.get("reference_ids"))})
    groups, anonymous = group_by_customer(prepared)

    incidents: list[_Incident] = []
    links: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []

    def _new_incident(c: dict[str, Any], customer_id: str | None, evidence: dict[str, Any]) -> _Incident:
        inc = _Incident(incident_id=incident_id_for(c["complaint_id"]), customer_id=customer_id, members=[c])
        incidents.append(inc)
        links.append({"complaint_id": c["complaint_id"], "incident_id": inc.incident_id, "link_type": "CANONICAL",
                      "confidence": 1.0, "evidence": evidence})
        return inc

    for c in anonymous:
        _new_incident(c, None, {"rule": "no_verified_identity", "note": "Anonymous complaint; not merged with others"})

    for customer_id, items in groups.items():
        cust_incidents: list[_Incident] = []
        ids = [c["complaint_id"] for c in items]
        vecs = [embeddings.get(i) for i in ids]
        sim_matrix: dict[str, dict[str, float]] = {i: {} for i in ids}
        valid = [k for k, v in enumerate(vecs) if v is not None]
        if len(valid) > 1:
            mat = np.vstack([vecs[k] for k in valid])
            sm = mat @ mat.T
            for a_pos, a in enumerate(valid):
                for b_pos, b in enumerate(valid):
                    if a != b:
                        sim_matrix[ids[a]][ids[b]] = float(sm[a_pos, b_pos])

        for c in items:
            best: tuple[float, _Incident, dict[str, Any], bool] | None = None
            for inc in cust_incidents:
                conf, ev, merge = evaluate_match(c, inc, sim_matrix[c["complaint_id"]], cfg)
                if best is None or conf > best[0]:
                    best = (conf, inc, ev, merge)
            if best and best[3]:
                conf, inc, ev, _ = best
                inc.members.append(c)
                links.append({"complaint_id": c["complaint_id"], "incident_id": inc.incident_id,
                              "link_type": "DUPLICATE", "confidence": conf, "evidence": ev})
                continue
            inc_new = _new_incident(c, customer_id, {"rule": "new_incident_for_customer"})
            cust_incidents.append(inc_new)
            if best and best[0] >= cfg.review_confidence:
                review.append({"complaint_id": c["complaint_id"], "incident_id": best[1].incident_id,
                               "confidence": best[0], "evidence": best[2]})

    out_incidents = []
    for inc in incidents:
        issue_votes: dict[str, int] = {}
        for m in inc.members:
            if m.get("issue_id"):
                issue_votes[m["issue_id"]] = issue_votes.get(m["issue_id"], 0) + 1
        issue_id = max(issue_votes.items(), key=lambda kv: kv[1])[0] if issue_votes else None
        out_incidents.append({
            "incident_id": inc.incident_id,
            "customer_id": inc.customer_id,
            "issue_id": issue_id,
            "first_reported_at": inc.first.isoformat(),
            "last_reported_at": inc.last.isoformat(),
            "member_ids": [m["complaint_id"] for m in inc.members],
        })
    return DedupResult(incidents=out_incidents, links=links, review=review)
