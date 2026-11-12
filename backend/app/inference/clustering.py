"""Issue discovery with HDBSCAN + stable cluster identity across reclusters.

HDBSCAN groups *semantically similar complaints* into candidate issues. It is not
used for duplicate/incident detection (see app.deduplication).

HDBSCAN labels (0, 1, 2, ...) are temporary and change between runs, so they are never
stored. Each discovered cluster is matched to a previously known issue by member overlap
(Jaccard) and centroid cosine similarity; matched clusters keep their stable
``cluster_id``/``issue_id``, unmatched clusters get fresh IDs.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

import numpy as np
from sklearn.cluster import HDBSCAN, AgglomerativeClustering
from sklearn.feature_extraction.text import TfidfVectorizer

logger = logging.getLogger(__name__)

NOISE = -1


@dataclass
class DiscoveredCluster:
    """A cluster from a single HDBSCAN run (before stable-ID resolution)."""

    temp_label: int
    member_ids: list[str]
    centroid: np.ndarray
    representative_ids: list[str]
    member_confidence: dict[str, float]
    cohesion: float  # mean cosine similarity of members to centroid


@dataclass
class ClusteringResult:
    clusters: list[DiscoveredCluster]
    noise_ids: list[str]
    n_points: int

    @property
    def noise_ratio(self) -> float:
        return len(self.noise_ids) / self.n_points if self.n_points else 0.0


@dataclass
class ResolvedCluster:
    cluster: DiscoveredCluster
    cluster_id: str
    issue_id: str
    is_new: bool
    matched_by: dict[str, float] = field(default_factory=dict)


def _normalize(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


class ClusteringEngine:
    def __init__(
        self,
        min_cluster_size: int = 8,
        min_samples: int | None = 4,
        representatives: int = 5,
        selection_method: str = "eom",
        merge_threshold: float = 0.5,
    ):
        if min_cluster_size < 2:
            raise ValueError("min_cluster_size must be >= 2")
        self.min_cluster_size = min_cluster_size
        self.min_samples = min_samples
        self.representatives = representatives
        self.selection_method = selection_method
        self.merge_threshold = merge_threshold

    def cluster(self, ids: list[str], embeddings: np.ndarray) -> ClusteringResult:
        """Run HDBSCAN on L2-normalized embeddings.

        Euclidean distance on unit vectors is monotonic with cosine distance
        (||a-b||^2 = 2 - 2cos), so this is effectively cosine clustering.
        Noise points (label -1) stay unassigned; they are never forced into a cluster.
        """
        n = len(ids)
        if n < max(self.min_cluster_size, 2):
            return ClusteringResult(clusters=[], noise_ids=list(ids), n_points=n)
        model = HDBSCAN(
            min_cluster_size=self.min_cluster_size,
            min_samples=self.min_samples,
            metric="euclidean",
            cluster_selection_method=self.selection_method,
            copy=True,
        )
        labels = model.fit_predict(embeddings)
        probs = getattr(model, "probabilities_", np.ones(n))
        raw_k = len(set(labels.tolist()) - {NOISE})
        labels = self.consolidate(labels, embeddings)
        clusters: list[DiscoveredCluster] = []
        for label in sorted(set(labels.tolist()) - {NOISE}):
            idx = np.where(labels == label)[0]
            members = embeddings[idx]
            centroid = _normalize(members.mean(axis=0))
            sims = members @ centroid
            reps = [ids[idx[i]] for i in select_representatives(members, sims, self.representatives)]
            clusters.append(
                DiscoveredCluster(
                    temp_label=int(label),
                    member_ids=[ids[i] for i in idx],
                    centroid=centroid.astype(np.float32),
                    representative_ids=reps,
                    member_confidence={ids[i]: float(probs[i]) for i in idx},
                    cohesion=float(sims.mean()),
                )
            )
        noise = [ids[i] for i in np.where(labels == NOISE)[0]]
        logger.info("HDBSCAN found %d clusters (%d after consolidation), %d noise of %d points",
                    raw_k, len(clusters), len(noise), n)
        return ClusteringResult(clusters=clusters, noise_ids=noise, n_points=n)


    def consolidate(self, labels: np.ndarray, embeddings: np.ndarray) -> np.ndarray:
        """Merge HDBSCAN clusters whose centroids are semantically close.

        HDBSCAN often splits one underlying issue into several dense sub-themes (e.g. different
        phrasings). Clusters are merged with average-linkage agglomerative clustering on their
        centroids when cosine similarity >= ``merge_threshold``. Noise points (-1) are left
        untouched - consolidation never assigns noise to a cluster. ``merge_threshold <= 0``
        disables the step.
        """
        ks = sorted(set(labels.tolist()) - {NOISE})
        if self.merge_threshold <= 0 or len(ks) < 2:
            return labels
        cents = np.vstack([_normalize(embeddings[labels == k].mean(axis=0)) for k in ks])
        agg = AgglomerativeClustering(
            n_clusters=None, metric="cosine", linkage="average", distance_threshold=1.0 - self.merge_threshold
        ).fit(cents)
        mapping = {k: int(agg.labels_[i]) for i, k in enumerate(ks)}
        return np.array([mapping[l] if l != NOISE else NOISE for l in labels.tolist()])


def select_representatives(members: np.ndarray, centroid_sims: np.ndarray, k: int,
                           redundancy: float = 0.92) -> list[int]:
    """Pick up to ``k`` member indices close to the centroid while skipping near-duplicates.

    Members are visited in order of centroid proximity; a member is skipped if its cosine
    similarity to an already chosen representative exceeds ``redundancy``. If too few
    diverse members exist, the remaining slots are filled by proximity alone.
    """
    order = list(np.argsort(-centroid_sims))
    chosen: list[int] = []
    for i in order:
        if len(chosen) >= k:
            break
        if all(float(members[i] @ members[j]) <= redundancy for j in chosen):
            chosen.append(int(i))
    for i in order:
        if len(chosen) >= k:
            break
        if int(i) not in chosen:
            chosen.append(int(i))
    return chosen


def resolve_stable_ids(
    result: ClusteringResult,
    previous: list[dict],
    centroid_threshold: float = 0.80,
    jaccard_threshold: float = 0.5,
) -> list[ResolvedCluster]:
    """Map temporary clusters onto previously known issues.

    ``previous`` items: {"issue_id", "cluster_id", "centroid": np.ndarray | None, "members": set[str]}.
    Greedy one-to-one matching on score = max(jaccard, centroid_similarity), accepting a pair
    if jaccard >= jaccard_threshold or centroid similarity >= centroid_threshold.
    """
    candidates: list[tuple[float, int, int, float, float]] = []
    for ci, cl in enumerate(result.clusters):
        members = set(cl.member_ids)
        for pi, prev in enumerate(previous):
            prev_members = prev.get("members") or set()
            union = members | prev_members
            jac = len(members & prev_members) / len(union) if union else 0.0
            cent = prev.get("centroid")
            csim = float(np.dot(cl.centroid, cent)) if cent is not None and len(cent) == len(cl.centroid) else 0.0
            if jac >= jaccard_threshold or csim >= centroid_threshold:
                candidates.append((max(jac, csim), ci, pi, jac, csim))
    candidates.sort(reverse=True)
    used_c, used_p = set(), set()
    resolved: dict[int, ResolvedCluster] = {}
    for _, ci, pi, jac, csim in candidates:
        if ci in used_c or pi in used_p:
            continue
        used_c.add(ci)
        used_p.add(pi)
        prev = previous[pi]
        resolved[ci] = ResolvedCluster(
            cluster=result.clusters[ci],
            cluster_id=prev["cluster_id"],
            issue_id=prev["issue_id"],
            is_new=False,
            matched_by={"jaccard": round(jac, 3), "centroid_similarity": round(csim, 3)},
        )
    for ci, cl in enumerate(result.clusters):
        if ci not in resolved:
            resolved[ci] = ResolvedCluster(
                cluster=cl, cluster_id=f"CL-{uuid.uuid4().hex[:10]}", issue_id=str(uuid.uuid4()), is_new=True
            )
    return [resolved[i] for i in range(len(result.clusters))]


def cluster_keywords(texts_by_cluster: dict[str, list[str]], top_n: int = 6) -> dict[str, list[str]]:
    """Distinctive terms per cluster via class-based TF-IDF (each cluster = one document)."""
    keys = [k for k, v in texts_by_cluster.items() if v]
    if not keys:
        return {}
    docs = [" ".join(texts_by_cluster[k]) for k in keys]
    vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), max_features=5000, sublinear_tf=True,
                          token_pattern=r"(?u)\b[a-zA-Z]{3,}\b")
    try:
        mat = vec.fit_transform(docs)
    except ValueError:  # empty vocabulary
        return {k: [] for k in keys}
    terms = np.array(vec.get_feature_names_out())
    out: dict[str, list[str]] = {}
    for row, key in enumerate(keys):
        scores = mat[row].toarray().ravel()
        top = terms[np.argsort(-scores)[: top_n * 3]]
        picked: list[str] = []
        for t in top:
            if any(t in p or p in t for p in picked):
                continue
            picked.append(str(t))
            if len(picked) >= top_n:
                break
        out[key] = picked
    return out
