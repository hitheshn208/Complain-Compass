"""Voice-call ingestion: audio recordings -> speech-to-text -> complaints.

Upload one or more recordings (.wav .mp3 .m4a .ogg .flac .webm .mp4 .aac) and optionally ONE
manifest CSV describing them::

    filename,customer_id,phone,timestamp,country,state,age_group,student_status,income_bracket
    call_0001.wav,CUST-1003,,2026-11-08T09:12:00Z,India,Karnataka,25-34,Non-student,25k-50k
    call_0002.wav,,+1 555 201 7781,2026-11-09T14:40:00Z,,,,,

Identity: manifest ``customer_id``, else the caller's ``phone`` through the customer directory,
else a pseudonymous phone ID, else anonymous. Without a manifest the timestamp is the upload time
(marked inferred) and calls are anonymous.

The whole call recording is transcribed (no speaker separation), so agent speech is included
in the complaint text. Spoken digit sequences ("4 8 1 2 3 4") are joined so order / transaction
numbers can be matched with other channels.
"""
from __future__ import annotations

import logging
import re

from app.database.models import Source
from app.ingestion.base import MultiFileAdapter, ResultBuilder, UploadedFile
from app.ingestion.errors import IngestionError
from app.ingestion.fields import demographics, load_records, pick
from app.ingestion.identity import ContactResolver, resolve_identity
from app.ingestion.transcription import Transcriber, TranscriptionUnavailable
from app.schemas.complaint import IngestionResult

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = (".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm", ".mp4", ".aac", ".opus")
_SPOKEN_DIGITS = re.compile(r"\b\d(?:[ ,.-]{1,2}\d\b){3,}")


def join_spoken_digits(text: str) -> str:
    """'order 4 8 1 2 3 4' -> 'order 481234' (only runs of >= 4 single digits are joined)."""
    return _SPOKEN_DIGITS.sub(lambda m: re.sub(r"\D", "", m.group(0)), text)


class VoiceIngestionAdapter(MultiFileAdapter):
    name = "voice"
    source = Source.VOICE
    extensions = AUDIO_EXTENSIONS
    description = "Call recordings transcribed with Whisper; optional manifest CSV for caller, time and demographics."

    def __init__(self, resolver: ContactResolver, transcriber: Transcriber):
        self.resolver = resolver
        self.transcriber = transcriber

    def parse_files(self, files: list[UploadedFile]) -> IngestionResult:
        self.check_extensions(files, extra=(".csv",))
        manifests = [f for f in files if f.ext == ".csv"]
        audio = [f for f in files if f.ext != ".csv"]
        if len(manifests) > 1:
            raise IngestionError("Upload at most one manifest CSV per voice batch")
        if not audio:
            raise IngestionError("No audio files uploaded")
        if not self.transcriber.available:
            raise IngestionError(f"Voice transcription unavailable: {self.transcriber.unavailable_reason}", 503)

        meta: dict[str, dict] = {}
        if manifests:
            for row in load_records(manifests[0].content, manifests[0].filename):
                name = (pick(row, "filename", "file", "recording") or "").strip()
                if name:
                    meta[name.lower()] = row
            if not meta:
                raise IngestionError(f"{manifests[0].filename}: manifest needs a 'filename' column with values", 422)

        b = ResultBuilder(self.source)
        for f in audio:
            item = b.next_item()
            row = meta.get(f.filename.lower()) or meta.get(f.filename.rsplit("/", 1)[-1].lower()) or {}
            try:
                tr = self.transcriber.transcribe(f.content, f.filename)
            except TranscriptionUnavailable as exc:
                raise IngestionError(f"Voice transcription unavailable: {exc}", 503) from exc
            except Exception as exc:  # decode errors, corrupt audio... recorded per file, not swallowed
                logger.exception("Transcription failed for %s", f.filename)
                b.reject(item, f.filename, f"transcription failed: {type(exc).__name__}: {exc}", "audio")
                continue
            text = join_spoken_digits(tr.text).strip()
            if not text:
                b.reject(item, f.filename, "no speech detected in recording", "audio")
                continue
            customer_id, link = resolve_identity(self.resolver, pick(row, "customer_id"),
                                                 [("phone", pick(row, "phone", "caller", "caller_id"))])
            parts = [f"{tr.duration_seconds}s" if tr.duration_seconds else None,
                     f"lang {tr.language}" if tr.language else None,
                     None if row else ("not in manifest" if meta else None)]
            b.add(item, f.filename, {"complaint_text": text, "timestamp": pick(row, "timestamp", "call_time", "date"),
                                     "customer_id": customer_id, **demographics(row)},
                  link, ", ".join(p for p in parts if p) or None)
        return b.result()
