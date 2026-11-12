"""Application lifespan: build heavy singletons once (embedding model, Chroma, DB)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from fastapi import FastAPI

from app.core.config import Settings, get_settings
from app.database.repository import Repository
from app.database.sqlite import Database
from app.inference.classifier import LLMClassifier
from app.inference.embeddings import EmbeddingEngine, HashingEncoder, SentenceTransformerEncoder
from app.inference.similarity import SimilarityService
from app.inference.vector_store import VectorStore
from app.ingestion.csv_ingestion import CSVIngestionAdapter
from app.services.analytics_service import AnalyticsService
from app.services.ingestion_service import IngestionService
from app.services.pipeline import PipelineOrchestrator, PipelineState

logger = logging.getLogger(__name__)


@dataclass
class Container:
    settings: Settings
    db: Database
    repo: Repository
    store: VectorStore
    embedder: EmbeddingEngine
    similarity: SimilarityService
    classifier: LLMClassifier
    pipeline: PipelineOrchestrator
    analytics: AnalyticsService
    ingestion: IngestionService


def build_container(settings: Settings) -> Container:
    db = Database(settings.db_path)
    db.init_schema()
    repo = Repository(db)
    store = VectorStore(settings.chroma_dir, settings.chroma_collection)
    if settings.embedder == "fake":
        logger.warning("Using FAKE hashing embedder (EMBEDDER=fake). Not for real analysis.")
        encoder = HashingEncoder()
    else:
        logger.info("Loading embedding model %s", settings.embedding_model)
        encoder = SentenceTransformerEncoder(settings.embedding_model)
    embedder = EmbeddingEngine(encoder, store, settings.embedding_batch_size)
    classifier = LLMClassifier(repo, settings.openai_api_key, settings.openai_model, settings.llm_timeout_seconds,
                               settings.llm_enabled)
    if not classifier.available:
        logger.warning("OPENAI_API_KEY not set or LLM disabled: using deterministic fallback classification/labels.")
    pipeline = PipelineOrchestrator(settings, repo, embedder, classifier, PipelineState())
    return Container(
        settings=settings,
        db=db,
        repo=repo,
        store=store,
        embedder=embedder,
        similarity=SimilarityService(store),
        classifier=classifier,
        pipeline=pipeline,
        analytics=AnalyticsService(repo, settings),
        ingestion=IngestionService(repo, CSVIngestionAdapter(int(settings.max_upload_mb * 1024 * 1024))),
    )


def make_lifespan(settings: Settings | None = None):
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.container = build_container(settings or get_settings())
        yield
        app.state.container.db.close()

    return lifespan
