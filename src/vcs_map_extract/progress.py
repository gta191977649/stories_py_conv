from __future__ import annotations

import sys
from typing import Callable

from tqdm.auto import tqdm


class ProgressDisplay:
    def __init__(self, total_phases: int, *, title: str = "Stories Convertor") -> None:
        self.title = title
        self.disable = not sys.stderr.isatty()
        self.overall = tqdm(
            total=max(0, total_phases),
            desc=title,
            unit="phase",
            dynamic_ncols=True,
            position=0,
            leave=True,
            disable=self.disable,
        )
        self.current: tqdm | None = None

    def start_phase(self, label: str, total: int, *, unit: str = "item") -> None:
        self._close_current()
        if not self.disable:
            self.overall.set_postfix_str(label)
        self.current = tqdm(
            total=max(0, total),
            desc=label,
            unit=unit,
            dynamic_ncols=True,
            position=1,
            leave=False,
            disable=self.disable,
        )
        if self.disable:
            print(f"[progress] {label}", flush=True)

    def advance(self, step: int = 1, *, detail: str | None = None) -> None:
        if self.current is not None:
            if detail and not self.disable:
                self.current.set_postfix_str(detail)
            self.current.update(step)

    def finish_phase(self, *, summary: str | None = None) -> None:
        if self.current is not None and self.current.total is not None and self.current.n < self.current.total:
            self.current.update(self.current.total - self.current.n)
        if summary:
            self.log(summary)
        self._close_current()
        self.overall.update(1)

    def log(self, message: str) -> None:
        if self.disable:
            print(message, flush=True)
        else:
            tqdm.write(message)

    def sink(self) -> Callable[[str], None]:
        return self.log

    def close(self) -> None:
        self._close_current()
        self.overall.close()

    def _close_current(self) -> None:
        if self.current is not None:
            self.current.close()
            self.current = None

