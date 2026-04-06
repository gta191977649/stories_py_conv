from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock
from time import perf_counter
from typing import Iterator


@dataclass(slots=True)
class TimingStat:
    count: int = 0
    seconds: float = 0.0


class TimingRecorder:
    def __init__(self) -> None:
        self._stats: dict[str, TimingStat] = {}
        self._lock = Lock()

    def record(self, name: str, seconds: float, *, count: int = 1) -> None:
        with self._lock:
            stat = self._stats.setdefault(name, TimingStat())
            stat.count += count
            stat.seconds += float(seconds)

    @contextmanager
    def timed(self, name: str) -> Iterator[None]:
        start = perf_counter()
        try:
            yield
        finally:
            self.record(name, perf_counter() - start)

    def snapshot(self) -> dict[str, dict[str, float | int]]:
        with self._lock:
            return {
                name: {
                    "count": stat.count,
                    "seconds": round(stat.seconds, 6),
                }
                for name, stat in self._stats.items()
            }
