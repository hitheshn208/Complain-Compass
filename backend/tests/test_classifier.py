import os
from types import SimpleNamespace

import pytest

from app.inference.classifier import (ClusterLabel, ComplaintClassification, ComplaintClassificationBatch,
                                      LLMClassifier, fallback_classify, fallback_cluster_label)


def test_fallback_classification_schema():
    r = fallback_classify("I was charged twice for order ORD-123456, please refund $49")
    assert r["category"] in {"Billing", "Refunds"}
    assert r["severity"] == "HIGH"
    assert r["source"] == "fallback" and 0 <= r["confidence"] <= 1
    assert {"type": "order_id", "value": "ORD-123456"} in r["entities"]
    assert fallback_classify("unauthorized charges, this is fraud")["severity"] == "CRITICAL"
    assert fallback_classify("hello")["category"] == "Other"


def test_fallback_cluster_label_never_invents_root_causes():
    lab = fallback_cluster_label(["refund", "days"], ["Refunds", "Refunds", "Billing"], ["Where is my refund"],
                                 ["id1"], 3)
    assert lab["category"] == "Refunds" and lab["root_cause_hypotheses"] == []
    assert lab["label_source"] == "fallback"


def test_classifier_without_key_uses_fallback(repo):
    clf = LLMClassifier(repo, api_key=None)
    assert not clf.available
    out = clf.classify_complaints([{"complaint_id": "x", "normalized_text": "The app crashes on launch"}])
    assert out[0]["source"] == "fallback" and out[0]["category"] == "App Stability"
    assert clf.label_cluster([{"complaint_id": "x", "text": "t"}], ["kw"], 5) is None


class FakeCompletions:
    def __init__(self, parsed=None, exc=None):
        self.parsed, self.exc, self.calls = parsed, exc, 0

    def parse(self, **kwargs):
        self.calls += 1
        if self.exc:
            raise self.exc
        msg = SimpleNamespace(parsed=self.parsed, refusal=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def _with_fake_client(repo, completions):
    clf = LLMClassifier(repo, api_key=None)
    clf.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return clf


def test_llm_results_are_validated_and_cached(repo):
    parsed = ComplaintClassificationBatch(results=[ComplaintClassification(
        index=0, category="Payments", severity="HIGH", summary="Payment failed", entities=[], confidence=0.9,
        evidence="'money was deducted'")])
    fake = FakeCompletions(parsed=parsed)
    clf = _with_fake_client(repo, fake)
    items = [{"complaint_id": "x", "normalized_text": "Payment failed, money was deducted"}]
    out = clf.classify_complaints(items)
    assert out[0]["source"] == "llm" and out[0]["severity"] == "HIGH"
    out2 = clf.classify_complaints([{"complaint_id": "y", "normalized_text": "Payment failed, money was deducted"}])
    assert fake.calls == 1  # served from cache
    assert out2[0]["complaint_id"] == "y" and out2[0]["source"] == "llm"


def test_llm_failure_falls_back_gracefully(repo):
    fake = FakeCompletions(exc=RuntimeError("network down"))
    clf = _with_fake_client(repo, fake)
    out = clf.classify_complaints([{"complaint_id": str(i), "normalized_text": "refund not received"}
                                   for i in range(45)])
    assert all(o["source"] == "fallback" for o in out)
    assert fake.calls == 1  # stops calling a failing API
    assert "network down" in clf.last_error


def test_cluster_label_filters_unknown_supporting_ids(repo):
    label = ClusterLabel(title="Refund delays", description="Refunds late", category="Refunds",
                         common_symptoms=["refund not received"], root_cause_hypotheses=["Possible bank batch delay"],
                         supporting_complaint_ids=["a", "invented"])
    clf = _with_fake_client(repo, FakeCompletions(parsed=label))
    out = clf.label_cluster([{"complaint_id": "a", "text": "refund late"}], ["refund"], 10)
    assert out["supporting_complaint_ids"] == ["a"] and out["label_source"] == "llm"
    assert repo.conn.execute("SELECT COUNT(*) FROM llm_cache WHERE kind = 'label'").fetchone()[0] == 1


@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
def test_real_openai_classification(repo):
    clf = LLMClassifier(repo, api_key=os.environ["OPENAI_API_KEY"])
    out = clf.classify_complaints([
        {"complaint_id": "a", "normalized_text": "Someone made unauthorized purchases on my account, this is fraud"},
        {"complaint_id": "b", "normalized_text": "Would be nice to have dark mode"},
    ])
    assert out[0]["source"] == "llm", clf.last_error
    assert out[0]["severity"] in {"CRITICAL", "HIGH"}
    assert out[1]["severity"] in {"LOW", "MEDIUM"}


# ------------------------------------------------- parallel batches + persistent cache
import re as _re
import threading as _threading
import time as _time

from app.database.models import BatchStatus
from app.inference.llm_cache import LLMCache


class EchoCompletions:
    """Answers every complaint in the request; records how many calls overlap in time."""

    def __init__(self, delay=0.05, fail_after=None):
        self.delay, self.fail_after = delay, fail_after
        self.calls = self.active = self.max_active = 0
        self.lock = _threading.Lock()

    def parse(self, **kwargs):
        with self.lock:
            self.calls += 1
            n = self.calls
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            _time.sleep(self.delay)
            if self.fail_after is not None and n > self.fail_after:
                raise RuntimeError("rate limited")
            idx = [int(m) for m in _re.findall(r"^\[(\d+)\]", kwargs["messages"][1]["content"], _re.M)]
            parsed = ComplaintClassificationBatch(results=[ComplaintClassification(
                index=i, category="Refunds", severity="HIGH", summary="s", entities=[], confidence=0.8, evidence="e")
                for i in idx])
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed, refusal=None))])
        finally:
            with self.lock:
                self.active -= 1


