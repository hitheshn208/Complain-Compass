import json

import numpy as np

from app.deduplication.incident_matching import DedupConfig, conflicting_references, deduplicate


def vec(*hot, dim=16):
    v = np.zeros(dim, dtype=np.float32)
    for h in hot:
        v[h] = 1.0
    return v / np.linalg.norm(v)


def c(cid, customer, ts, refs=(), issue="I1", category="Payments"):
    return {"complaint_id": cid, "customer_id": customer, "timestamp": ts, "reference_ids": json.dumps(list(refs)),
            "issue_id": issue, "category": category}


def incident_of(result, cid):
    return next(l["incident_id"] for l in result.links if l["complaint_id"] == cid)


def test_same_customer_cross_channel_shared_order_is_merged():
    rows = [c("a", "C1", "2026-10-01T10:00:00+00:00", ["ORD-111111"]),
            c("b", "C1", "2026-10-03T10:00:00+00:00", ["ORD-111111"], issue="I2", category="Refunds")]
    r = deduplicate(rows, {"a": vec(0), "b": vec(5)})
    assert incident_of(r, "a") == incident_of(r, "b")
    link = next(l for l in r.links if l["complaint_id"] == "b")
    assert link["link_type"] == "DUPLICATE" and link["confidence"] >= 0.9
    assert link["evidence"]["shared_reference_ids"] == ["ORD-111111"]


def test_different_customers_never_merged_even_if_identical():
    rows = [c("a", "C1", "2026-10-01T10:00:00+00:00", ["ORD-1"]), c("b", "C2", "2026-10-01T11:00:00+00:00", ["ORD-1"])]
    r = deduplicate(rows, {"a": vec(0), "b": vec(0)})
    assert incident_of(r, "a") != incident_of(r, "b")
    assert r.duplicate_count == 0


def test_anonymous_complaints_not_merged():
    rows = [c("a", None, "2026-10-01T10:00:00+00:00"), c("b", None, "2026-10-01T10:05:00+00:00")]
    r = deduplicate(rows, {"a": vec(0), "b": vec(0)})
    assert len(r.incidents) == 2


def test_semantic_similarity_alone_is_not_enough():
    # same customer, identical text, but 30 days apart -> separate incidents (recurrence, not duplicate)
    rows = [c("a", "C1", "2026-09-01T10:00:00+00:00"), c("b", "C1", "2026-10-01T10:00:00+00:00")]
    r = deduplicate(rows, {"a": vec(0), "b": vec(0)})
    assert incident_of(r, "a") != incident_of(r, "b")


def test_identity_time_semantic_topic_merges():
    rows = [c("a", "C1", "2026-10-01T10:00:00+00:00"), c("b", "C1", "2026-10-02T10:00:00+00:00")]
    r = deduplicate(rows, {"a": vec(0, 1), "b": vec(0, 1, 2)})
    assert incident_of(r, "a") == incident_of(r, "b")
    assert next(l for l in r.links if l["complaint_id"] == "b")["evidence"]["rule"].startswith("T2")


def test_conflicting_order_ids_never_merged():
    rows = [c("a", "C1", "2026-10-01T10:00:00+00:00", ["ORD-111111"]),
            c("b", "C1", "2026-10-01T12:00:00+00:00", ["ORD-222222"])]
    r = deduplicate(rows, {"a": vec(0), "b": vec(0)})
    assert incident_of(r, "a") != incident_of(r, "b")
    assert not r.review


def test_order_and_transaction_ids_do_not_conflict():
    assert not conflicting_references({"ORD-1"}, {"TXN-9"})
    assert conflicting_references({"ORD-1"}, {"ORD-2"})
    assert not conflicting_references({"ORD-1", "TXN-9"}, {"ORD-1"})


def test_partial_evidence_flagged_for_review_not_merged():
    # same customer, same topic, moderately similar text, within window -> below threshold -> review
    rows = [c("a", "C1", "2026-10-01T10:00:00+00:00"), c("b", "C1", "2026-10-02T10:00:00+00:00")]
    a, b = vec(0), vec(0, 1, 2, 3)  # cos = 0.5
    r = deduplicate(rows, {"a": a, "b": b}, DedupConfig(semantic_threshold=0.55))
    assert incident_of(r, "a") != incident_of(r, "b")
    assert len(r.review) == 1 and r.review[0]["complaint_id"] == "b"


def test_incident_ids_are_deterministic():
    rows = [c("a", "C1", "2026-10-01T10:00:00+00:00"), c("b", "C2", "2026-10-01T10:00:00+00:00")]
    r1 = deduplicate(rows, {})
    r2 = deduplicate(rows, {})
    assert [i["incident_id"] for i in r1.incidents] == [i["incident_id"] for i in r2.incidents]
