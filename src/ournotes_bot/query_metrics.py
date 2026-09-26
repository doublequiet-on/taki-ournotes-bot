"""Privacy-safe daily counters for /问 routing outcomes."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping

from .ai_quota import BEIJING


METRIC_NAMES = frozenset({
    "ask_total",
    "local_success",
    "local_terminal_reject",
    "cache_hit_success",
    "cache_hit_terminal",
    "ai_route_requested",
    "ai_parse_requested",
    "ai_repair_requested",
    "ai_success",
    "ai_empty",
    "ai_unsupported",
    "ai_unknown_entity",
    "ai_ambiguous",
    "ai_invalid_output",
    "ai_provider_error",
    "ai_data_unavailable",
    "quota_exhausted",
    "ai_prompt_tokens",
    "ai_completion_tokens",
    "ai_total_tokens",
    "ai_cache_hit_tokens",
    "ai_cache_miss_tokens",
})


class QueryMetrics:
    """Best-effort counters; metrics failures never fail a user query."""

    def __init__(self, path: Path, clock: Callable[[], datetime] | None = None) -> None:
        self.path = path
        self._clock = clock or (lambda: datetime.now(BEIJING))
        self._lock = threading.Lock()
        self._disabled = False

    def increment(self, name: str, amount: int = 1) -> bool:
        return self.increment_many({name: amount})

    def increment_many(self, changes: Mapping[str, int]) -> bool:
        if (not changes or any(name not in METRIC_NAMES for name in changes)
                or any(not isinstance(value, int) or isinstance(value, bool) or value < 0
                       for value in changes.values())):
            return False
        with self._lock:
            if self._disabled:
                return False
            try:
                record = self._load()
                day = self._clock().astimezone(BEIJING).date().isoformat()
                bucket = record["days"].setdefault(day, {})
                for name, amount in changes.items():
                    current = bucket.get(name, 0)
                    if not isinstance(current, int) or isinstance(current, bool) or current < 0:
                        raise ValueError("invalid metric value")
                    bucket[name] = current + amount
                self._write(record)
                return True
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                self._disabled = True
                return False

    def _load(self) -> dict:
        if not self.path.exists():
            return {"schema": 1, "days": {}}
        record = json.loads(self.path.read_text(encoding="utf-8"))
        if (not isinstance(record, dict) or record.get("schema") != 1
                or not isinstance(record.get("days"), dict)):
            raise ValueError("invalid metrics record")
        for day, bucket in record["days"].items():
            datetime.strptime(day, "%Y-%m-%d")
            if not isinstance(bucket, dict) or any(
                name not in METRIC_NAMES or not isinstance(value, int)
                or isinstance(value, bool) or value < 0
                for name, value in bucket.items()
            ):
                raise ValueError("invalid metrics bucket")
        return record

    def _write(self, record: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump(record, stream, ensure_ascii=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    @property
    def available(self) -> bool:
        return not self._disabled
