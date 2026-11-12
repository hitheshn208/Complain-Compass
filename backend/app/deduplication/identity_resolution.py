"""Customer identity resolution.

Only *verified* customer IDs (explicitly provided by the source system and stored in the
customers table) establish identity. We never guess identity from text, names, writing
style or semantic similarity. Complaints without a customer ID remain anonymous.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Sequence


def resolve_identity(complaint: dict[str, Any]) -> str | None:
    cid = complaint.get("customer_id")
    if cid is None:
        return None
    cid = str(cid).strip()
    return cid or None


def group_by_customer(complaints: Sequence[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    """Split complaints into {customer_id: [complaints sorted by time]} and anonymous complaints."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    anonymous: list[dict[str, Any]] = []
    for c in complaints:
        ident = resolve_identity(c)
        if ident is None:
            anonymous.append(c)
        else:
            groups[ident].append(c)
    for items in groups.values():
        items.sort(key=lambda x: (x["timestamp"], x["complaint_id"]))
    return dict(groups), anonymous
