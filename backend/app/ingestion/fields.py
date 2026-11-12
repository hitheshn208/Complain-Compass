"""Helpers for reading loosely structured channel exports (JSON / CSV)."""
from __future__ import annotations

import csv
import io
import json
from typing import Any, Iterable, Mapping

from app.ingestion.errors import IngestionError
from app.ingestion.validator import DEMOGRAPHIC_COLUMNS


def pick(d: Mapping[str, Any] | None, *keys: str) -> Any:
    """First non-empty value among ``keys`` (case-insensitive)."""
    if not isinstance(d, Mapping):
        return None
    lower = {str(k).lower(): v for k, v in d.items()}
    for k in keys:
        v = lower.get(k.lower())
        if v is not None and not (isinstance(v, str) and not v.strip()):
            return v
    return None


def demographics(*sources: Mapping[str, Any] | None) -> dict[str, Any]:
    """Explicit demographic fields from the record (or a nested ``customer`` object).
    Values are only ever copied from supplied data, never inferred."""
    out: dict[str, Any] = {}
    for col in DEMOGRAPHIC_COLUMNS:
        for src in sources:
            v = pick(src, col)
            if v is not None:
                out[col] = v
                break
    return out


def decode_text(content: bytes, filename: str) -> str:
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    raise IngestionError(f"{filename}: not valid text")  # pragma: no cover


def load_records(content: bytes, filename: str, list_keys: Iterable[str] = ()) -> list[dict[str, Any]]:
    """Read records from .json (array, or object holding an array under one of ``list_keys``),
    .jsonl / .ndjson (one object per line) or .csv."""
    text = decode_text(content, filename)
    name = filename.lower()
    if name.endswith((".jsonl", ".ndjson")):
        out = []
        for n, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise IngestionError(f"{filename}: invalid JSON on line {n}: {exc.msg}") from exc
            out.append(obj if isinstance(obj, dict) else {"_invalid": obj})
        return out
    if name.endswith(".json"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise IngestionError(f"{filename}: invalid JSON (line {exc.lineno}): {exc.msg}") from exc
        if isinstance(data, dict):
            for k in list_keys:
                if isinstance(data.get(k), list):
                    data = data[k]
                    break
            else:
                data = [data]
        if not isinstance(data, list):
            raise IngestionError(f"{filename}: expected a JSON array of records")
        return [r if isinstance(r, dict) else {"_invalid": r} for r in data]
    if name.endswith(".csv"):
        reader = csv.DictReader(io.StringIO(text))
        return [{(k or "").strip().lower(): v for k, v in row.items()} for row in reader]
    raise IngestionError(f"{filename}: unsupported format")
