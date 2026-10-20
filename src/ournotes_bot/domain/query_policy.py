# L3: Immutable per-request execution ceiling, copied into preparation threads by asyncio.to_thread.
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionPolicy:
    natural_query: str = 'model'
    epoch: str = 'internal'
    global_revision: int = 0
    group_revision: int = 0


CURRENT_POLICY = ContextVar('taki_execution_policy', default=ExecutionPolicy())


def continuation_allowed(origin):
    mode = CURRENT_POLICY.get().natural_query
    return mode == 'model' or origin in ('direct','policy') or (mode == 'local' and origin == 'local')
