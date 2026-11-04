"""Profile-related domain vocabulary.

Values are stored as short stable keys; Russian labels live in the bot
texts layer so the domain stays presentation-free.
"""

from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from app.domain.transliteration import TRANSLITERATION


class Level(StrEnum):
    """CEFR level; ``UNKNOWN`` means the user has not estimated it yet."""

    UNKNOWN = "unknown"
    A1 = "A1"
    A2 = "A2"
    B1 = "B1"
    B2 = "B2"
    C1 = "C1"
    C2 = "C2"


class LearningGoal(StrEnum):
    """Why the user is learning the language."""

    WORK = "work"
    TRAVEL = "travel"
    EXAMS = "exams"
    RELOCATION = "relocation"
    MEDIA = "media"
    FOR_MYSELF = "for_myself"


class Accent(StrEnum):
    """Preferred pronunciation variant."""

    US = "us"
    UK = "uk"


class Topic(StrEnum):
    """Interest topics used to pick lesson material."""

    EVERYDAY = "everyday"
    WORK = "work"
    TRAVEL = "travel"
    TECHNOLOGY = "technology"
    BUSINESS = "business"
    SCIENCE = "science"
    CULTURE = "culture"
    SPORT = "sport"
    MOVIES = "movies"
    MUSIC = "music"
    GAMES = "games"


# Popular IANA zones offered as one-tap choices; any other zone can be
# entered by name or city during onboarding.
POPULAR_TIMEZONES: tuple[str, ...] = (
    "Europe/Moscow",
    "Europe/Kaliningrad",
    "Europe/Samara",
    "Asia/Yekaterinburg",
    "Asia/Omsk",
    "Asia/Novosibirsk",
    "Asia/Krasnoyarsk",
    "Asia/Irkutsk",
    "Asia/Yakutsk",
    "Asia/Vladivostok",
    "Asia/Magadan",
    "Asia/Kamchatka",
    "Europe/Kyiv",
    "Europe/Minsk",
    "Europe/Riga",
    "Europe/Almaty",
    "Europe/London",
    "Europe/Berlin",
)

# Valid lesson hours offered as one-tap choices.
LESSON_HOUR_CHOICES: tuple[int, ...] = tuple(range(6, 24))


def resolve_timezone(query: str) -> str | None:
    """Resolve user input to a valid IANA zone key.

    Accepts a zone name (``Europe/Moscow``, case-insensitive) or a city
    name (``москва``/``moscow``) matched against the last path segment of
    known zones. Returns ``None`` when nothing matches.
    """
    candidates: list[str] = [query.strip()]
    transliterated = TRANSLITERATION.get(query.strip().lower())
    if transliterated is not None:
        candidates.append(transliterated)

    for candidate in candidates:
        normalized = candidate.replace(" ", "_").lower()
        if normalized.startswith("/"):
            continue
        try:
            zone = ZoneInfo(candidate.replace(" ", "_"))
        except (ValueError, ZoneInfoNotFoundError, UnicodeEncodeError):
            # Not a zone identifier: fall through to city matching.
            zone = None
        if zone is not None and zone.key in available_timezones():
            return zone.key
        # City match on the last path segment.
        for available in available_timezones():
            if available.rsplit("/", 1)[-1].lower() == normalized:
                return available
    return None
