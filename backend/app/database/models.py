"""Domain enums and small value objects shared across modules."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum


class Source(str, Enum):
    EMAIL = "EMAIL"
    CHAT = "CHAT"
    VOICE = "VOICE"
    SOCIAL_MEDIA = "SOCIAL_MEDIA"
    CSV = "CSV"
    OTHER = "OTHER"


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class BatchStatus(str, Enum):
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    FAILED = "FAILED"


class ComplaintStatus(str, Enum):
    INGESTED = "INGESTED"
    PREPROCESSED = "PREPROCESSED"
    EMBEDDED = "EMBEDDED"
    PROCESSED = "PROCESSED"
    REJECTED_EMPTY = "REJECTED_EMPTY"
    FAILED = "FAILED"


class IssueStatus(str, Enum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    RESOLVED = "RESOLVED"
    STALE = "STALE"  # cluster disappeared on recluster


SEVERITY_ORDER = {Severity.LOW: 0, Severity.MEDIUM: 1, Severity.HIGH: 2, Severity.CRITICAL: 3}

CATEGORIES = [
    "Payments",
    "Billing",
    "Refunds",
    "Delivery",
    "Product Quality",
    "Account Access",
    "Account Status",
    "Customer Support",
    "Subscription",
    "App Stability",
    "Other",
]


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
