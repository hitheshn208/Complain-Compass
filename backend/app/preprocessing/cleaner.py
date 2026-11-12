"""Conservative text cleaning. The original text is always stored separately."""
from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass, field

from app.preprocessing.pii_detection import extract_reference_ids, mask_pii

_HTML_TAG = re.compile(r"<[^>]{1,200}>")
_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_REPEATED_PUNCT = re.compile(r"([!?.,])\1{2,}")
_ZERO_WIDTH = re.compile(r"[​-‏⁠﻿]")
_EMAIL_QUOTE_LINE = re.compile(r"^\s*>.*$", re.MULTILINE)
_SIGNATURE = re.compile(r"(?im)^(sent from my \w+.*|--\s*$)")
_WS = re.compile(r"\s+")
_WORD = re.compile(r"[A-Za-z]{2,}")


@dataclass
class CleanResult:
    text: str
    is_empty: bool
    pii_found: list[str] = field(default_factory=list)
    reference_ids: list[str] = field(default_factory=list)


def clean_text(raw: str) -> CleanResult:
    """Normalize unicode/whitespace, strip markup noise, mask PII.

    Deliberately does NOT lowercase, remove stopwords or negations ("not", "never"),
    because those change complaint meaning for the embedding model.
    """
    text = unicodedata.normalize("NFKC", raw or "")
    text = _ZERO_WIDTH.sub("", text)
    text = html.unescape(text)
    text = _HTML_TAG.sub(" ", text)
    text = _EMAIL_QUOTE_LINE.sub(" ", text)
    text = _SIGNATURE.sub(" ", text)
    text = _URL.sub("[URL]", text)
    text = _REPEATED_PUNCT.sub(r"\1", text)
    # Reference IDs (orders, transactions) are business evidence used in dedup; extract
    # them before masking so they are not mistaken for PII numbers.
    refs = extract_reference_ids(text)
    text, pii_types = mask_pii(text)
    text = _WS.sub(" ", text).strip()
    is_empty = len(_WORD.findall(text)) == 0
    return CleanResult(text=text, is_empty=is_empty, pii_found=pii_types, reference_ids=refs)
