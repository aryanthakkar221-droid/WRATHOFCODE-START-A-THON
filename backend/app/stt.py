"""Speech-to-text with OpenAI Whisper (handles German well).

Needs OPENAI_API_KEY. Without it the API answers 501 and the frontend can fall back to the
browser's Web Speech API and send `text` instead of `audio`.
"""
import io
import os
from typing import Optional

from .ai import client

STT_MODEL = os.environ.get("OPENAI_STT_MODEL", "whisper-1")


class STTUnavailable(Exception):
    pass


def stt_enabled() -> bool:
    return client() is not None


def transcribe(audio: bytes, filename: str, language: Optional[str] = None) -> str:
    c = client()
    if c is None:
        raise STTUnavailable(
            "Server speech-to-text is off (no OPENAI_API_KEY). Send `text` instead, e.g. from "
            "the browser's Web Speech API."
        )
    f = io.BytesIO(audio)
    f.name = filename or "audio.webm"  # the API infers the format from the name
    kwargs = {"model": STT_MODEL, "file": f}
    if language:
        kwargs["language"] = language
    return c.audio.transcriptions.create(**kwargs).text.strip()
