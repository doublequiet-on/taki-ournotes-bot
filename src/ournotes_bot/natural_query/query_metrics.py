# L3
# Input: 旧统计 JSON 基础路径、可选时钟、允许的计数名及非负增量；token usage 由调用方映射为计数。
# Output: increment／increment_many 返回 bool；独立 .v2 文件保存 schema 2 的按北京时间日期聚合匿名计数，不迁移旧文件。
# Pos: Query / Natural 的持久匿名查询指标；见 L2-2.md。
# Effects/Dependencies: 在实例锁内读写独立 JSON；记录或保存故障后停记，不阻断查询、不保存问句正文。

"""Privacy-safe daily counters for /问 routing outcomes."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping

from .ai_quota import BEIJING
from .query_validation import OutcomeCode


METRIC_NAMES = frozenset({
    "ask_total",
    "local_success",
    "local_handled",
    "local_parsed",
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
}) | frozenset("outcome_" + code.value for code in OutcomeCode) | frozenset(
    "ai_" + code.value for code in OutcomeCode
) | frozenset({"route_local", "route_cache", "route_model"})


class QueryMetrics:
    """Best-effort counters; metrics failures never fail a user query."""

    def __init__(self, path: Path, clock: Callable[[], datetime] | None = None) -> None:
        # Old releases reject unknown counter names. Keep their file untouched,
        # including on rollback; v2 counts start here, never reinterpret history.
        self.path = path.with_name(path.stem + ".v2" + path.suffix)
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
            return {"schema": 2, "days": {}}
        record = json.loads(self.path.read_text(encoding="utf-8"))
        if (not isinstance(record, dict) or record.get("schema") != 2
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

    def snapshot(self) -> dict:
        """Read-only validated snapshot; never creates files or changes writer availability."""
        # A query worker may hold this lock through fsync. Console reads run on
        # the backend event loop and must not wait for that worker.
        if not self._lock.acquire(blocking=False):
            return {'available': False, 'write_available': not self._disabled,
                    'reason_code': 'metrics_snapshot_busy', 'timezone': 'Asia/Shanghai', 'days': None}
        try:
            try:
                exists = self.path.is_file()
                record = self._load()
                return {'available': exists, 'write_available': not self._disabled,
                        'reason_code': None if exists else 'metrics_not_collected',
                        'timezone': 'Asia/Shanghai', 'days': record['days']}
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                return {'available': False, 'write_available': not self._disabled,
                        'reason_code': 'metrics_snapshot_invalid', 'timezone': 'Asia/Shanghai', 'days': None}
        finally:
            self._lock.release()
