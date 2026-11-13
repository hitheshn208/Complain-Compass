"""API tests. Plumbing tests use the fast hashing embedder; the end-to-end test uses the real model."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

DATA = Path(__file__).resolve().parents[1] / "data" / "sample_complaints.csv"

SMALL_CSV = """complaint_text,customer_id,source,timestamp,country,state,age_group,student_status,income_bracket
My refund for ORD-100001 has not arrived after two weeks,C1,email,2026-10-01T10:00:00Z,India,Delhi,18-24,yes,<25k
Following up on ORD-100001 refund still not received,C1,chat,2026-10-02T10:00:00Z,,,,,
Refund not received for my returned order,C2,email,2026-10-03T10:00:00Z,India,Karnataka,25-34,no,25k-50k
Still waiting for refund money for return,C3,voice,2026-10-04T10:00:00Z,India,Delhi,25-34,no,25k-50k
Where is my refund money for the return,C4,chat,2026-10-05T10:00:00Z,United States,Texas,35-44,no,100k+
App crashes every time when I open cart,C5,social_media,2026-10-05T10:00:00Z,India,Delhi,18-24,yes,<25k
The app crashes when opening the cart,C6,email,2026-10-06T10:00:00Z,India,Delhi,18-24,yes,<25k
app crashes on opening cart screen,C7,chat,2026-10-06T12:00:00Z,India,Delhi,18-24,yes,<25k
cart screen crashes the app,,chat,2026-10-07T12:00:00Z,,,,,
,C8,email,2026-10-07T12:00:00Z,India,,,,
Delivery is late,C9,fax,2026-10-07T12:00:00Z,India,,,,
"""


@pytest.fixture
def client(fake_settings):
    with TestClient(create_app(fake_settings)) as c:
        yield c


def upload(client, content: str | bytes, name="complaints.csv", **params):
    data = content.encode() if isinstance(content, str) else content
    return client.post("/api/v1/complaints/upload", files={"file": (name, data, "text/csv")}, params=params)


def test_upload_processes_batch_and_reports_row_errors(client):
    r = upload(client, SMALL_CSV)
    assert r.status_code == 201, r.text
    body = r.json()["data"]
    assert body["total_rows"] == 11 and body["accepted_rows"] == 9 and body["rejected_rows"] == 2
    assert {e["row_number"] for e in body["row_errors"]} == {10, 11}
    batch = client.get(f"/api/v1/batches/{body['batch_id']}").json()["data"]
    assert batch["status"] == "PROCESSED" and batch["progress"] == 1.0
    assert len(batch["row_errors"]) == 2


def test_duplicate_upload_returns_409(client):
    assert upload(client, SMALL_CSV).status_code == 201
    r = upload(client, SMALL_CSV, name="again.csv")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "duplicate_file"


def test_upload_validation_errors(client):
    assert upload(client, "text\nhello\n").status_code == 422
    assert upload(client, "complaint_text\nhello there\n", name="x.txt").status_code == 400
    assert upload(client, b"").status_code == 400


def test_complaints_listing_detail_and_similarity(client):
    upload(client, SMALL_CSV)
    r = client.get("/api/v1/complaints", params={"page_size": 5})
    meta = r.json()["meta"]
    assert meta["total"] == 9 and meta["pages"] == 2 and len(r.json()["data"]) == 5
    cid = r.json()["data"][0]["complaint_id"]
    d = client.get(f"/api/v1/complaints/{cid}").json()["data"]
    assert d["original_text"] and d["incident_id"]
    sim = client.get(f"/api/v1/complaints/{cid}/similar", params={"top_k": 3}).json()["data"]
    assert len(sim) == 3 and all(s["complaint_id"] != cid for s in sim)
    assert client.get("/api/v1/complaints/nope").status_code == 404
    filtered = client.get("/api/v1/complaints", params={"source": "CHAT"}).json()
    assert filtered["meta"]["total"] == 4 and all(x["source"] == "CHAT" for x in filtered["data"])


def test_cross_channel_duplicate_counted_once(client):
    upload(client, SMALL_CSV)
    rows = client.get("/api/v1/complaints", params={"search": "ORD-100001"}).json()["data"]
    assert len(rows) == 2
    assert rows[0]["incident_id"] == rows[1]["incident_id"]
    ov = client.get("/api/v1/analytics/overview").json()["data"]
    assert ov["total_reports"] == 9
    assert ov["duplicate_reports"] >= 1
    # 7 distinct verified customers (C1..C7) + 1 anonymous complaint
    assert ov["unique_affected_customers"] == 8 and ov["verified_customers"] == 7


def test_issues_endpoints_and_patch(client):
    upload(client, SMALL_CSV)
    issues = client.get("/api/v1/issues").json()["data"]
    assert len(issues) >= 1
    top = client.get("/api/v1/issues/top10").json()
    scores = [i["priority_score"] for i in top["data"]]
    assert scores == sorted(scores, reverse=True) and len(scores) <= 10
    assert abs(sum(top["meta"]["weights"].values()) - 1) < 1e-9
    iid = issues[0]["issue_id"]
    detail = client.get(f"/api/v1/issues/{iid}").json()["data"]
    for key in ("breakdown", "explanation", "representative_complaints", "trend", "demographics", "evidence"):
        assert key in detail
    comps = detail["breakdown"]["components"]
    assert set(comps) == {"impact", "severity", "recurrence", "growth", "business"}
    r = client.patch(f"/api/v1/issues/{iid}", json={"title": "Cart crash after update", "status": "INVESTIGATING"})
    assert r.status_code == 200 and r.json()["data"]["label_source"] == "manual"
    assert client.patch(f"/api/v1/issues/{iid}", json={"status": "BOGUS"}).status_code == 422
    assert client.patch("/api/v1/issues/missing", json={"title": "abc"}).status_code == 404
    # manual label survives reclustering
    assert client.post("/api/v1/pipeline/recluster").status_code == 202
    titles = {i["title"] for i in client.get("/api/v1/issues").json()["data"]}
    assert "Cart crash after update" in titles


def test_analytics_endpoints_and_filters(client):
    upload(client, SMALL_CSV)
    tr = client.get("/api/v1/analytics/trends").json()["data"]
    assert sum(p["reports"] for p in tr["series"]) == 9
    assert tr["emerging"]["limited_data"] is True
    seg = client.get("/api/v1/analytics/segments", params={"dimension": "country"}).json()["data"]
    country = seg["segments"]["country"]
    assert country["metric_type"] == "count"
    india = {s["segment"]: s for s in country["segments"]}["India"]
    assert india["unique_customers"] == 6
    only_us = client.get("/api/v1/analytics/overview", params={"country": "United States"}).json()["data"]
    assert only_us["total_reports"] == 1 and only_us["filtered"] is True
    combo = client.get("/api/v1/analytics/overview", params={"country": "India", "age_group": "18-24",
                                                             "source": "EMAIL"}).json()["data"]
    assert combo["total_reports"] == 2  # C1 email + C6 email
    assert client.get("/api/v1/analytics/segments", params={"dimension": "bogus"}).status_code == 422
    opts = client.get("/api/v1/analytics/filters").json()["data"]
    assert "India" in opts["country"]


def test_population_upload_enables_rates(client):
    upload(client, SMALL_CSV)
    r = client.post("/api/v1/analytics/populations",
                    files={"file": ("p.csv", b"dimension,value,population\ncountry,India,1000\ncountry,X,-1\n", "text/csv")})
    assert r.json()["data"]["accepted"] == 1 and len(r.json()["data"]["errors"]) == 1
    seg = client.get("/api/v1/analytics/segments", params={"dimension": "country"}).json()["data"]["segments"]["country"]
    assert seg["metric_type"] == "rate"
    india = {s["segment"]: s for s in seg["segments"]}["India"]
    assert india["affected_rate_per_1000"] == 6.0


def test_pipeline_endpoints(client):
    body = upload(client, SMALL_CSV, auto_process="false").json()["data"]
    assert body["processing_started"] is False
    assert client.get(f"/api/v1/batches/{body['batch_id']}").json()["data"]["status"] == "UPLOADED"
    assert client.post(f"/api/v1/pipeline/process/{body['batch_id']}").status_code == 202
    assert client.get(f"/api/v1/batches/{body['batch_id']}").json()["data"]["status"] == "PROCESSED"
    assert client.post("/api/v1/pipeline/process/does-not-exist").status_code == 404
    st = client.get("/api/v1/pipeline/status").json()
    assert st["meta"]["counts"]["complaints"] == 9 and st["data"]["running"] is False


@pytest.mark.skipif(not DATA.exists(), reason="sample dataset not generated")
def test_end_to_end_sample_dataset_real_embeddings(settings):
    """Full pipeline on the 600+ row synthetic dataset with the real MiniLM model."""
    with TestClient(create_app(settings)) as client:
        r = upload(client, DATA.read_bytes(), name="sample_complaints.csv")
        assert r.status_code == 201
        assert r.json()["data"]["accepted_rows"] >= 500
        batch = client.get(f"/api/v1/batches/{r.json()['data']['batch_id']}").json()["data"]
        assert batch["status"] == "PROCESSED", batch["error_message"]
        ov = client.get("/api/v1/analytics/overview").json()["data"]
        assert ov["issue_clusters"] >= 8
        assert ov["unique_affected_customers"] < ov["total_reports"]
        assert ov["duplicate_reports"] > 0
        top = client.get("/api/v1/issues/top10").json()["data"]
        assert len(top) == 10
        tr = client.get("/api/v1/analytics/trends").json()["data"]
        assert tr["emerging"]["limited_data"] is False
        assert any(i["is_emerging"] for i in tr["emerging"]["issues"])


def test_small_upload_groups_issues_by_category(client):
    rows = ["complaint_text,customer_id,timestamp"]
    texts = [
        "My package has not been delivered, tracking stuck for 10 days",
        "The courier keeps rescheduling my delivery, it is very late",
        "Order never arrived, shipping is delayed again",
        "I was charged twice on my invoice for the same order",
        "My bill shows an extra charge I did not agree to",
        "The app crashes every time I open it",
    ]
    for i, t in enumerate(texts):
        rows.append(f'"{t}",CUST-{i},2026-09-0{i + 1}T10:00:00Z')
    assert upload(client, "\n".join(rows) + "\n", name="small.csv").status_code == 201
    o = client.get("/api/v1/analytics/overview").json()["data"]
    assert o["grouping_mode"] == "category" and o["processed_complaints"] == 6
    issues = client.get("/api/v1/issues").json()["data"]
    cats = sorted(i["category"] for i in issues)
    assert cats == ["Billing", "Delivery"]  # the single App Stability complaint stays unassigned
    assert client.get("/api/v1/issues/top10").json()["data"]


def test_all_rejected_upload_is_closed_and_can_be_reuploaded(client):
    posts = b'{"post_id": "p1", "text": "App keeps crashing", "created_at": "2026-09-01T10:00:00Z"}\n' \
            b'{"post_id": "p2", "text": "Refund still missing", "created_at": "2026-09-02T10:00:00Z"}\n'
    files = {"files": ("social_posts.jsonl", posts, "application/json")}
    r = client.post("/api/v1/ingest/chat", files=files)  # wrong tab: posts are not conversations
    assert r.status_code == 201, r.text
    body = r.json()["data"]
    assert body["accepted_rows"] == 0 and body["processing_started"] is False
    batch = client.get(f"/api/v1/batches/{body['batch_id']}").json()["data"]
    assert batch["status"] == "FAILED"  # not left UPLOADED, so the UI stops polling
    assert "right tab" in batch["error_message"] and "message list" in batch["error_message"]
    # Same file, same tab again: nothing was imported the first time, so no 409.
    again = client.post("/api/v1/ingest/chat", files=files)
    assert again.status_code == 201
    assert len([b for b in client.get("/api/v1/batches").json()["data"] if b["filename"] == "social_posts.jsonl"]) == 1
