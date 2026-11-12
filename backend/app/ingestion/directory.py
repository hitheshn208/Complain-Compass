"""Customer directory import: links contacts (email / phone / social handle) to customer IDs.

CSV columns: ``customer_id`` (required) plus any of ``email``, ``phone``, ``social_handle``
(several values may be separated by ``;``) and optional explicit demographics
(country, state, age_group, student_status, income_bracket).

Only salted hashes of the contacts are produced; raw values are discarded after hashing.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.ingestion.errors import IngestionError
from app.ingestion.fields import load_records
from app.ingestion.identity import CONTACT_TYPES, ContactResolver, pseudonymous_id
from app.ingestion.normalizer import normalize_category_value, normalize_customer_id, normalize_student_status
from app.schemas.complaint import CustomerProfile, RowError

CONTACT_COLUMNS = {"email": ("email", "emails", "email_address"),
                   "phone": ("phone", "phones", "phone_number", "mobile"),
                   "social_handle": ("social_handle", "handle", "handles", "twitter", "instagram")}


@dataclass
class DirectoryImport:
    customers: list[CustomerProfile] = field(default_factory=list)
    contacts: list[tuple[str, str, str]] = field(default_factory=list)  # (type, hash, customer_id)
    errors: list[RowError] = field(default_factory=list)
    rows: int = 0

    def pseudonym_mapping(self) -> dict[str, str]:
        """{pseudonymous id that earlier uploads may have created: directory customer_id}."""
        return {pseudonymous_id(t, h): cid for t, h, cid in self.contacts}


def parse_directory(content: bytes, filename: str, resolver: ContactResolver) -> DirectoryImport:
    if not filename.lower().endswith(".csv"):
        raise IngestionError("Customer directory must be a .csv file")
    records = load_records(content, filename)
    if not records:
        raise IngestionError("Customer directory is empty", 422)
    if "customer_id" not in records[0]:
        raise IngestionError("Missing required column: customer_id", 422)
    present = [t for t in CONTACT_TYPES if any(c in records[0] for c in CONTACT_COLUMNS[t])]
    if not present:
        raise IngestionError("Directory needs at least one contact column: email, phone or social_handle", 422)

    out = DirectoryImport(rows=len(records))
    seen: dict[tuple[str, str], str] = {}
    for n, row in enumerate(records, start=1):
        cid = normalize_customer_id(row.get("customer_id"))
        if cid is None:
            out.errors.append(RowError(row_number=n, field="customer_id", message="customer_id is empty"))
            continue
        added = 0
        for ctype in present:
            raw = next((row.get(c) for c in CONTACT_COLUMNS[ctype] if row.get(c)), None)
            for value in str(raw or "").replace("|", ";").split(";"):
                if not value.strip():
                    continue
                h = resolver.hash(ctype, value)
                if h is None:
                    out.errors.append(RowError(row_number=n, field=ctype, message=f"invalid {ctype} value"))
                    continue
                if seen.get((ctype, h), cid) != cid:
                    out.errors.append(RowError(row_number=n, field=ctype,
                                               message=f"{ctype} already assigned to another customer in this file"))
                    continue
                seen[(ctype, h)] = cid
                out.contacts.append((ctype, h, cid))
                added += 1
        out.customers.append(CustomerProfile(
            customer_id=cid,
            country=normalize_category_value(row.get("country")),
            state=normalize_category_value(row.get("state")),
            age_group=normalize_category_value(row.get("age_group")),
            student_status=normalize_student_status(row.get("student_status")),
            income_bracket=normalize_category_value(row.get("income_bracket")),
        ))
        if not added:
            out.errors.append(RowError(row_number=n, field=None, message="no usable contact values"))
    return out
