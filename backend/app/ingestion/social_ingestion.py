"""Social media ingestion: exported posts / mentions / reviews (.json, .jsonl or .csv).

One post becomes one complaint. The author handle is resolved through the customer directory
(``social_handle``), else a pseudonymous ID is used so the same author's posts are still linked.
Reposts / retweets are rejected with a reason (they repeat someone else's complaint).

Recognised fields (case-insensitive aliases): text|content|body|message|full_text,
handle|author|username|screen_name|user (string or object), created_at|timestamp|date,
post_id|id, platform, customer_id, plus explicit demographic columns. Location or other
attributes are never inferred from the post.
"""
from __future__ import annotations

import re

from app.database.models import Source
from app.ingestion.base import MultiFileAdapter, ResultBuilder, UploadedFile
from app.ingestion.fields import demographics, load_records, pick
from app.ingestion.identity import ContactResolver, resolve_identity
from app.schemas.complaint import IngestionResult

_RT = re.compile(r"^\s*RT @\w+:")


class SocialIngestionAdapter(MultiFileAdapter):
    name = "social"
    source = Source.SOCIAL_MEDIA
    extensions = (".json", ".jsonl", ".ndjson", ".csv")
    description = "Social media posts, mentions or reviews (JSON, JSONL or CSV export)."

    def __init__(self, resolver: ContactResolver):
        self.resolver = resolver

    def parse_files(self, files: list[UploadedFile]) -> IngestionResult:
        self.check_extensions(files)
        b = ResultBuilder(self.source)
        for f in files:
            for n, post in enumerate(load_records(f.content, f.filename, ("posts", "data", "mentions", "items")), 1):
                self._add_post(b, post, f"{f.filename}#{pick(post, 'post_id', 'id', 'tweet_id') or n}")
        return b.result()

    def _add_post(self, b: ResultBuilder, post: dict, label: str) -> None:
        item = b.next_item()
        if "_invalid" in post:
            b.reject(item, label, "record is not a JSON object")
            return
        text = pick(post, "text", "content", "body", "message", "full_text", "complaint_text")
        if str(pick(post, "is_retweet", "is_repost", "is_share") or "").lower() in {"true", "1", "yes"} or (
            isinstance(text, str) and _RT.match(text)
        ):
            b.reject(item, label, "repost/retweet of another post")
            return
        user = post.get("user") if isinstance(post.get("user"), dict) else None
        handle = pick(post, "handle", "author", "username", "screen_name", "author_handle") \
            or pick(user, "screen_name", "username", "handle")
        if handle is None and isinstance(post.get("user"), str):
            handle = post["user"]
        customer_id, link = resolve_identity(self.resolver, pick(post, "customer_id"),
                                             [("social_handle", handle)])
        platform = pick(post, "platform", "network", "source")
        b.add(item, label, {"complaint_text": text,
                            "timestamp": pick(post, "created_at", "timestamp", "date", "posted_at"),
                            "customer_id": customer_id, **demographics(post, user)},
              link, f"platform: {platform}" if platform else None)
