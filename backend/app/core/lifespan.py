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
from app.inference.llm_cache import LLMCache
from app.inference.embeddings import EmbeddingEngine, HashingEncoder, SentenceTransformerEncoder
from app.inference.similarity import SimilarityService
from app.inference.vector_store import VectorStore
from app.ingestion.chat_ingestion import ChatIngestionAdapter
from app.ingestion.csv_ingestion import CSVIngestionAdapter
from app.ingestion.email_ingestion import EmailIngestionAdapter
from app.ingestion.identity import ContactResolver
from app.ingestion.social_ingestion import SocialIngestionAdapter
from app.ingestion.transcription import Transcriber, build_transcriber
from app.ingestion.voice_ingestion import VoiceIngestionAdapter
from app.services.analytics_service import AnalyticsService
from app.services.ingestion_service import IngestionService
from app.services.pipeline import PipelineOrchestrator, PipelineState
from app.services.priority_settings import PrioritySettings

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
    llm_cache: LLMCache
    pipeline: PipelineOrchestrator
    priority: PrioritySettings
    analytics: AnalyticsService
    ingestion: IngestionService
    transcriber: Transcriber


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
    llm_cache = LLMCache(settings.llm_cache_path)
    moved = llm_cache.import_from(db.connect())
    if moved:
        logger.info("Moved %d cached LLM responses into %s", moved, settings.llm_cache_path)
    logger.info("LLM response cache: %d entries in %s", llm_cache.count(), settings.llm_cache_path)
    interrupted = repo.fail_interrupted_batches()
    if interrupted:
        logger.warning("Marked %d batch(es) left PROCESSING by a previous run as FAILED", interrupted)
    classifier = LLMClassifier(llm_cache, settings.openai_api_key, settings.openai_model, settings.llm_timeout_seconds,
                               settings.llm_enabled, settings.llm_batch_size, settings.llm_concurrency)
    if not classifier.available:
        logger.warning("OPENAI_API_KEY not set or LLM disabled: using deterministic fallback classification/labels.")
    priority = PrioritySettings(repo, settings)
    pipeline = PipelineOrchestrator(settings, repo, embedder, classifier, PipelineState(), priority)
    resolver = ContactResolver(repo.lookup_contacts, settings.identity_salt)
    transcriber = build_transcriber(settings)
    if not transcriber.available:
        logger.warning("Voice ingestion unavailable: %s", transcriber.unavailable_reason)
    channels = {
        "email": EmailIngestionAdapter(resolver),
        "chat": ChatIngestionAdapter(resolver),
        "social": SocialIngestionAdapter(resolver),
        "voice": VoiceIngestionAdapter(resolver, transcriber),
    }
    return Container(
        settings=settings,
        db=db,
        repo=repo,
        store=store,
        embedder=embedder,
        similarity=SimilarityService(store),
        classifier=classifier,
        llm_cache=llm_cache,
        pipeline=pipeline,
        priority=priority,
        analytics=AnalyticsService(repo, settings, priority),
        ingestion=IngestionService(repo, CSVIngestionAdapter(int(settings.max_upload_mb * 1024 * 1024)),
                                   channels, resolver),
        transcriber=transcriber,
    )


def make_lifespan(settings: Settings | None = None):
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.container = build_container(settings or get_settings())
        yield
        app.state.container.db.close()
        app.state.container.llm_cache.close()

    return lifespan
