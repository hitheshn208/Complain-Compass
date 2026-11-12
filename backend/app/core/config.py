"""Application configuration loaded from environment variables / .env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """All tunables for Complaint Compass. Every field can be set via env var."""

    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    # Storage
    db_path: Path = BACKEND_DIR / "storage" / "complaint_compass.db"
    chroma_dir: Path = BACKEND_DIR / "storage" / "chroma"
    chroma_collection: str = "complaints"

    # Embeddings: "sentence-transformers" (real) or "fake" (deterministic hashing; tests only)
    embedder: str = "sentence-transformers"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_batch_size: int = 64

    # LLM
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"
    llm_enabled: bool = True
    llm_timeout_seconds: float = 30.0
    llm_max_complaint_classifications: int = 2000

    # Upload limits
    max_upload_mb: float = 20.0

    # Clustering (HDBSCAN)
    hdbscan_min_cluster_size: int = 8
    hdbscan_min_samples: int = 4
    cluster_merge_threshold: float = 0.50  # consolidate HDBSCAN clusters with centroid cosine >= this (0 = off)
    cluster_match_threshold: float = 0.80  # centroid cosine sim to keep a stable issue id
    representatives_per_cluster: int = 5

    # Deduplication
    dedup_time_window_days: float = 7.0
    dedup_semantic_threshold: float = 0.55  # only applied within one verified customer + time window + same topic
    dedup_auto_merge_confidence: float = 0.75
    dedup_review_confidence: float = 0.45

    # Priority weights: P = wI*I + wS*S + wR*R + wG*G + wB*B
    weight_impact: float = 0.35
    weight_severity: float = 0.25
    weight_recurrence: float = 0.20
    weight_growth: float = 0.10
    weight_business: float = 0.10

    # Trend detection
    trend_baseline_days: int = 14
    trend_recent_days: int = 3
    trend_z_threshold: float = 2.0
    trend_min_recent_count: int = 3

    # Segmentation: suppress segments with fewer unique customers than this
    segment_min_customers: int = 3

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"])

    @model_validator(mode="after")
    def _check_weights(self) -> "Settings":
        total = self.weight_impact + self.weight_severity + self.weight_recurrence + self.weight_growth + self.weight_business
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Priority weights must sum to 1.0, got {total}")
        return self

    @property
    def weights(self) -> dict[str, float]:
        return {
            "impact": self.weight_impact,
            "severity": self.weight_severity,
            "recurrence": self.weight_recurrence,
            "growth": self.weight_growth,
            "business": self.weight_business,
        }

    @property
    def llm_available(self) -> bool:
        return bool(self.llm_enabled and self.openai_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
