from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from app.core.config import Settings  # noqa: E402
from app.database.repository import Repository  # noqa: E402
from app.database.sqlite import Database  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: requires external services (OpenAI API)")


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Isolated settings: temp storage, LLM disabled, real embeddings."""
    return Settings(db_path=tmp_path / "test.db", chroma_dir=tmp_path / "chroma", openai_api_key=None,
                    llm_enabled=False, _env_file=None)


@pytest.fixture
def fake_settings(tmp_path) -> Settings:
    """Same as ``settings`` but with the fast hashing embedder (API plumbing tests only)."""
    return Settings(db_path=tmp_path / "test.db", chroma_dir=tmp_path / "chroma", openai_api_key=None,
                    llm_enabled=False, embedder="fake", hdbscan_min_cluster_size=3, hdbscan_min_samples=2,
                    _env_file=None)


@pytest.fixture
def repo(tmp_path) -> Repository:
    db = Database(tmp_path / "repo.db")
    db.init_schema()
    return Repository(db)


@pytest.fixture(scope="session")
def st_encoder():
    """Real all-MiniLM-L6-v2 encoder, loaded once per test session."""
    from app.inference.embeddings import SentenceTransformerEncoder

    return SentenceTransformerEncoder("sentence-transformers/all-MiniLM-L6-v2")
