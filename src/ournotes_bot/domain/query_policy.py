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
# Request-scoped state and execution hook; application supplies the trusted permit.
CURRENT_QUERY_SCOPE = ContextVar('taki_query_scope', default=None)
CURRENT_MODEL_START = ContextVar('taki_model_start', default=None)
CURRENT_EXECUTION_CHECK = ContextVar('taki_execution_check', default=None)
CURRENT_CONTENT_DEADLINE = ContextVar('taki_content_deadline', default=None)


def continuation_allowed(origin):
    mode = CURRENT_POLICY.get().natural_query
    return mode == 'model' or origin in ('direct','policy') or (mode == 'local' and origin == 'local')
