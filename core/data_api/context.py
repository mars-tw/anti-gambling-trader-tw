"""Bounded synchronous execution context for read-only data providers."""

from __future__ import annotations

import math
import time
from typing import Any, Callable, Optional

from core.data_api.transport import DataAPIError

MAX_DEADLINE_SECONDS = 45.0
MAX_PAGES = 20
MAX_RETURNED_ROWS = 5000
_SLEEP_QUANTUM_SECONDS = 0.05


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


class SyncContext:
    """One bounded provider synchronization run.

    ``now_ms`` is captured once so pagination and completion decisions use one
    stable wall-clock boundary.  Deadline enforcement uses a monotonic clock.
    """

    def __init__(
        self,
        cancel_event: Any = None,
        deadline_seconds: float = 45,
        progress: Optional[Callable[[int, int], None]] = None,
        now_ms: Optional[int] = None,
    ) -> None:
        if (
            isinstance(deadline_seconds, bool)
            or not isinstance(deadline_seconds, (int, float))
        ):
            raise ValueError("deadline_seconds")
        deadline = float(deadline_seconds)
        if (
            not math.isfinite(deadline)
            or deadline <= 0.0
            or deadline > MAX_DEADLINE_SECONDS
        ):
            raise ValueError("deadline_seconds")
        if progress is not None and not callable(progress):
            raise ValueError("progress")
        if now_ms is None:
            captured_now_ms = time.time_ns() // 1_000_000
        elif _is_int(now_ms) and 0 <= now_ms <= 9_223_372_036_854_775_807:
            captured_now_ms = now_ms
        else:
            raise ValueError("now_ms")

        self.cancel_event = cancel_event
        self.deadline_seconds = deadline
        self.progress = progress
        self.now_ms = captured_now_ms
        self._started = time.monotonic()
        self._pages = 0
        self._records = 0

    @property
    def pages(self) -> int:
        return self._pages

    @property
    def records(self) -> int:
        return self._records

    def _cancelled(self) -> bool:
        event = self.cancel_event
        if event is None:
            return False
        is_set = getattr(event, "is_set", None)
        if callable(is_set):
            try:
                return bool(is_set())
            except Exception:
                # A broken cancellation primitive cannot be treated as a safe
                # instruction to continue a private-data request.
                return True
        return bool(event)

    def check(self) -> None:
        if self._cancelled():
            raise DataAPIError("cancelled")
        elapsed = time.monotonic() - self._started
        if not math.isfinite(elapsed) or elapsed >= self.deadline_seconds:
            raise DataAPIError("timeout")

    def remaining_seconds(self) -> float:
        self.check()
        remaining = self.deadline_seconds - (time.monotonic() - self._started)
        if not math.isfinite(remaining) or remaining <= 0.0:
            raise DataAPIError("timeout")
        return min(self.deadline_seconds, remaining)

    def advance(self, pages: int, records: int) -> None:
        self.check()
        if not _is_int(pages) or pages < 0:
            raise ValueError("pages")
        if not _is_int(records) or records < 0:
            raise ValueError("records")
        next_pages = self._pages + pages
        next_records = self._records + records
        if next_pages > MAX_PAGES or next_records > MAX_RETURNED_ROWS:
            raise DataAPIError("oversize")
        self._pages = next_pages
        self._records = next_records
        if self.progress is not None and (pages != 0 or records != 0):
            try:
                self.progress(self._pages, self._records)
            except DataAPIError:
                raise
            except Exception:
                raise DataAPIError("invalid_response") from None
        self.check()

    def sleep(self, seconds: float) -> None:
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            raise ValueError("seconds")
        duration = float(seconds)
        if not math.isfinite(duration) or duration < 0.0:
            raise ValueError("seconds")
        self.check()
        if duration == 0.0:
            return
        wake_at = time.monotonic() + duration
        while True:
            self.check()
            remaining_sleep = wake_at - time.monotonic()
            if remaining_sleep <= 0.0:
                break
            chunk = min(
                _SLEEP_QUANTUM_SECONDS,
                remaining_sleep,
                self.remaining_seconds(),
            )
            waiter = getattr(self.cancel_event, "wait", None)
            if callable(waiter):
                try:
                    if waiter(chunk):
                        raise DataAPIError("cancelled")
                except DataAPIError:
                    raise
                except Exception:
                    # Fall back to a bounded local sleep; ``check`` still runs
                    # at each quantum and fails closed on a broken is_set().
                    time.sleep(chunk)
            else:
                time.sleep(chunk)
        self.check()
