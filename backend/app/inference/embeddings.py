"""Semantic embedding engine (sentence-transformers/all-MiniLM-L6-v2, 384-d)."""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Protocol, Sequence

import numpy as np

from app.inference.vector_store import VectorStore

logger = logging.getLogger(__name__)
EMBEDDING_DIM = 384


class Encoder(Protocol):
    dim: int

    def encode(self, texts: Sequence[str], batch_size: int) -> np.ndarray: ...


class SentenceTransformerEncoder:
    """Wraps a SentenceTransformer model. Loaded once at application startup."""

    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer  # heavy import kept lazy

        self.model_name = model_name
        self.model = SentenceTransformer(model_name, device="cpu")
        dim_fn = getattr(self.model, "get_embedding_dimension", None) or self.model.get_sentence_embedding_dimension
        self.dim = int(dim_fn())

    def encode(self, texts: Sequence[str], batch_size: int) -> np.ndarray:
        vectors = self.model.encode(
            list(texts), batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False, convert_to_numpy=True
        )
        return vectors.astype(np.float32)


class HashingEncoder:
    """Deterministic bag-of-words hashing encoder. ONLY for fast API tests (EMBEDDER=fake);
    it has no semantic understanding and is never used in normal operation."""

    model_name = "fake-hashing"

    def __init__(self, dim: int = EMBEDDING_DIM):
        self.dim = dim

    def encode(self, texts: Sequence[str], batch_size: int) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            for tok in re.findall(r"[a-z]{3,}", text.lower()):
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                out[i, h % self.dim] += 1.0
        return l2_normalize(out)


def l2_normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (vectors / norms).astype(np.float32)


class EmbeddingEngine:
    """Generates normalized embeddings and persists them to the vector store.

    Incremental: complaints whose ID is already in the vector store are skipped, so
    re-running the pipeline never re-embeds processed complaints.
    """

    def __init__(self, encoder: Encoder, store: VectorStore, batch_size: int = 64):
        self.encoder = encoder
        self.store = store
        self.batch_size = batch_size

    @property
    def dim(self) -> int:
        return self.encoder.dim

    def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return l2_normalize(self.encoder.encode(texts, self.batch_size))

    def generate_embeddings(self, complaints: Sequence[dict[str, Any]]) -> list[str]:
        """Embed and store complaints. Each item needs complaint_id and normalized_text;
        customer_id/source/timestamp/batch_id are stored as metadata.
        Returns the IDs that were newly embedded (or already present)."""
        if not complaints:
            return []
        ids = [c["complaint_id"] for c in complaints]
        existing = self.store.existing_ids(ids)
        todo = [c for c in complaints if c["complaint_id"] not in existing]
        logger.info("Embedding %d complaints (%d already in vector store)", len(todo), len(existing))
        for start in range(0, len(todo), 256):
            chunk = todo[start : start + 256]
            vectors = self.embed_texts([c["normalized_text"] for c in chunk])
            self.store.upsert(
                ids=[c["complaint_id"] for c in chunk],
                embeddings=vectors,
                documents=[c["normalized_text"] for c in chunk],
                metadatas=[
                    {
                        "customer_id": c.get("customer_id") or "",
                        "source": c.get("source") or "",
                        "timestamp": c.get("timestamp") or "",
                        "batch_id": c.get("batch_id") or "",
                    }
                    for c in chunk
                ],
            )
        return ids
