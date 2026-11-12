"""Abstract ingestion interface.

Every input channel (CSV today; email, chat transcripts, voice-to-text later) implements
``IngestionAdapter`` and emits ``StandardComplaint`` objects. Downstream inference never
sees the raw input format, so new adapters require no change to the inference engine.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.schemas.complaint import IngestionResult


class IngestionError(Exception):
    """File-level failure (bad format, missing required columns, too large...)."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class IngestionAdapter(ABC):
    """Converts a raw payload into standardized complaints + row-level errors."""

    name: str = "base"

    @abstractmethod
    def parse(self, content: bytes, filename: str) -> IngestionResult:
        """Parse ``content``. Raise ``IngestionError`` for file-level problems; collect
        row-level problems in ``IngestionResult.errors`` and keep the valid rows."""
