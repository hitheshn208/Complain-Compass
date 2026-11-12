"""Chatbot / live-chat transcript ingestion (.json or .jsonl).

One conversation becomes one complaint. Only the *customer's* turns are used as complaint text;
bot and agent turns are dropped so that scripted bot replies do not distort clustering.

Accepted shape (field names are matched case-insensitively, common aliases accepted)::

    [{"conversation_id": "C-1", "customer_id": "CUST-1001",          # optional
      "customer_email": "a@b.com", "customer_phone": "...",           # optional, for directory lookup
      "started_at": "2026-11-01T10:00:00Z",
      "country": "India", ...                                         # optional explicit demographics
      "messages": [{"role": "customer", "text": "...", "timestamp": "..."},
                   {"role": "bot", "text": "..."}]}]

A JSON object holding the array under ``conversations`` / ``chats`` / ``transcripts`` / ``data`` also works.
"""
from __future__ import annotations

from app.database.models import Source
from app.ingestion.base import MultiFileAdapter, ResultBuilder, UploadedFile
from app.ingestion.fields import demographics, load_records, pick
from app.ingestion.identity import ContactResolver, resolve_identity
from app.schemas.complaint import IngestionResult

CUSTOMER_ROLES = {"customer", "user", "client", "visitor", "human", "end_user", "enduser", "guest", "contact"}
OTHER_ROLES = {"bot", "assistant", "agent", "system", "operator", "support", "staff", "ai"}
MESSAGE_KEYS = ("messages", "turns", "transcript", "conversation", "dialog")


class ChatIngestionAdapter(MultiFileAdapter):
    name = "chat"
    source = Source.CHAT
    extensions = (".json", ".jsonl", ".ndjson")
    description = "Chatbot / live-chat transcripts (JSON or JSONL); only customer messages are analysed."

    def __init__(self, resolver: ContactResolver):
        self.resolver = resolver

    def parse_files(self, files: list[UploadedFile]) -> IngestionResult:
        self.check_extensions(files)
        b = ResultBuilder(self.source)
        for f in files:
            records = load_records(f.content, f.filename, ("conversations", "chats", "transcripts", "data"))
            for n, conv in enumerate(records, start=1):
                self._add_conversation(b, conv, f"{f.filename}#{pick(conv, 'conversation_id', 'id', 'chat_id') or n}")
        return b.result()

    def _add_conversation(self, b: ResultBuilder, conv: dict, label: str) -> None:
        item = b.next_item()
        if "_invalid" in conv:
            b.reject(item, label, "record is not a JSON object")
            return
        messages = pick(conv, *MESSAGE_KEYS)
        customer_obj = conv.get("customer") if isinstance(conv.get("customer"), dict) else None
        if not isinstance(messages, list):
            b.reject(item, label, f"no message list (expected one of: {', '.join(MESSAGE_KEYS)})", "messages")
            return
        texts: list[str] = []
        first_ts = None
        unknown_roles: set[str] = set()
        for m in messages:
            if not isinstance(m, dict):
                continue
            role = str(pick(m, "role", "sender", "from", "author", "speaker", "type") or "").strip().lower()
            if role in CUSTOMER_ROLES:
                text = pick(m, "text", "message", "content", "body")
                if text:
                    texts.append(str(text).strip())
                    first_ts = first_ts or pick(m, "timestamp", "time", "created_at", "sent_at")
            elif role not in OTHER_ROLES:
                unknown_roles.add(role or "<missing>")
        if not texts:
            hint = f" (unrecognised roles: {', '.join(sorted(unknown_roles))})" if unknown_roles else ""
            b.reject(item, label, f"conversation has no customer messages{hint}", "messages")
            return
        ts = first_ts or pick(conv, "started_at", "start_time", "created_at", "timestamp", "date")
        customer_id, link = resolve_identity(
            self.resolver,
            pick(conv, "customer_id") or pick(customer_obj, "customer_id", "id"),
            [("email", pick(conv, "customer_email", "email") or pick(customer_obj, "email")),
             ("phone", pick(conv, "customer_phone", "phone") or pick(customer_obj, "phone"))],
        )
        detail = f"{len(texts)} customer message(s)"
        b.add(item, label, {"complaint_text": "\n".join(texts), "timestamp": ts, "customer_id": customer_id,
                            **demographics(conv, customer_obj)}, link, detail)
