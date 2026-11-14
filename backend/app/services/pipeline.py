"""Central processing pipeline orchestrator.

Stages (per batch):  preprocess -> embed -> cluster -> classify -> label -> deduplicate -> score
``recluster()`` runs the global stages (cluster..score) over all embedded complaints.

Only one pipeline run executes at a time (process-wide lock). Failures are logged with a
traceback, recorded on the batch (status FAILED + error_message) and re-raised.
"""
from __future__ import annotations

import json
import logging
import threading
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from app.analytics.prioritization import compute_issue_scores
from app.core.config import Settings
from app.database.models import BatchStatus, ComplaintStatus, IssueStatus, utcnow_iso
from app.database.repository import Repository
from app.deduplication.incident_matching import DedupConfig, deduplicate
from app.inference.classifier import LLMClassifier, fallback_cluster_label
from app.inference.clustering import ClusteringEngine, cluster_keywords, resolve_stable_ids
from app.inference.embeddings import EmbeddingEngine
from app.preprocessing.cleaner import clean_text
from app.services.priority_settings import PrioritySettings

logger = logging.getLogger(__name__)

# Classification runs before clustering: small datasets are grouped by category (see _cluster).
STAGES = ["preprocessing", "embedding", "classification", "clustering", "labeling", "deduplication", "scoring"]


@dataclass
class PipelineState:
    """In-memory status of global pipeline runs (batch status is persisted in SQLite)."""

    running: bool = False
    stage: str | None = None
    last_run_at: str | None = None
    last_error: str | None = None
    last_summary: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"running": self.running, "stage": self.stage, "last_run_at": self.last_run_at,
                "last_error": self.last_error, "last_summary": self.last_summary}


