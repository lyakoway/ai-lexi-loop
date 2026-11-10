"""Repository for the TTS audio cache metadata."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TtsAudio
from app.repositories.errors import wrap_storage_errors


class TtsAudioRepository:
    """Data access for audio cache rows (keyed by sha256)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_key(self, cache_key: str) -> TtsAudio | None:
        """The cache row for the key, or ``None``."""
        async with wrap_storage_errors():
            result = await self._session.execute(
                select(TtsAudio).where(TtsAudio.cache_key == cache_key)
            )
            return result.scalar_one_or_none()

    async def create(
        self,
        *,
        cache_key: str,
        text: str,
        provider: str,
        provider_version: str,
        voice: str,
        accent: str,
        file_path: str,
    ) -> TtsAudio:
        """Insert a cache row (flushed, not committed)."""
        async with wrap_storage_errors():
            row = TtsAudio(
                cache_key=cache_key,
                text=text,
                provider=provider,
                provider_version=provider_version,
                voice=voice,
                accent=accent,
                file_path=file_path,
            )
            self._session.add(row)
            await self._session.flush()
            await self._session.refresh(row)
            return row