def _items(n, prefix="c"):
    return [{"complaint_id": f"{prefix}{i}", "normalized_text": f"refund {i} not received"} for i in range(n)]


def test_classification_batches_run_in_parallel_and_are_cached(tmp_path):
    cache = LLMCache(tmp_path / "llm.db")
    fake = EchoCompletions()
    clf = _with_fake_client(cache, fake)
    clf.batch_size, clf.concurrency = 10, 4
    out = clf.classify_complaints(_items(95))
    assert [o["complaint_id"] for o in out] == [f"c{i}" for i in range(95)]  # order preserved
    assert all(o["source"] == "llm" for o in out)
    assert fake.calls == 10 and fake.max_active > 1
    assert cache.count() == 95
    # Same texts under new complaint IDs (what a re-upload after a reset looks like): no API calls.
    again = clf.classify_complaints(_items(95, prefix="new"))
    assert fake.calls == 10 and all(o["source"] == "llm" for o in again)


def test_failing_api_is_probed_once_then_parallel_failures_stop(tmp_path):
    fake = EchoCompletions(fail_after=0)  # every call fails
    clf = _with_fake_client(LLMCache(tmp_path / "llm.db"), fake)
    clf.batch_size, clf.concurrency = 10, 6
    out = clf.classify_complaints(_items(100))
    assert fake.calls == 1 and all(o["source"] == "fallback" for o in out)

    fake = EchoCompletions(fail_after=3, delay=0.02)  # API breaks mid-run
    clf = _with_fake_client(LLMCache(tmp_path / "llm2.db"), fake)
    clf.batch_size, clf.concurrency = 10, 2
    out = clf.classify_complaints(_items(200))
    assert fake.calls < 20  # stopped early instead of calling all 20 batches
    sources = {o["source"] for o in out}
    assert sources == {"llm", "fallback"}  # good answers kept, rest falls back


def test_label_cache_survives_new_complaint_ids(tmp_path):
    label = ClusterLabel(title="Refund delays", description="d", category="Refunds", common_symptoms=["s"],
                         root_cause_hypotheses=["h"], supporting_complaint_ids=["a2", "invented"])
    fake = FakeCompletions(parsed=label)
    clf = _with_fake_client(LLMCache(tmp_path / "llm.db"), fake)
    first = [{"complaint_id": "a1", "text": "refund late"}, {"complaint_id": "a2", "text": "no refund yet"}]
    second = [{"complaint_id": "b1", "text": "refund late"}, {"complaint_id": "b2", "text": "no refund yet"}]
    out = clf.label_clusters([(first, ["refund"], 10), (first, ["other"], 3)])
    assert fake.calls == 2 and out[0]["supporting_complaint_ids"] == ["a2"]
    again = clf.label_cluster(second, ["refund"], 10)
    assert fake.calls == 2  # cache hit despite different IDs
    assert again["title"] == "Refund delays" and again["supporting_complaint_ids"] == ["b2"]


def test_legacy_cache_rows_move_to_cache_file(repo, tmp_path):
    repo.cache_put("k1", "classify", "gpt-4o-mini", "{}")
    cache = LLMCache(tmp_path / "llm.db")
    assert cache.import_from(repo.conn) == 1 and cache.cache_get("k1") == "{}"
    assert repo.cache_get("k1") is None
    assert cache.import_from(repo.conn) == 0


def test_interrupted_batches_are_marked_failed(repo):
    repo.conn.execute(
        "INSERT INTO ingestion_batches (batch_id, filename, file_hash, total_rows, accepted_rows, rejected_rows, "
        "status, stage, progress, created_at, updated_at) VALUES ('b1','f.csv','h',1,1,0,'PROCESSING',"
        "'classification',0.4,'2026-01-01','2026-01-01')")
    repo.conn.commit()
    assert repo.fail_interrupted_batches() == 1
    b = repo.get_batch("b1")
    assert b["status"] == BatchStatus.FAILED.value and "classification" in b["error_message"]
    assert repo.fail_interrupted_batches() == 0


def test_disabled_llm_ignores_cached_llm_answers(tmp_path):
    cache = LLMCache(tmp_path / "llm.db")
    clf = _with_fake_client(cache, EchoCompletions())
    clf.classify_complaints(_items(3))
    assert cache.count() == 3
    off = LLMClassifier(cache, api_key=None)
    assert all(o["source"] == "fallback" for o in off.classify_complaints(_items(3)))
