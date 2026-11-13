"""Complaint classification and cluster labeling.

Primary path: OpenAI gpt-4o-mini with structured outputs (Pydantic schemas).
Fallback path: deterministic keyword rules + TF-IDF keywords, used when no API key is
configured or the API fails. Every result records its ``source`` ("llm" or "fallback").

The LLM is never used for counting/aggregation and is never asked about demographics.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Literal, Sequence

from pydantic import BaseModel, Field, ValidationError

from app.database.models import CATEGORIES, Severity

logger = logging.getLogger(__name__)

CategoryLiteral = Literal[
    "Payments", "Billing", "Refunds", "Delivery", "Product Quality", "Account Access", "Account Status",
    "Customer Support", "Subscription", "App Stability", "Other",
]
SeverityLiteral = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]


# ----------------------------------------------------------------- schemas
class Entity(BaseModel):
    type: Literal["order_id", "transaction_id", "product", "amount", "payment_method", "feature", "other"]
    value: str


class ComplaintClassification(BaseModel):
    index: int = Field(description="Index of the complaint in the request list")
    category: CategoryLiteral
    severity: SeverityLiteral
    summary: str = Field(description="One-sentence neutral summary of the complaint")
    entities: list[Entity]
    confidence: float = Field(description="0-1 confidence in category and severity")
    evidence: str = Field(description="Short quote(s) from the complaint justifying the severity")


class ComplaintClassificationBatch(BaseModel):
    results: list[ComplaintClassification]


class ClusterLabel(BaseModel):
    title: str = Field(description="Short issue title, max 8 words")
    description: str = Field(description="2-3 sentences describing the shared problem, facts only")
    category: CategoryLiteral
    common_symptoms: list[str] = Field(description="Observed symptoms stated in the complaints (facts)")
    root_cause_hypotheses: list[str] = Field(description="POSSIBLE causes; unverified hypotheses")
    supporting_complaint_ids: list[str] = Field(description="IDs of sample complaints that support the label")


# --------------------------------------------------------- fallback rules
_CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("Refunds", ["refund", "money back", "reimburse", "returned item", "return was"]),
    ("Billing", ["billed", "bill ", "invoice", "overcharg", "charged twice", "double charg", "wrong amount",
                 "incorrect charge", "extra charge", "charged more", "statement"]),
    ("Subscription", ["subscription", "cancel my plan", "unsubscribe", "auto-renew", "renewal", "membership", "cancel"]),
    ("Payments", ["payment", "card declined", "declined", "checkout", "transaction failed", "pay ", "upi", "gateway"]),
    ("Delivery", ["deliver", "shipping", "shipment", "package", "courier", "tracking", "parcel", "arrive"]),
    ("Product Quality", ["damaged", "broken", "defective", "cracked", "torn", "wrong item", "missing parts", "quality"]),
    ("Account Status", ["suspended", "banned", "deactivated", "blocked my account", "account locked", "disabled"]),
    ("Account Access", ["login", "log in", "sign in", "password", "otp", "verification code", "2fa", "locked out"]),
    ("App Stability", ["crash", "freez", "app closes", "force close", "keeps closing", "blank screen", "bug", "not loading"]),
    ("Customer Support", ["support", "agent", "customer service", "no response", "rude", "on hold", "hung up", "ticket"]),
]

_SEVERITY_RULES: list[tuple[Severity, list[str]]] = [
    (Severity.CRITICAL, ["fraud", "unauthori", "stolen", "hacked", "identity theft", "security breach", "injur", "fire",
                         "smoke", "lawsuit", "lawyer", "all my savings", "rent money", "medical", "emergency",
                         "someone else accessed"]),
    (Severity.HIGH, ["charged twice", "double charg", "money deducted", "deducted", "lost my money", "can't access",
                     "cannot access", "locked out", "suspended", "weeks", "still no refund", "never arrived",
                     "unacceptable", "furious", "overcharg", "lost all", "business", "urgent", "can't log", "cannot log"]),
    (Severity.MEDIUM, ["delay", "late", "crash", "not working", "failed", "damaged", "broken", "waiting", "days",
                       "wrong", "declined", "error"]),
    (Severity.LOW, ["minor", "slightly", "small", "suggestion", "would be nice", "a bit", "just wanted", "slow"]),
]

_REF = re.compile(r"\b(?:ORD|TXN|INV|REF|TKT)-[A-Z0-9]{4,}\b", re.IGNORECASE)
_AMOUNT = re.compile(r"(?:[$₹€£]\s?\d[\d,]*(?:\.\d+)?|\b\d[\d,]*(?:\.\d+)?\s?(?:usd|inr|rs|dollars|rupees)\b)", re.I)


def fallback_classify(text: str) -> dict[str, Any]:
    """Deterministic keyword classifier. Low-ish confidence by design."""
    low = text.lower()
    cat_hits = {cat: sum(low.count(k) for k in kws) for cat, kws in _CATEGORY_RULES}
    best_cat, best_hits = max(cat_hits.items(), key=lambda kv: kv[1])
    category = best_cat if best_hits > 0 else "Other"
    severity, matched = Severity.LOW, []
    for sev, kws in _SEVERITY_RULES:
        hits = [k for k in kws if k in low]
        if hits:
            severity, matched = sev, hits
            break
    else:
        severity = Severity.MEDIUM  # neutral default when no cue words appear
    entities = [{"type": "order_id" if m.upper().startswith("ORD") else "transaction_id", "value": m.upper()}
                for m in _REF.findall(text)]
    entities += [{"type": "amount", "value": m} for m in _AMOUNT.findall(text)]
    confidence = min(0.35 + 0.1 * best_hits + (0.1 if matched else 0.0), 0.7)
    summary = text if len(text) <= 160 else text[:157].rsplit(" ", 1)[0] + "..."
    return {
        "category": category,
        "severity": severity.value,
        "summary": summary,
        "entities": entities,
        "confidence": round(confidence, 2),
        "evidence": f"Keyword rules matched: {', '.join(matched[:5])}" if matched else "No severity cue words; default MEDIUM",
        "source": "fallback",
    }


def fallback_cluster_label(
    keywords: list[str], member_categories: list[str], representative_texts: list[str], representative_ids: list[str],
    size: int,
) -> dict[str, Any]:
    counts = Counter(c for c in member_categories if c)
    category = counts.most_common(1)[0][0] if counts else "Other"
    kw = [k for k in keywords if k][:3]
    title = f"{category}: {', '.join(kw)}" if kw else f"{category} complaints"
    share = (counts[category] / sum(counts.values())) if counts else 0.0
    description = (
        f"Automatically grouped cluster of {size} semantically similar complaints. "
        f"Distinctive terms: {', '.join(keywords[:6]) or 'n/a'}. "
        f"{share:.0%} of classified members fall under '{category}'. (Label generated without LLM.)"
    )
    return {
        "title": title[:120],
        "description": description,
        "category": category,
        "symptoms": [t if len(t) <= 160 else t[:157] + "..." for t in representative_texts[:3]],
        "root_cause_hypotheses": [],
        "supporting_complaint_ids": representative_ids[:5],
        "label_source": "fallback",
    }


# --------------------------------------------------------------- LLM client
class LLMUnavailable(RuntimeError):
    pass


class LLMClassifier:
    """gpt-4o-mini wrapper with persistent response caching, parallel requests and graceful fallback.

    Requests run on a small thread pool (``concurrency``). The first request of every run is sent
    alone as a probe, so a missing/broken API key costs one call, not ``concurrency`` calls. After
    any failure, requests that have not started yet are skipped and fall back to the rules.
    Cache reads/writes only happen on the calling thread; worker threads only talk to OpenAI.
    """

    def __init__(self, cache: Any, api_key: str | None, model: str = "gpt-4o-mini", timeout: float = 30.0,
                 enabled: bool = True, batch_size: int = 10, concurrency: int = 6):
        # ``cache`` is anything with cache_get/cache_put: LLMCache in the app, Repository in tests.
        self.cache = cache
        self.model = model
        self.batch_size = max(1, batch_size)
        self.concurrency = max(1, concurrency)
        self.client = None
        self.last_error: str | None = None
        if enabled and api_key:
            from openai import OpenAI

            self.client = OpenAI(api_key=api_key, timeout=timeout, max_retries=2)

    @property
    def available(self) -> bool:
        return self.client is not None

    def _key(self, kind: str, payload: str) -> str:
        return hashlib.sha256(f"{kind}|{self.model}|{payload}".encode()).hexdigest()

    def _parse(self, system: str, user: str, schema: type[BaseModel]) -> BaseModel:
        if self.client is None:
            raise LLMUnavailable("OpenAI API key not configured")
        try:
            completion = self.client.chat.completions.parse(
                model=self.model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format=schema,
                temperature=0,
            )
        except Exception as exc:  # network/auth/rate-limit: surface as unavailable, caller falls back
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.warning("LLM call failed: %s", self.last_error)
            raise LLMUnavailable(self.last_error) from exc
        msg = completion.choices[0].message
        if getattr(msg, "refusal", None) or msg.parsed is None:
            raise LLMUnavailable(f"LLM refused or returned no parsed content: {getattr(msg, 'refusal', None)}")
        return msg.parsed

    def _run_parallel(self, jobs: Sequence[Any], fn: Callable[[Any], Any]) -> list[Any]:
        """Run ``fn(job)`` for every job and return results in job order (None where skipped or failed).
        The first job runs alone as a probe; the rest run on the thread pool. ``LLMUnavailable`` from
        any job stops jobs that have not started yet."""
        results: list[Any] = [None] * len(jobs)
        if not jobs:
            return results
        try:
            results[0] = fn(jobs[0])
        except LLMUnavailable:
            return results
        stop = threading.Event()

        def run(i: int) -> tuple[int, Any]:
            if stop.is_set():
                return i, None
            try:
                return i, fn(jobs[i])
            except LLMUnavailable:
                stop.set()
                return i, None

        if len(jobs) > 1:
            with ThreadPoolExecutor(max_workers=min(self.concurrency, len(jobs) - 1), thread_name_prefix="llm") as ex:
                for i, res in ex.map(run, range(1, len(jobs))):
                    results[i] = res
        return results

    # ------------------------------------------------ complaint classification
    def classify_complaints(self, complaints: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        """Classify complaints ({complaint_id, normalized_text}). Uses cache, then the LLM in parallel
        batches, then the rule-based fallback for anything the LLM could not handle."""
        results: dict[str, dict[str, Any]] = {}
        pending: list[dict[str, Any]] = []
        for c in complaints:
            # With the LLM disabled, cached LLM answers are not used either: "disabled" must mean
            # the deterministic rules, so results stay reproducible and honestly labeled.
            cached = self.cache.cache_get(self._key("classify", c["normalized_text"])) if self.available else None
            if cached:
                results[c["complaint_id"]] = {**json.loads(cached), "complaint_id": c["complaint_id"]}
            else:
                pending.append(c)

        if self.available and pending:
            chunks = [pending[i : i + self.batch_size] for i in range(0, len(pending), self.batch_size)]
            t0 = time.monotonic()
            answered = 0
            for out in self._run_parallel(chunks, self._classify_chunk):
                for c, data in out or []:
                    self.cache.cache_put(self._key("classify", c["normalized_text"]), "classify", self.model,
                                         json.dumps(data))
                    results[c["complaint_id"]] = {**data, "complaint_id": c["complaint_id"]}
                    answered += 1
            logger.info("LLM classified %d/%d uncached complaints in %d call(s), %.1fs (%d served from cache)",
                        answered, len(pending), len(chunks), time.monotonic() - t0, len(complaints) - len(pending))

        for c in complaints:
            if c["complaint_id"] not in results:
                results[c["complaint_id"]] = {**fallback_classify(c["normalized_text"]), "complaint_id": c["complaint_id"]}
        return [results[c["complaint_id"]] for c in complaints]

    def _classify_chunk(self, chunk: Sequence[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        """One API call for a chunk. Runs on a worker thread: no database access here."""
        system = (
            "You classify customer complaints for a support analytics team. For each complaint return category, "
            "severity, a one-sentence summary, explicit entities (order/transaction IDs, products, amounts, payment "
            "methods, features) and short evidence quotes. Severity guide: CRITICAL = fraud, security, safety, legal "
            "threat, or severe financial harm; HIGH = money lost/charged incorrectly, account inaccessible, repeated "
            "unresolved problem; MEDIUM = service failure or delay with moderate impact; LOW = minor inconvenience or "
            "feedback. Never infer or mention demographics (age, income, student status, location) of the customer. "
            "Only use information present in the text."
        )
        lines = [f"[{i}] {c['normalized_text'][:1500]}" for i, c in enumerate(chunk)]
        parsed = self._parse(system, "Complaints:\n" + "\n".join(lines), ComplaintClassificationBatch)
        out: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for item in parsed.results:  # type: ignore[attr-defined]
            if not 0 <= item.index < len(chunk):
                continue
            out.append((chunk[item.index], {
                "category": item.category,
                "severity": item.severity,
                "summary": item.summary,
                "entities": [e.model_dump() for e in item.entities],
                "confidence": max(0.0, min(1.0, item.confidence)),
                "evidence": item.evidence,
                "source": "llm",
            }))
        return out

    # ---------------------------------------------------- cluster labeling
    def _label_key(self, samples: Sequence[dict[str, Any]], keywords: list[str], size: int) -> str:
        # Content only (texts, keywords, size), never complaint IDs: IDs are new UUIDs on every
        # upload, so an ID-based key would never hit after the application is reset.
        payload = json.dumps({"texts": [s["text"][:800] for s in samples], "kw": keywords, "size": size})
        return self._key("label_v2", payload)

    @staticmethod
    def _bind_label(data: dict[str, Any], samples: Sequence[dict[str, Any]]) -> dict[str, Any]:
        """Turn a cached label (supporting samples stored by position) into one citing current IDs."""
        out = {k: v for k, v in data.items() if k != "supporting_sample_indices"}
        out["supporting_complaint_ids"] = [samples[i]["complaint_id"] for i in data.get("supporting_sample_indices", [])
                                           if 0 <= i < len(samples)]
        return out

    def label_cluster(self, samples: Sequence[dict[str, Any]], keywords: list[str], size: int) -> dict[str, Any] | None:
        """Label one cluster from representative samples ({complaint_id, text}). Returns None if the LLM
        is unavailable (caller then uses ``fallback_cluster_label``)."""
        return self.label_clusters([(samples, keywords, size)])[0]

    def label_clusters(
        self, requests: Sequence[tuple[Sequence[dict[str, Any]], list[str], int]]
    ) -> list[dict[str, Any] | None]:
        """Label many clusters ((samples, keywords, size) each), uncached ones in parallel.
        Returns one label (or None = use the fallback) per request, in order."""
        out: list[dict[str, Any] | None] = [None] * len(requests)
        if not self.available:
            return out
        todo: list[tuple[int, str, Sequence[dict[str, Any]], list[str], int]] = []
        for i, (samples, keywords, size) in enumerate(requests):
            key = self._label_key(samples, keywords, size)
            cached = self.cache.cache_get(key)
            if cached:
                out[i] = self._bind_label(json.loads(cached), samples)
            else:
                todo.append((i, key, samples, keywords, size))
        results = self._run_parallel(todo, lambda job: self._label_call(job[2], job[3], job[4]))
        for (i, key, samples, _, _), data in zip(todo, results):
            if data is None:
                continue
            self.cache.cache_put(key, "label", self.model, json.dumps(data))
            out[i] = self._bind_label(data, samples)
        return out

    def _label_call(self, samples: Sequence[dict[str, Any]], keywords: list[str], size: int) -> dict[str, Any] | None:
        """One API call for one cluster. Runs on a worker thread: no database access here."""
        system = (
            "You name recurring customer issues discovered by clustering. Given representative complaints from one "
            "cluster, produce a concise title, a factual description, a category, the common symptoms customers "
            "explicitly report (confirmed facts), and separately a few POSSIBLE root-cause hypotheses that are clearly "
            "speculative. Only cite complaint IDs from the provided samples. Never mention customer demographics."
        )
        user = (
            f"Cluster size: {size} complaints. Distinctive keywords: {', '.join(keywords)}.\n"
            "Representative complaints (id: text):\n"
            + "\n".join(f"- {s['complaint_id']}: {s['text'][:800]}" for s in samples)
        )
        try:
            label: ClusterLabel = self._parse(system, user, ClusterLabel)  # type: ignore[assignment]
        except ValidationError as exc:
            logger.warning("Cluster label failed validation: %s", exc)
            return None
        cited = set(label.supporting_complaint_ids)
        return {
            "title": label.title[:120],
            "description": label.description,
            "category": label.category if label.category in CATEGORIES else "Other",
            "symptoms": label.common_symptoms[:8],
            "root_cause_hypotheses": label.root_cause_hypotheses[:6],
            # Unknown IDs the model invented are dropped here.
            "supporting_sample_indices": [i for i, s in enumerate(samples) if s["complaint_id"] in cited],
            "label_source": "llm",
        }
