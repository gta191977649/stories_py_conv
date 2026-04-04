from __future__ import annotations

import shutil
import sys
from typing import Callable

try:
    from tqdm.auto import tqdm
except ModuleNotFoundError:  # pragma: no cover - exercised only when tqdm is absent
    class tqdm:  # type: ignore[no-redef]
        def __init__(
            self,
            total: int = 0,
            desc: str = "",
            unit: str = "item",
            dynamic_ncols: bool = True,
            position: int = 0,
            leave: bool = True,
            disable: bool = False,
        ) -> None:
            self.total = total
            self.n = 0
            self.disable = disable

        def update(self, step: int = 1) -> None:
            self.n += step

        def set_postfix_str(self, _value: str) -> None:
            return

        def close(self) -> None:
            return

        @staticmethod
        def write(message: str) -> None:
            print(message, flush=True)


class ProgressDisplay:
    def __init__(self, total_phases: int, *, title: str = "Stories Convertor") -> None:
        self.title = title
        self.disable = not sys.stderr.isatty()
        self.overall = tqdm(
            total=max(0, total_phases),
            desc=self._clip_text(title, self._max_desc_width()),
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
            self.overall.set_postfix_str(self._clip_text(label, self._max_overall_postfix_width()))
        self.current = tqdm(
            total=max(0, total),
            desc=self._clip_text(label, self._max_desc_width()),
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
                self.current.set_postfix_str(self._clip_text(detail, self._max_current_postfix_width()))
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

    def _terminal_columns(self) -> int:
        return max(40, shutil.get_terminal_size(fallback=(100, 20)).columns)

    def _max_desc_width(self) -> int:
        columns = self._terminal_columns()
        return max(18, min(32, columns // 3))

    def _max_overall_postfix_width(self) -> int:
        columns = self._terminal_columns()
        return max(12, columns - self._max_desc_width() - 40)

    def _max_current_postfix_width(self) -> int:
        columns = self._terminal_columns()
        return max(10, columns - self._max_desc_width() - 52)

    @staticmethod
    def _clip_text(value: str, limit: int) -> str:
        text = " ".join(value.split())
        if limit <= 0:
            return ""
        if len(text) <= limit:
            return text
        if limit <= 3:
            return text[:limit]
        return text[: limit - 3] + "..."
