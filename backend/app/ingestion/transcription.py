"""Speech-to-text for voice-call recordings.

Backends (``TRANSCRIBER`` setting):
  * ``local``  - faster-whisper (CTranslate2 Whisper) running offline. The model is downloaded
                 from Hugging Face on first use and loaded lazily, once per process.
  * ``openai`` - OpenAI transcription API (needs ``OPENAI_API_KEY``; audio leaves the machine).
  * ``none``   - voice ingestion disabled.

Failures are raised, never swallowed: the voice adapter records them per file.
"""
from __future__ import annotations

import io
import logging
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class TranscriptionUnavailable(RuntimeError):
    pass


@dataclass
class Transcript:
    text: str
    language: str | None = None
    duration_seconds: float | None = None


class Transcriber(ABC):
    name: str = "base"

    @property
    @abstractmethod
    def available(self) -> bool: ...

    @property
    def unavailable_reason(self) -> str | None:
        return None

    @abstractmethod
    def transcribe(self, content: bytes, filename: str) -> Transcript: ...

    def describe(self) -> dict:
        return {"backend": self.name, "available": self.available, "reason": self.unavailable_reason}


class DisabledTranscriber(Transcriber):
    name = "none"

    def __init__(self, reason: str):
        self._reason = reason

    @property
    def available(self) -> bool:
        return False

    @property
    def unavailable_reason(self) -> str | None:
        return self._reason

    def transcribe(self, content: bytes, filename: str) -> Transcript:
        raise TranscriptionUnavailable(self._reason)


class LocalWhisperTranscriber(Transcriber):
    name = "local"

    def __init__(self, model_size: str = "base", device: str = "cpu", compute_type: str = "int8"):
        self.model_size, self.device, self.compute_type = model_size, device, compute_type
        self._model = None
        self._lock = threading.Lock()
        try:
            import faster_whisper  # noqa: F401
            self._import_error = None
        except ImportError as exc:  # pragma: no cover - depends on environment
            self._import_error = f"faster-whisper is not installed ({exc}); run: pip install faster-whisper"

    @property
    def available(self) -> bool:
        return self._import_error is None

    @property
    def unavailable_reason(self) -> str | None:
        return self._import_error

    def describe(self) -> dict:
        return {**super().describe(), "model": self.model_size, "device": self.device, "loaded": self._model is not None}

    def _load(self):
        with self._lock:
            if self._model is None:
                if self._import_error:
                    raise TranscriptionUnavailable(self._import_error)
                from faster_whisper import WhisperModel

                logger.info("Loading Whisper model '%s' on %s (%s)", self.model_size, self.device, self.compute_type)
                self._model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)
        return self._model

    def transcribe(self, content: bytes, filename: str) -> Transcript:
        model = self._load()
        segments, info = model.transcribe(io.BytesIO(content), vad_filter=True, beam_size=5)
        text = " ".join(s.text.strip() for s in segments).strip()  # segments is lazy: decoding happens here
        return Transcript(text=text, language=info.language, duration_seconds=round(float(info.duration), 1))


class OpenAITranscriber(Transcriber):
    name = "openai"

    def __init__(self, api_key: str | None, model: str, timeout: float = 120.0):
        self.model = model
        self._client = None
        if api_key:
            from openai import OpenAI

            self._client = OpenAI(api_key=api_key, timeout=timeout)

    @property
    def available(self) -> bool:
        return self._client is not None

    @property
    def unavailable_reason(self) -> str | None:
        return None if self._client else "TRANSCRIBER=openai requires OPENAI_API_KEY"

    def transcribe(self, content: bytes, filename: str) -> Transcript:
        if self._client is None:
            raise TranscriptionUnavailable(self.unavailable_reason)
        resp = self._client.audio.transcriptions.create(model=self.model, file=(filename, content))
        return Transcript(text=(resp.text or "").strip())


def build_transcriber(settings) -> Transcriber:
    kind = (settings.transcriber or "none").lower()
    if kind == "local":
        return LocalWhisperTranscriber(settings.whisper_model, settings.whisper_device, settings.whisper_compute_type)
    if kind == "openai":
        return OpenAITranscriber(settings.openai_api_key, settings.openai_transcription_model)
    return DisabledTranscriber("Voice transcription disabled (TRANSCRIBER=none)")
