"""Minimal in-process metrics with a Prometheus text exposition.

Counters live in the application process; ``GET /metrics`` renders them
in the Prometheus text format, so a scraper can be attached later
without code changes. No user data is included — only aggregate counts.
"""

from collections.abc import Mapping
from threading import Lock

_lock = Lock()
_counters: dict[str, int] = {}


def inc_counter(name: str, amount: int = 1) -> None:
    """Increment a named counter."""
    with _lock:
        _counters[name] = _counters.get(name, 0) + amount


def get_counters() -> Mapping[str, int]:
    """A snapshot of current counter values."""
    with _lock:
        return dict(_counters)


def reset_counters() -> None:
    """Test helper: clear all counters."""
    with _lock:
        _counters.clear()


def render_prometheus() -> str:
    """Render counters in the Prometheus text exposition format."""
    snapshot = get_counters()
    lines = []
    for name, value in sorted(snapshot.items()):
        if not _valid_metric_name(name):
            continue
        lines.append(f"# TYPE {name} counter")
        lines.append(f"{name} {value}")
    return "\n".join(lines) + "\n"


def _valid_metric_name(name: str) -> bool:
    return bool(name) and all(char.isalnum() or char == "_" for char in name)
