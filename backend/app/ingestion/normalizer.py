"""Field-level normalization for ingested values."""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from app.database.models import Source

_SOURCE_ALIASES = {
    "email": Source.EMAIL,
    "e_mail": Source.EMAIL,
    "mail": Source.EMAIL,
    "chat": Source.CHAT,
    "live_chat": Source.CHAT,
    "livechat": Source.CHAT,
    "voice": Source.VOICE,
    "phone": Source.VOICE,
    "call": Source.VOICE,
    "social_media": Source.SOCIAL_MEDIA,
    "social": Source.SOCIAL_MEDIA,
    "twitter": Source.SOCIAL_MEDIA,
    "facebook": Source.SOCIAL_MEDIA,
    "csv": Source.CSV,
    "other": Source.OTHER,
}

_STUDENT_TRUE = {"yes", "y", "true", "1", "student"}
_STUDENT_FALSE = {"no", "n", "false", "0", "non-student", "non_student", "not student"}


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def clean_optional_str(value: Any) -> str | None:
    """Return a trimmed string or ``None`` for missing values."""
    if is_missing(value):
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    if text.lower() in {"nan", "none", "null", "n/a", "na"}:
        return None
    return text


def normalize_customer_id(value: Any) -> str | None:
    text = clean_optional_str(value)
    if text is None:
        return None
    # pandas may parse numeric ids as floats ("123.0")
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    return text


def normalize_source(value: Any) -> Source:
    """Map free-form channel names onto the Source enum. Raises ValueError if unknown."""
    text = clean_optional_str(value)
    if text is None:
        return Source.CSV
    key = text.lower().replace(" ", "_").replace("-", "_")
    if key in _SOURCE_ALIASES:
        return _SOURCE_ALIASES[key]
    try:
        return Source(text.upper())
    except ValueError as exc:
        raise ValueError(f"Unknown source '{text}'. Allowed: {', '.join(s.value for s in Source)}") from exc


def normalize_timestamp(value: Any) -> datetime | None:
    """Parse common timestamp formats to an aware UTC datetime. ``None`` if missing.
    Raises ValueError for unparseable values."""
    if is_missing(value):
        return None
    ts = pd.to_datetime(str(value).strip(), utc=True, errors="coerce")
    if ts is pd.NaT or pd.isna(ts):
        raise ValueError(f"Unparseable timestamp '{value}'")
    dt = ts.to_pydatetime().astimezone(timezone.utc)
    if dt.year < 1990 or dt > datetime.now(timezone.utc) + timedelta(days=366):
        raise ValueError(f"Timestamp '{value}' out of plausible range")
    return dt


def normalize_student_status(value: Any) -> str | None:
    text = clean_optional_str(value)
    if text is None:
        return None
    low = text.lower()
    if low in _STUDENT_TRUE:
        return "Student"
    if low in _STUDENT_FALSE:
        return "Non-student"
    return text


def normalize_category_value(value: Any) -> str | None:
    """Generic demographic bucket normalization (country, state, age_group, income)."""
    return clean_optional_str(value)
