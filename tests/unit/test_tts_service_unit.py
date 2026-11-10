"""Unit tests for TTS cache key and hint fading (pure functions)."""

from app.services.tts import cache_audio_key


def test_cache_key_is_deterministic() -> None:
    key1 = cache_audio_key(
        text="Break a leg", voice="alloy", accent="us", provider_version="tts-1"
    )
    key2 = cache_audio_key(
        text="Break a leg", voice="alloy", accent="us", provider_version="tts-1"
    )
    assert key1 == key2


def test_cache_key_changes_with_each_dimension() -> None:
    base = {
        "text": "Break a leg",
        "voice": "alloy",
        "accent": "us",
        "provider_version": "tts-1",
    }
    changed_text = cache_audio_key(**{**base, "text": "Different phrase"})
    changed_voice = cache_audio_key(**{**base, "voice": "onyx"})
    changed_accent = cache_audio_key(**{**base, "accent": "uk"})
    changed_version = cache_audio_key(**{**base, "provider_version": "tts-2"})

    original = cache_audio_key(**base)
    assert changed_text != original
    assert changed_voice != original
    assert changed_accent != original
    assert changed_version != original


def test_cache_key_is_sha256_hex() -> None:
    import hashlib

    key = cache_audio_key(
        text="Break a leg", voice="alloy", accent="us", provider_version="tts-1"
    )
    expected = hashlib.sha256(b"Break a leg|alloy|us|tts-1").hexdigest()
    assert key == expected
