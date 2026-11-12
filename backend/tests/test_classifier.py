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
