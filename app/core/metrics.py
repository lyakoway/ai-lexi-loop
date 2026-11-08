"""Minimal in-process metrics with a Prometheus text exposition.

Counters live in the application process; ``GET /metrics`` renders them
in the Prometheus text format, so a scraper can be attached later
without code changes. No user data is included — only aggregate counts.

``observe_milliseconds`` records simple cumulative histograms with fixed
buckets: enough for latency monitoring without a metrics library.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from threading import Lock

#: Fixed latency buckets (milliseconds), chosen for LLM-ish latencies.
LATENCY_BUCKETS: tuple[float, ...] = (
    100.0,
    250.0,
    500.0,
    1000.0,
    2000.0,
    5000.0,
    10000.0,
    30000.0,
)


@dataclass
class Histogram:
    """Cumulative bucket counts with a running sum and count."""

    buckets: dict[float, int] = field(
        default_factory=lambda: dict.fromkeys(LATENCY_BUCKETS, 0)
    )
    sum: float = 0.0
    count: int = 0

    def observe(self, value: float) -> None:
        for boundary in self.buckets:
            if value <= boundary:
                self.buckets[boundary] += 1
        self.sum += value
        self.count += 1


_lock = Lock()
_counters: dict[str, int] = {}
_histograms: dict[str, Histogram] = {}


def inc_counter(name: str, amount: int = 1) -> None:
    """Increment a named counter."""
    with _lock:
        _counters[name] = _counters.get(name, 0) + amount


def observe_milliseconds(name: str, value: float) -> None:
    """Record one duration into a fixed-bucket histogram."""
    with _lock:
        _histograms.setdefault(name, Histogram()).observe(value)


def get_counters() -> Mapping[str, int]:
    """A snapshot of current counter values."""
    with _lock:
        return dict(_counters)


def get_histograms() -> Mapping[str, Histogram]:
    """A snapshot of the recorded histograms."""
    with _lock:
        return dict(_histograms)


def reset_counters() -> None:
    """Test helper: clear all counters and histograms."""
    with _lock:
        _counters.clear()
        _histograms.clear()


def render_prometheus() -> str:
    """Render counters and histograms in the Prometheus text format."""
    with _lock:
        counters = dict(_counters)
        histograms = {
            name: Histogram(
                buckets=dict(histogram.buckets),
                sum=histogram.sum,
                count=histogram.count,
            )
            for name, histogram in _histograms.items()
        }
    lines = []
    for name, value in sorted(counters.items()):
        if not _valid_metric_name(name):
            continue
        lines.append(f"# TYPE {name} counter")
        lines.append(f"{name} {value}")
    for name, histogram in sorted(histograms.items()):
        if not _valid_metric_name(name):
            continue
        lines.append(f"# TYPE {name} histogram")
        for boundary in LATENCY_BUCKETS:
            count = histogram.buckets.get(boundary, 0)
            lines.append(f'{name}_bucket{{le="{boundary:.0f}"}} {count}')
        lines.append(f'{name}_bucket{{le="+Inf"}} {histogram.count}')
        lines.append(f"{name}_sum {histogram.sum}")
        lines.append(f"{name}_count {histogram.count}")
    return "\n".join(lines) + "\n"


def _valid_metric_name(name: str) -> bool:
    return bool(name) and all(char.isalnum() or char == "_" for char in name)
