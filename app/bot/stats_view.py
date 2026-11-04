"""Formatting of learning statistics for Telegram."""

from app.bot import texts
from app.services.stats import StatsOverview


def format_overview(stats: StatsOverview) -> str:
    """Render headline stats as Telegram HTML."""
    remember = (
        f"{stats.remember_ratio:.0%}" if stats.remember_ratio is not None else "—"
    )
    lines = [
        texts.STATS_TITLE,
        "",
        f"Выражений изучено: <b>{stats.expressions_shown}</b>",
        f"Пройдено карточек до конца: <b>{stats.expressions_completed}</b>",
        "Завершено уроков: "
        f"<b>{stats.lessons_completed}</b> <i>(с запуска учёта)</i>",
        f"Повторений: <b>{stats.reviews_total}</b>",
        f"Ответов «Помню»: <b>{remember}</b>",
        f"Ждут повторения: <b>{stats.due_count}</b>",
        f"Дней подряд: <b>{stats.streak_days}</b>",
    ]
    return "\n".join(lines)
