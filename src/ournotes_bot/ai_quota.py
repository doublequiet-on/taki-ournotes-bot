"""Single-process, fail-closed daily AI request budget."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable


BEIJING = timezone(timedelta(hours=8))


class QuotaUnavailable(Exception):
    """The budget record cannot be trusted or safely saved."""


class DailyQuota:
    def __init__(self, path: Path, clock: Callable[[], datetime] | None = None) -> None:
        self.path = path
        self._clock = clock or (lambda: datetime.now(BEIJING))
        self._lock = threading.Lock()
        self._has_written = False

    def reserve(self, limit: int) -> bool:
        """Persist one attempt before contacting the provider; False means exhausted."""
        with self._lock:
            try:
                today = self._clock().astimezone(BEIJING).date().isoformat()
                if not self.path.exists():
                    if self._has_written:
                        raise QuotaUnavailable("budget record disappeared")
                    recorded_day, used = today, 0
                else:
                    record = json.loads(self.path.read_text(encoding="utf-8"))
                    if (not isinstance(record, dict) or type(record.get("schema")) is not int
                            or record["schema"] != 1
                            or not isinstance(record.get("day"), str)
                            or not isinstance(record.get("used"), int)
                            or isinstance(record.get("used"), bool)
                            or record["used"] < 0):
                        raise QuotaUnavailable("invalid budget record")
                    recorded_day = record["day"]
                    if datetime.strptime(recorded_day, "%Y-%m-%d").date().isoformat() != recorded_day:
                        raise QuotaUnavailable("invalid budget day")
                    used = record["used"]
                    self._has_written = True
                if recorded_day > today:
                    raise QuotaUnavailable("system clock moved backwards")
                if recorded_day < today:
                    used = 0
                if used >= limit:
                    return False
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_name(self.path.name + ".tmp")
                try:
                    with temporary.open("w", encoding="utf-8") as stream:
                        json.dump({"schema": 1, "day": today, "used": used + 1}, stream)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, self.path)
                finally:
                    temporary.unlink(missing_ok=True)
                self._has_written = True
                return True
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                raise QuotaUnavailable("budget record unavailable") from exc
