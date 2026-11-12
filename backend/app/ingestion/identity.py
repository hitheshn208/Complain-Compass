"""Cross-channel identity resolution.

Channels identify people differently: CSV/chat exports may carry a ``customer_id``, emails carry a
sender address, calls a phone number, social posts a handle. To recognise the same customer
across channels (and so avoid double counting), the company can upload a *customer directory*
mapping contacts to customer IDs.

Privacy: raw contact values are never stored. They are normalized and hashed with a salted
SHA-256 (HMAC); only the hash is kept in ``customer_contacts``.

Resolution order for one item:
  1. explicit ``customer_id`` in the source data            -> link "explicit"
  2. contact hash found in the directory                    -> link "directory"
  3. contact present but unknown -> stable pseudonymous ID  -> link "pseudonymous"
     (e.g. ``EMAIL-3fa1c09b22d1``: the same sender is still recognised across their own
     messages, and is relinked automatically if the directory later contains the contact)
  4. no identity at all                                     -> anonymous (never merged)

Nothing about a person is inferred from complaint text.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from email.utils import parseaddr
from typing import Callable

CONTACT_TYPES = ("email", "phone", "social_handle")
PSEUDONYM_PREFIX = {"email": "EMAIL", "phone": "PHONE", "social_handle": "SOCIAL"}

_HANDLE = re.compile(r"^[a-z0-9_.]{1,64}$")


def normalize_contact(contact_type: str, value: str | None) -> str | None:
    """Canonical form of a contact, or ``None`` if it is not a usable identifier."""
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    if contact_type == "email":
        addr = parseaddr(value)[1].strip().lower()
        return addr if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", addr) else None
    if contact_type == "phone":
        digits = re.sub(r"\D", "", value)
        if len(digits) < 7:
            return None
        # Country-code formatting differs between systems; the last 10 digits identify the line.
        return digits[-10:]
    if contact_type == "social_handle":
        handle = value.lower().lstrip("@").strip()
        handle = handle.rsplit("/", 1)[-1]  # profile URLs -> handle
        return handle if _HANDLE.match(handle) else None
    raise ValueError(f"Unknown contact type {contact_type!r}")


def contact_hash(contact_type: str, normalized: str, salt: str) -> str:
    return hmac.new(salt.encode(), f"{contact_type}:{normalized}".encode(), hashlib.sha256).hexdigest()


def pseudonymous_id(contact_type: str, chash: str) -> str:
    return f"{PSEUDONYM_PREFIX[contact_type]}-{chash[:12]}"


class ContactResolver:
    """Resolves contacts to customer IDs using the directory lookup supplied by the repository."""

    def __init__(self, lookup: Callable[[str, list[str]], dict[str, str]], salt: str):
        self._lookup = lookup
        self.salt = salt

    def hash(self, contact_type: str, value: str | None) -> str | None:
        norm = normalize_contact(contact_type, value)
        return contact_hash(contact_type, norm, self.salt) if norm else None

    def resolve(self, contacts: list[tuple[str, str | None]]) -> tuple[str | None, str]:
        """``contacts``: ordered [(contact_type, raw_value)]. Returns (customer_id, link_method)."""
        hashed = [(t, h) for t, v in contacts if (h := self.hash(t, v))]
        for t, h in hashed:
            found = self._lookup(t, [h]).get(h)
            if found:
                return found, "directory"
        if hashed:
            t, h = hashed[0]
            return pseudonymous_id(t, h), "pseudonymous"
        return None, "anonymous"


def resolve_identity(
    resolver: ContactResolver, explicit_customer_id: str | None, contacts: list[tuple[str, str | None]]
) -> tuple[str | None, str]:
    if explicit_customer_id and str(explicit_customer_id).strip():
        return str(explicit_customer_id).strip(), "explicit"
    return resolver.resolve(contacts)
