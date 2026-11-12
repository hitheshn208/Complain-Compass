"""Uses the real all-MiniLM-L6-v2 model."""
import numpy as np
import pytest

from app.inference.embeddings import EmbeddingEngine
from app.inference.similarity import ComplaintNotEmbeddedError, SimilarityService
from app.inference.vector_store import VectorStore


class CountingEncoder:
    def __init__(self, inner):
        self.inner = inner
        self.dim = inner.dim
        self.model_name = inner.model_name
        self.calls = 0
        self.texts = 0

    def encode(self, texts, batch_size):
        self.calls += 1
        self.texts += len(texts)
        return self.inner.encode(texts, batch_size)


DOCS = [
    ("a", "My payment failed at checkout but money was deducted from my bank"),
    ("b", "Card declined while paying, amount debited from account"),
    ("c", "The package has not been delivered and tracking is stuck"),
    ("d", "My parcel is late, shipping hasn't updated in days"),
    ("e", "The app crashes every time I open it"),
]


def _items():
    return [{"complaint_id": i, "normalized_text": t, "customer_id": "C1" if i < "c" else None, "source": "EMAIL",
             "timestamp": "2026-10-01T00:00:00+00:00", "batch_id": "b1"} for i, t in DOCS]


def test_embedding_dimension_and_normalization(st_encoder, tmp_path):
    engine = EmbeddingEngine(st_encoder, VectorStore(tmp_path / "chroma"))
    vecs = engine.embed_texts(["hello world", "refund not received"])
    assert vecs.shape == (2, 384)
    assert np.allclose(np.linalg.norm(vecs, axis=1), 1.0, atol=1e-5)


def test_incremental_generation_skips_existing(st_encoder, tmp_path):
    enc = CountingEncoder(st_encoder)
    engine = EmbeddingEngine(enc, VectorStore(tmp_path / "chroma"))
    engine.generate_embeddings(_items())
    assert enc.texts == 5
    engine.generate_embeddings(_items())
    assert enc.texts == 5  # nothing re-embedded
    extra = _items()[:1]
    extra[0]["complaint_id"] = "z"
    engine.generate_embeddings(_items() + extra)
    assert enc.texts == 6


def test_chroma_persistence_and_metadata(st_encoder, tmp_path):
    engine = EmbeddingEngine(st_encoder, VectorStore(tmp_path / "chroma"))
    engine.generate_embeddings(_items())
    reopened = VectorStore(tmp_path / "chroma")
    assert reopened.count() == 5
    ids, mat = reopened.get_embeddings(["c", "a"])
    assert ids == ["c", "a"] and mat.shape == (2, 384)
    res = reopened.collection.get(ids=["a"], include=["metadatas"])
    assert res["metadatas"][0]["customer_id"] == "C1"


def test_similarity_search_ranks_semantic_neighbours(st_encoder, tmp_path):
    store = VectorStore(tmp_path / "chroma")
    EmbeddingEngine(st_encoder, store).generate_embeddings(_items())
    sim = SimilarityService(store)
    hits = sim.find_similar_complaints("a", top_k=3)
    assert hits[0]["complaint_id"] == "b"
    assert all(h["complaint_id"] != "a" for h in hits)
    assert hits[0]["similarity"] > hits[-1]["similarity"]
    hits_c = sim.find_similar_complaints("c", top_k=1)
    assert hits_c[0]["complaint_id"] == "d"
    with pytest.raises(ComplaintNotEmbeddedError):
        sim.find_similar_complaints("missing", top_k=3)
