"""Regex-based PII detection and masking (best effort, not a compliance tool)."""
from __future__ import annotations

import re

# Business reference identifiers: ORD-12345, order #12345, TXN-8899, transaction id 77ab12
_REFERENCE = re.compile(
    r"\b(?:(?P<prefix>ORD|ORDER|TXN|TRX|INV|REF|TKT|TICKET)[-_#: ]?(?P<code>[A-Z0-9]{4,}))\b"
    r"|\b(?:order|transaction|txn|invoice|ref(?:erence)?)\s*(?:id|no\.?|number)?\s*[#:]?\s*(?P<code2>[A-Z0-9-]{5,})\b",
    re.IGNORECASE,
)

_PII_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[EMAIL]"),
    ("CARD", re.compile(r"\b\d(?:[ -]?\d){12,15}\b"), "[CARD]"),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
    ("PHONE", re.compile(r"(?<![\w-])(?:\+?\d{1,3}[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]?\d{3}[ .-]?\d{4}\b"), "[PHONE]"),
]


def _canonical_ref(prefix: str | None, code: str) -> str:
    code = code.upper().strip("-")
    p = (prefix or "").upper()
    if p in {"ORDER"}:
        p = "ORD"
    if p in {"TRX"}:
        p = "TXN"
    if p in {"TICKET"}:
        p = "TKT"
    return f"{p}-{code}" if p and not code.startswith(p) else code


def extract_reference_ids(text: str) -> list[str]:
    """Return canonical order/transaction-like identifiers mentioned in ``text``."""
    refs: list[str] = []
    for m in _REFERENCE.finditer(text):
        if m.group("code"):
            ref = _canonical_ref(m.group("prefix"), m.group("code"))
        else:
            code = m.group("code2")
            if not any(ch.isdigit() for ch in code):
                continue
            ref = code.upper()
        if ref not in refs:
            refs.append(ref)
    return refs


def mask_pii(text: str) -> tuple[str, list[str]]:
    """Replace emails, card numbers, SSNs and phone numbers with typed placeholders.
    Reference IDs (ORD-123456) are protected from masking."""
    protected: dict[str, str] = {}

    def _protect(m: re.Match[str]) -> str:
        key = f"\x00{len(protected)}\x00"
        protected[key] = m.group(0)
        return key

    work = _REFERENCE.sub(_protect, text)
    found: list[str] = []
    for name, pattern, placeholder in _PII_PATTERNS:
        work, n = pattern.subn(placeholder, work)
        if n:
            found.append(name)
    for key, original in protected.items():
        work = work.replace(key, original)
    return work, found
