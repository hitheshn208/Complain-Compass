"""Priority scoring, unique-customer counting, segmentation and trend detection."""
import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from app.analytics.prioritization import (compute_issue_scores, growth_score, impact_score,
                                          unique_affected_customers)
from app.analytics.segmentation import segment_breakdown
from app.analytics.severity import severity_label, severity_score
from app.analytics.trends import detect_emerging_issues, volume_over_time
from app.core.config import Settings

END = datetime(2026, 11, 10, tzinfo=timezone.utc)
W = Settings(_env_file=None).weights


def row(cid, issue, customer, days_ago, severity="MEDIUM", incident=None, country="India", category="Payments"):
    return {"complaint_id": cid, "issue_id": issue, "issue_title": f"Issue {issue}", "customer_id": customer,
            "incident_id": incident or f"inc-{cid}", "timestamp": END - timedelta(days=days_ago), "severity": severity,
            "category": category, "entities": "[]", "reference_ids": "[]", "source": "EMAIL", "country": country,
            "state": None, "age_group": None, "student_status": None, "income_bracket": None}


def frame(rows):
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


def test_unique_customers_not_raw_reports():
    # Issue A: 12 reports from ONE customer (all one incident); Issue B: 5 reports from 5 customers
    rows = [row(f"a{i}", "A", "C1", 1, incident="incA") for i in range(12)]
    rows += [row(f"b{i}", "B", f"C{i + 10}", 1) for i in range(5)]
    df = frame(rows)
    assert unique_affected_customers(df[df.issue_id == "A"]) == (1, 1, 0)
    scores = {s.issue_id: s for s in compute_issue_scores(df, [], W)}
    assert scores["A"].total_reports == 12 and scores["A"].unique_affected_customers == 1
    assert scores["B"].impact_score > scores["A"].impact_score
    assert scores["A"].breakdown["inputs"]["duplicate_reports_collapsed"] == 11


def test_anonymous_incidents_counted_individually():
    df = frame([row("x1", "A", None, 1), row("x2", "A", None, 1), row("x3", "A", "C1", 1)])
    assert unique_affected_customers(df) == (3, 1, 2)


def test_components_bounded_and_contributions_sum():
    rows = [row(f"r{i}", "A" if i % 2 else "B", f"C{i}", i, severity=["LOW", "HIGH", "CRITICAL"][i % 3])
            for i in range(40)]
    for s in compute_issue_scores(frame(rows), [], W):
        comps = s.breakdown["components"]
        for comp in comps.values():
            assert 0 <= comp["score"] <= 100
        assert sum(c["contribution"] for c in comps.values()) == pytest.approx(s.priority_score, abs=0.05)
        assert s.explanation.startswith("Priority")


def test_scoring_is_deterministic():
    rows = [row(f"r{i}", "A" if i % 3 else "B", f"C{i % 7}", i % 20) for i in range(50)]
    a = [(s.issue_id, s.priority_score) for s in compute_issue_scores(frame(rows), [], W)]
    b = [(s.issue_id, s.priority_score) for s in compute_issue_scores(frame(rows), [], W)]
    assert a == b


def test_weights_must_sum_to_one():
    with pytest.raises(ValueError):
        Settings(weight_impact=0.5, _env_file=None)


def test_normalization_functions():
    assert impact_score(10, 10) == 100
    assert impact_score(0, 10) == 0
    assert 0 < impact_score(3, 100) < 50
    assert growth_score(2, 2) == pytest.approx(50)
    assert growth_score(10, 1) > 80
    assert growth_score(0, 10) < 20
    assert severity_score(["CRITICAL"] * 5) == 100
    assert severity_score([]) == 0
    assert severity_label(severity_score(["LOW", "LOW"])) == "LOW"


def test_growth_neutral_with_insufficient_history():
    rows = [row(f"r{i}", "A", f"C{i}", i % 5) for i in range(10)]
    s = compute_issue_scores(frame(rows), [], W)[0]
    assert s.growth_score == 50.0
    assert s.breakdown["inputs"]["growth_insufficient_history"] is True


def test_segment_counts_vs_rates_and_suppression():
    rows = [row(f"i{i}", "A", f"C{i}", 1, country="India") for i in range(6)]
    rows += [row(f"u{i}", "A", f"U{i}", 1, country="Canada") for i in range(2)]  # below k=3 -> suppressed
    rows += [row("n1", "A", "N1", 1, country=None)]
    df = frame(rows)
    out = segment_breakdown(df, "country", None, min_customers=3)
    assert out["metric_type"] == "count" and out["rate_note"]
    segs = {s["segment"]: s for s in out["segments"]}
    assert segs["India"]["unique_customers"] == 6 and segs["India"]["affected_rate_per_1000"] is None
    assert "Canada" not in segs and out["suppressed_segments"] == 1
    assert "Unknown" in segs
    assert "C1" not in json.dumps(out)  # no customer identifiers leak

    rated = segment_breakdown(df, "country", {"India": 2000}, min_customers=3)
    assert rated["metric_type"] == "rate"
    assert {s["segment"]: s for s in rated["segments"]}["India"]["affected_rate_per_1000"] == 3.0


def test_emerging_requires_history():
    rows = [row(f"r{i}", "A", f"C{i}", 0.5) for i in range(10)]
    res = detect_emerging_issues(frame(rows))
    assert res["limited_data"] is True
    assert not any(i["is_emerging"] for i in res["issues"])


def test_emerging_spike_detected():
    rows = [row(f"base{d}", "A", f"C{d}", d + 3.5) for d in range(25)]  # ~1/day baseline for A
    rows += [row(f"b{d}", "B", f"D{d}", d + 3.5) for d in range(25)]  # ~1/day baseline for B
    rows += [row(f"spike{i}", "A", f"S{i}", 0.5 + (i % 3) * 0.7) for i in range(15)]  # 15 in last 3 days
    rows += [row(f"bs{i}", "B", f"E{i}", 1.0 + i) for i in range(2)]
    res = detect_emerging_issues(frame(rows))
    flags = {i["issue_id"]: i for i in res["issues"]}
    assert res["limited_data"] is False
    assert flags["A"]["is_emerging"] and not flags["B"]["is_emerging"]


def test_undated_upload_does_not_shift_trend_window():
    """An item without a timestamp gets the upload time; that must not move 'now' past a real spike."""
    rows = [row(f"base{d}", "A", f"C{d}", d + 3.5) for d in range(25)]
    rows += [row(f"b{d}", "B", f"D{d}", d + 3.5) for d in range(25)]
    rows += [row(f"spike{i}", "A", f"S{i}", 0.5 + (i % 3) * 0.7) for i in range(15)]
    df = frame(rows)
    df["timestamp_inferred"] = 0
    late = frame([row("undated", "B", "Z1", -5.0)])  # 5 days "after" the data: upload time
    late["timestamp_inferred"] = 1
    res = detect_emerging_issues(pd.concat([df, late], ignore_index=True))
    flags = {i["issue_id"]: i for i in res["issues"]}
    assert flags["A"]["is_emerging"]
    assert res["excluded_inferred_timestamps"] == 1
    assert res["as_of"] == df["timestamp"].max().isoformat()


def test_volume_over_time_fills_gaps():
    df = frame([row("a", "A", "C1", 0), row("b", "A", "C1", 3, incident="inc-a")])
    series = volume_over_time(df)
    assert len(series) == 4
    assert sum(p["reports"] for p in series) == 2
    assert series[1]["reports"] == 0
