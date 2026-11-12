"""Persistent ChromaDB vector store. Vector IDs == SQLite complaint_id."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import numpy as np


class VectorStore:
    def __init__(self, path: Path | str, collection_name: str = "complaints"):
        import chromadb

        Path(path).mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(
            path=str(path), settings=chromadb.Settings(anonymized_telemetry=False, allow_reset=True)
        )
        self.collection_name = collection_name
        self.collection = self.client.get_or_create_collection(
            collection_name, configuration={"hnsw": {"space": "cosine"}}, embedding_function=None
        )

    def count(self) -> int:
        return self.collection.count()

    def existing_ids(self, ids: Sequence[str]) -> set[str]:
        found: set[str] = set()
        ids = list(ids)
        for i in range(0, len(ids), 1000):
            res = self.collection.get(ids=ids[i : i + 1000], include=[])
            found.update(res["ids"])
        return found

    def upsert(
        self,
        ids: Sequence[str],
        embeddings: np.ndarray,
        documents: Sequence[str],
        metadatas: Sequence[dict[str, Any]],
    ) -> None:
        if len(ids) == 0:
            return
        self.collection.upsert(
            ids=list(ids), embeddings=embeddings.astype(np.float32), documents=list(documents), metadatas=list(metadatas)
        )

    def get_embeddings(self, ids: Sequence[str] | None = None) -> tuple[list[str], np.ndarray]:
        """Return (ids, matrix). With ``ids=None`` returns the whole collection.
        Rows are returned in the order of ``ids`` when given."""
        if ids is None:
            res = self.collection.get(include=["embeddings"])
            out_ids = list(res["ids"])
            emb = res["embeddings"]
            mat = np.asarray(emb, dtype=np.float32) if len(out_ids) else np.zeros((0, 0), dtype=np.float32)
            return out_ids, mat
        lookup: dict[str, np.ndarray] = {}
        ids = list(ids)
        for i in range(0, len(ids), 1000):
            res = self.collection.get(ids=ids[i : i + 1000], include=["embeddings"])
            for cid, vec in zip(res["ids"], res["embeddings"]):
                lookup[cid] = np.asarray(vec, dtype=np.float32)
        present = [cid for cid in ids if cid in lookup]
        mat = np.vstack([lookup[c] for c in present]) if present else np.zeros((0, 0), dtype=np.float32)
        return present, mat

    def query(
        self, embedding: np.ndarray, top_k: int, where: dict[str, Any] | None = None
    ) -> list[tuple[str, float, dict[str, Any], str]]:
        """Nearest neighbours. Returns (id, cosine_similarity, metadata, document)."""
        n = self.count()
        if n == 0:
            return []
        res = self.collection.query(
            query_embeddings=np.asarray(embedding, dtype=np.float32).reshape(1, -1),
            n_results=min(top_k, n),
            where=where,
            include=["distances", "metadatas", "documents"],
        )
        out = []
        for cid, dist, meta, doc in zip(res["ids"][0], res["distances"][0], res["metadatas"][0], res["documents"][0]):
            out.append((cid, float(1.0 - dist), meta or {}, doc or ""))
        return out

    def delete(self, ids: Sequence[str]) -> None:
        if ids:
            self.collection.delete(ids=list(ids))
