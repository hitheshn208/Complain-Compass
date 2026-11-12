"""Email ingestion: RFC 822 messages (.eml) and mailbox archives (.mbox).

Each message becomes one complaint:
  text      = subject + plain-text body (HTML-only bodies are converted to text; quoted replies
              and signatures are removed later by the shared preprocessing step)
  timestamp = ``Date`` header
  customer  = ``X-Customer-ID`` header if present, else the sender address resolved through the
              customer directory, else a pseudonymous sender ID
Auto-generated mail (auto-replies, bounces, bulk) is rejected with a reason, never silently dropped.
"""
from __future__ import annotations

import html
import mailbox
import os
import re
import tempfile
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parsedate_to_datetime

from app.database.models import Source
from app.ingestion.base import MultiFileAdapter, ResultBuilder, UploadedFile
from app.ingestion.errors import IngestionError
from app.ingestion.identity import ContactResolver, resolve_identity
from app.schemas.complaint import IngestionResult

_BLOCK_TAGS = re.compile(r"(?i)</?(p|div|br|li|tr|h[1-6]|table|blockquote)[^>]*>")
_SCRIPT_STYLE = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>")
_TAG = re.compile(r"<[^>]+>")
_GENERIC_SUBJECTS = {"", "(no subject)", "no subject", "complaint", "help", "support", "issue"}


def html_to_text(markup: str) -> str:
    text = _SCRIPT_STYLE.sub(" ", markup)
    text = _BLOCK_TAGS.sub("\n", text)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", text)).strip()


def message_body(msg: EmailMessage) -> str:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, UnicodeDecodeError):  # unknown charset
        payload = part.get_payload(decode=True) or b""
        content = payload.decode("utf-8", errors="replace")
    if part.get_content_subtype() == "html":
        content = html_to_text(content)
    return content.strip()


def is_auto_generated(msg: EmailMessage) -> str | None:
    auto = (msg.get("Auto-Submitted") or "").strip().lower()
    if auto and auto != "no":
        return f"auto-generated message (Auto-Submitted: {auto})"
    prec = (msg.get("Precedence") or "").strip().lower()
    if prec in {"bulk", "junk", "list", "auto_reply"}:
        return f"bulk/automated message (Precedence: {prec})"
    if msg.get("X-Autoreply") or msg.get("X-Autorespond"):
        return "auto-reply"
    ctype = msg.get_content_type()
    if ctype in {"multipart/report", "message/delivery-status"}:
        return "delivery status notification"
    return None


class EmailIngestionAdapter(MultiFileAdapter):
    name = "email"
    source = Source.EMAIL
    extensions = (".eml", ".mbox")
    description = "Email messages as .eml files or .mbox archives (sender is matched through the customer directory)."

    def __init__(self, resolver: ContactResolver):
        self.resolver = resolver

    def parse_files(self, files: list[UploadedFile]) -> IngestionResult:
        self.check_extensions(files)
        b = ResultBuilder(self.source)
        for f in files:
            if f.ext == ".mbox":
                for i, msg in enumerate(self._read_mbox(f), start=1):
                    self._add_message(b, msg, f"{f.filename}#{i}")
            else:
                msg = BytesParser(policy=policy.default).parsebytes(f.content)
                if not msg.keys():
                    b.reject(b.next_item(), f.filename, "not an email message (no headers)")
                    continue
                self._add_message(b, msg, f.filename)
        return b.result()

    @staticmethod
    def _read_mbox(f: UploadedFile) -> list[EmailMessage]:
        if not f.content.startswith(b"From "):
            raise IngestionError(f"{f.filename}: not an mbox archive (must start with a 'From ' line)")
        fd, path = tempfile.mkstemp(suffix=".mbox")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(f.content)
            box = mailbox.mbox(path, create=False)
            try:
                return [BytesParser(policy=policy.default).parsebytes(m.as_bytes()) for m in box]
            finally:
                box.close()
        finally:
            os.unlink(path)

    def _add_message(self, b: ResultBuilder, msg: EmailMessage, label: str) -> None:
        item = b.next_item()
        auto = is_auto_generated(msg)
        if auto:
            b.reject(item, label, auto)
            return
        subject = (msg.get("Subject") or "").strip()
        body = message_body(msg)
        text = body if subject.lower() in _GENERIC_SUBJECTS or subject.lower() in body.lower() \
            else f"{subject}\n\n{body}".strip()
        ts = None
        date_hdr = msg.get("Date")
        if date_hdr is not None and str(date_hdr).strip():
            dt = getattr(date_hdr, "datetime", None)
            if dt is None:
                try:
                    dt = parsedate_to_datetime(str(date_hdr))
                except (TypeError, ValueError):
                    b.reject(item, label, f"unparseable Date header '{date_hdr}'", "timestamp")
                    return
            ts = dt.isoformat()
        sender = msg.get("From")
        customer_id, link = resolve_identity(self.resolver, msg.get("X-Customer-ID"), [("email", sender)])
        b.add(item, label, {"complaint_text": text, "timestamp": ts, "customer_id": customer_id}, link)
