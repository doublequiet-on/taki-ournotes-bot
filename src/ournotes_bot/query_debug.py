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
