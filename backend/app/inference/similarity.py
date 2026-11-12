"""Semantic similarity search over stored complaint embeddings."""
from __future__ import annotations

from typing import Any

import numpy as np

from app.inference.vector_store import VectorStore


class ComplaintNotEmbeddedError(LookupError):
    pass


def cosine_similarity_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Cosine similarity for L2-normalized rows (dot product)."""
    return np.clip(a @ b.T, -1.0, 1.0)


class SimilarityService:
    def __init__(self, store: VectorStore):
        self.store = store

    def find_similar_complaints(
        self, complaint_id: str, top_k: int = 10, where: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Top-k most similar complaints (excluding the complaint itself), by cosine similarity."""
        ids, mat = self.store.get_embeddings([complaint_id])
        if not ids:
            raise ComplaintNotEmbeddedError(complaint_id)
        hits = self.store.query(mat[0], top_k + 1, where=where)
        results = []
        for cid, sim, meta, doc in hits:
            if cid == complaint_id:
                continue
            results.append(
                {
                    "complaint_id": cid,
                    "similarity": round(sim, 4),
                    "text": doc,
                    "customer_id": meta.get("customer_id") or None,
                    "source": meta.get("source") or None,
                    "timestamp": meta.get("timestamp") or None,
                }
            )
        return results[:top_k]