class PipelineOrchestrator:
    _lock = threading.Lock()

    def __init__(
        self,
        settings: Settings,
        repo: Repository,
        embedder: EmbeddingEngine,
        classifier: LLMClassifier,
        state: PipelineState | None = None,
        priority: PrioritySettings | None = None,
    ):
        self.settings = settings
        self.repo = repo
        self.embedder = embedder
        self.classifier = classifier
        self.state = state or PipelineState()
        self.priority = priority
        self.clusterer = ClusteringEngine(
            min_cluster_size=settings.hdbscan_min_cluster_size,
            min_samples=settings.hdbscan_min_samples,
            representatives=settings.representatives_per_cluster,
            merge_threshold=settings.cluster_merge_threshold,
        )

    # ---------------------------------------------------------------- public
    def process_batch(self, batch_id: str) -> dict[str, Any]:
        batch = self.repo.get_batch(batch_id)
        if batch is None:
            raise LookupError(f"Batch {batch_id} not found")
        with self._lock:
            self.state.running = True
            self.state.last_error = None

            def progress(stage: str) -> None:
                self.state.stage = stage
                self.repo.update_batch(batch_id, status=BatchStatus.PROCESSING, stage=stage,
                                       progress=STAGES.index(stage) / len(STAGES))

            try:
                self.repo.update_batch(batch_id, status=BatchStatus.PROCESSING, stage="preprocessing", progress=0,
                                       clear_error=True)
                summary: dict[str, Any] = {"batch_id": batch_id}
                progress("preprocessing")
                summary["preprocessing"] = self._preprocess(batch_id)
                progress("embedding")
                summary["embedding"] = self._embed()
                summary.update(self._global_stages(progress))
                self.repo.mark_batch_complaints_processed(batch_id)
                self.repo.update_batch(batch_id, status=BatchStatus.PROCESSED, stage="completed", progress=1.0)
                self.state.last_summary = summary
                return summary
            except Exception as exc:
                msg = f"{type(exc).__name__}: {exc}"
                logger.error("Pipeline failed for batch %s: %s\n%s", batch_id, msg, traceback.format_exc())
                self.state.last_error = msg
                self.repo.update_batch(batch_id, status=BatchStatus.FAILED, error_message=msg)
                raise
            finally:
                self.state.running = False
                self.state.stage = None
                self.state.last_run_at = utcnow_iso()

    def recluster(self) -> dict[str, Any]:
        with self._lock:
            self.state.running = True
            self.state.last_error = None
            try:
                summary = {"embedding": self._embed()}

                def progress(stage: str) -> None:
                    self.state.stage = stage

                summary.update(self._global_stages(progress))
                self.state.last_summary = summary
                return summary
            except Exception as exc:
                self.state.last_error = f"{type(exc).__name__}: {exc}"
                logger.error("Recluster failed: %s\n%s", self.state.last_error, traceback.format_exc())
                raise
            finally:
                self.state.running = False
                self.state.stage = None
                self.state.last_run_at = utcnow_iso()

    def rescore(self) -> dict[str, Any]:
        """Recompute priority metrics only (e.g. after a manual issue edit)."""
        return self._score()

    # ---------------------------------------------------------------- stages
    def _global_stages(self, progress: Callable[[str], None]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        progress("classification")
        out["classification"] = self._classify()
        progress("clustering")
        out["clustering"] = self._cluster()
        progress("labeling")
        out["labeling"] = self._label()
        progress("deduplication")
        out["deduplication"] = self._dedup()
        progress("scoring")
        out["scoring"] = self._score()
        return out

    def _preprocess(self, batch_id: str) -> dict[str, int]:
        rows = self.repo.complaints_by_status(batch_id, ComplaintStatus.INGESTED)
        updates = []
        empty = pii = 0
        for r in rows:
            res = clean_text(r["original_text"])
            status = ComplaintStatus.REJECTED_EMPTY if res.is_empty else ComplaintStatus.PREPROCESSED
            empty += res.is_empty
            pii += bool(res.pii_found)
            updates.append((r["complaint_id"], res.text, bool(res.pii_found), res.reference_ids, status))
        self.repo.save_preprocessed(updates)
        return {"preprocessed": len(rows), "empty_after_cleaning": empty, "pii_masked": pii}

    def _embed(self) -> dict[str, int]:
        pending = self.repo.complaints_pending_embedding()
        ids = self.embedder.generate_embeddings(pending)
        self.repo.mark_embedded(ids)
        return {"embedded": len(ids), "vector_store_total": self.embedder.store.count()}

    def _cluster(self) -> dict[str, Any]:
        ids = self.repo.embedded_complaint_ids()
        ids, mat = self.embedder.store.get_embeddings(ids)
        if len(ids) < self.settings.semantic_clustering_min_complaints:
            # Too little data for density-based clustering: one issue per complaint category.
            cats = self.repo.complaint_categories()
            result = self.clusterer.cluster_by_category(ids, mat, [cats.get(i) for i in ids])
            mode = "category"
        else:
            result = self.clusterer.cluster(ids, mat)
            mode = "semantic"

        previous_members = self.repo.cluster_members()
        previous = []
        for issue in self.repo.list_all_issues():
            cent = np.asarray(json.loads(issue["centroid"]), dtype=np.float32) if issue.get("centroid") else None
            previous.append({"issue_id": issue["issue_id"], "cluster_id": issue["cluster_id"], "centroid": cent,
                             "members": set(previous_members.get(issue["cluster_id"], []))})
        resolved = resolve_stable_ids(result, previous, centroid_threshold=self.settings.cluster_match_threshold)
        existing = {i["issue_id"]: i for i in self.repo.list_all_issues()}

        for rc in resolved:
            prev = existing.get(rc.issue_id)
            status = prev["status"] if prev and prev["status"] != IssueStatus.STALE.value else IssueStatus.OPEN.value
            self.repo.upsert_issue({
                "issue_id": rc.issue_id,
                "cluster_id": rc.cluster_id,
                "title": prev["title"] if prev else f"Unlabeled cluster {rc.cluster_id}",
                "description": prev["description"] if prev else None,
                "category": prev["category"] if prev else None,
                "severity": prev["severity"] if prev else None,
                "status": status,
                "label_source": prev["label_source"] if prev else "pending",
                "symptoms": prev["symptoms"] if prev else "[]",
                "root_cause_hypotheses": prev["root_cause_hypotheses"] if prev else "[]",
                "supporting_complaint_ids": prev["supporting_complaint_ids"] if prev else "[]",
                "representative_ids": json.dumps(rc.cluster.representative_ids),
                "keywords": prev["keywords"] if prev else "[]",
                "centroid": json.dumps([round(float(x), 6) for x in rc.cluster.centroid]),
                "label_signature": prev["label_signature"] if prev else None,
            })
        self.repo.mark_issues_stale([rc.issue_id for rc in resolved])

        rows = []
        for rc in resolved:
            for cid in rc.cluster.member_ids:
                rows.append((cid, rc.cluster_id, rc.cluster.member_confidence.get(cid, 1.0), False))
        rows += [(cid, None, 0.0, True) for cid in result.noise_ids]
        self.repo.replace_cluster_assignments(rows)
        return {
            "mode": mode,
            "points": result.n_points,
            "clusters": len(result.clusters),
            "noise": len(result.noise_ids),
            "noise_ratio": round(result.noise_ratio, 3),
            "new_issues": sum(rc.is_new for rc in resolved),
            "matched_issues": sum(not rc.is_new for rc in resolved),
        }

    def _classify(self) -> dict[str, Any]:
        todo = self.repo.unclassified_complaints()
        already_fallback = self.repo.classified_by_source("fallback")
        if not self.classifier.available:
            # Without an LLM only classify what has never been classified.
            todo = [t for t in todo if t["complaint_id"] not in already_fallback]
        else:
            todo = todo[: self.settings.llm_max_complaint_classifications]
        results = self.classifier.classify_complaints(todo)
        self.repo.save_classifications(results)
        by_source: dict[str, int] = {}
        for r in results:
            by_source[r["source"]] = by_source.get(r["source"], 0) + 1
        return {"classified": len(results), "by_source": by_source, "llm_available": self.classifier.available,
                "llm_error": self.classifier.last_error}

    def _label(self) -> dict[str, Any]:
        issues = [i for i in self.repo.list_all_issues() if i["status"] != IssueStatus.STALE.value]
        members = self.repo.cluster_members()
        all_ids = [cid for i in issues for cid in members.get(i["cluster_id"], [])]
        complaints = self.repo.get_complaints(all_ids)
        frame = self.repo.complaint_frame()
        cat_by_id = dict(zip(frame["complaint_id"], frame["category"])) if not frame.empty else {}
        texts_by_cluster = {
            i["cluster_id"]: [complaints[c]["normalized_text"] for c in members.get(i["cluster_id"], []) if c in complaints]
            for i in issues
        }
        keywords = cluster_keywords(texts_by_cluster)
        counts = {"llm": 0, "fallback": 0, "manual_kept": 0, "cached_unchanged": 0}
        pending: list[tuple[dict[str, Any], list[str], list[str], list[str], str, list[dict[str, str]]]] = []
        for issue in issues:
            mids = members.get(issue["cluster_id"], [])
            reps = json.loads(issue["representative_ids"] or "[]")
            kw = keywords.get(issue["cluster_id"], [])
            signature = json.dumps({"reps": reps, "llm": self.classifier.available})
            if issue["label_source"] == "manual":
                counts["manual_kept"] += 1
                continue
            if issue["label_signature"] == signature and issue["label_source"] in ("llm", "fallback"):
                counts["cached_unchanged"] += 1
                continue
            samples = [{"complaint_id": c, "text": complaints[c]["normalized_text"]} for c in reps if c in complaints]
            pending.append((issue, mids, reps, kw, signature, samples))

        # All LLM label requests go out together (cached ones are answered without a call).
        labels = self.classifier.label_clusters([(p[5], p[3], len(p[1])) for p in pending])
        for (issue, mids, reps, kw, signature, samples), label in zip(pending, labels):
            if label is None:
                label = fallback_cluster_label(
                    kw, [cat_by_id.get(c) for c in mids], [s["text"] for s in samples], reps, len(mids)
                )
            counts[label["label_source"]] += 1
            self.repo.upsert_issue({
                **issue,
                "title": label["title"],
                "description": label["description"],
                "category": label["category"],
                "label_source": label["label_source"],
                "symptoms": json.dumps(label["symptoms"]),
                "root_cause_hypotheses": json.dumps(label["root_cause_hypotheses"]),
                "supporting_complaint_ids": json.dumps(label["supporting_complaint_ids"]),
                "keywords": json.dumps(kw),
                "label_signature": signature,
            })
        return counts

    def _dedup(self) -> dict[str, Any]:
        rows = self.repo.dedup_input()
        multi = {}
        for r in rows:
            if r["customer_id"]:
                multi[r["customer_id"]] = multi.get(r["customer_id"], 0) + 1
        need = [r["complaint_id"] for r in rows if r["customer_id"] and multi[r["customer_id"]] > 1]
        ids, mat = self.embedder.store.get_embeddings(need)
        emb = {cid: mat[i] for i, cid in enumerate(ids)}
        cfg = DedupConfig(
            time_window_days=self.settings.dedup_time_window_days,
            semantic_threshold=self.settings.dedup_semantic_threshold,
            auto_merge_confidence=self.settings.dedup_auto_merge_confidence,
            review_confidence=self.settings.dedup_review_confidence,
        )
        res = deduplicate(rows, emb, cfg)
        self.repo.replace_incidents(res.incidents, res.links, res.review)
        return {"complaints": len(rows), "incidents": len(res.incidents), "duplicates_linked": res.duplicate_count,
                "review_candidates": len(res.review)}

    def _score(self) -> dict[str, Any]:
        frame = self.repo.complaint_frame()
        issues = self.repo.list_all_issues(include_stale=False)
        weights = self.priority.weights() if self.priority else self.settings.weights
        cats = self.priority.category_weights() if self.priority else None
        scores = compute_issue_scores(frame, issues, weights, category_weights=cats)
        self.repo.replace_issue_metrics([s.as_metrics_row() for s in scores])
        self.repo.update_issue_scores([(s.issue_id, s.priority_score, s.severity) for s in scores])
        return {"scored_issues": len(scores)}
