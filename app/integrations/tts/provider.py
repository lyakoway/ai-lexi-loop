"""TTS provider protocol and the null implementation."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class TtsProvider(Protocol):
    """Anything that can synthesize speech audio for one phrase.

    ``accent`` participates in pronunciation and in the cache key; the
    concrete provider decides how (OpenAI TTS has no accent parameter —
    the accent is implied by the text's natural reading and by the voice).
    """

    name: str
    version: str

    async def synthesize(self, text: str, voice: str, accent: str) -> bytes:
        """Synthesize ``text`` and return audio bytes (mp3)."""
        ...  # pragma: no cover - protocol body


class NullTtsProvider:
    """No-op provider for tests and disabled TTS.

    Returns empty audio; callers treat the empty result as
    "unavailable" via the service layer.
    """

    name: str = "null"
    version: str = "null-0"

    async def synthesize(self, text: str, voice: str, accent: str) -> bytes:
        return b""
