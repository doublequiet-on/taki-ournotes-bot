# L3: Optional cooperative cancellation signal for synchronous query work.
from contextvars import ContextVar
from threading import Event


thread_cancel_event: ContextVar[Event | None] = ContextVar('thread_cancel_event', default=None)
