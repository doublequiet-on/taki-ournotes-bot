# L3
# Input: 成功 API 调用、有效 AI 检索的显式计数事件，以及快照／重置请求。
# Output: snapshot 返回 QueryDebugSnapshot，只有 successful_api_calls 与 useful_ai_queries 两个计数。
# Pos: Query / Natural 的进程内调试计数叶子；见 L2-2.md。
# Effects/Dependencies: 在锁内修改实例计数，模块提供共享实例；不持久化、不记录问句正文，重启后清零。

"""Small process-local counters for operator-visible AI query debugging."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class QueryDebugSnapshot:
    successful_api_calls: int
    useful_ai_queries: int


class QueryDebugCounters:
    """Thread-safe counters that intentionally reset when the process restarts."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._successful_api_calls = 0
        self._useful_ai_queries = 0

    def record_api_success(self) -> None:
        with self._lock:
            self._successful_api_calls += 1

    def record_useful_query(self) -> None:
        with self._lock:
            self._useful_ai_queries += 1

    def snapshot(self) -> QueryDebugSnapshot:
        with self._lock:
            return QueryDebugSnapshot(
                successful_api_calls=self._successful_api_calls,
                useful_ai_queries=self._useful_ai_queries,
            )

    def reset(self) -> None:
        """Reset both counters; intended for isolated tests, not a bot command."""
        with self._lock:
            self._successful_api_calls = 0
            self._useful_ai_queries = 0


QUERY_DEBUG_COUNTERS = QueryDebugCounters()
