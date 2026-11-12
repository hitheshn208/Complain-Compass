"""Ingestion exceptions."""
from __future__ import annotations


class IngestionError(Exception):
    """File-level failure (bad format, missing required columns, too large...)."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
