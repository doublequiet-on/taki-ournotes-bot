"""Bounded, single-use standby slots for a Hermes pilot lane."""

import asyncio
from itertools import count
import logging
from time import monotonic

from .backend_protocol import ProtocolError


logger = logging.getLogger(__name__)


class StandbyLane:
    def __init__(self, prepare, capacity):
        self.prepare = prepare
        self.capacity = capacity
        self.enabled = False
        self.running = 0
        self.ready = []
        self.initializing = set()
        self.waiting = []
        self.sequence = count()
        self.changed = asyncio.Condition()

    def start(self):
        self.enabled = True
        self.refill()

    def refill(self):
        while self.enabled and len(self.ready)+len(self.initializing)<2:
            task = asyncio.create_task(self.prewarm())
            self.initializing.add(task)
            task.add_done_callback(self.initializing.discard)

    async def prewarm(self):
        try:
            worker = await self.prepare()
        except (OSError, asyncio.TimeoutError):
            logger.warning('Hermes standby initialization failed')
            return
        async with self.changed:
            if self.enabled:
                self.ready.append(worker)
                self.changed.notify_all()
                return
        await worker.close()

    async def claim(self, deadline, *, priority, wait):
        async with self.changed:
            ticket = (priority, next(self.sequence))
            self.waiting.append(ticket)
            try:
                while self.enabled:
                    if monotonic()>=deadline:
                        break
                    if self.ready and self.running<self.capacity and ticket==min(self.waiting):
                        worker = self.ready.pop(0)
                        if worker.alive:
                            self.running += 1
                            self.refill()
                            return worker
                        await worker.close()
                        continue
                    self.refill()
                    if not wait:
                        break
                    try:
                        await asyncio.wait_for(self.changed.wait(), deadline-monotonic())
                    except asyncio.TimeoutError:
                        break
                raise ProtocolError('chat_hermes_busy', 503)
            finally:
                self.waiting.remove(ticket)
                self.changed.notify_all()

    async def release(self, worker):
        try:
            await worker.close()
        finally:
            async with self.changed:
                self.running -= 1
                self.refill()
                self.changed.notify_all()

    async def stop(self):
        async with self.changed:
            self.enabled = False
            ready, self.ready = self.ready, []
            self.changed.notify_all()
        await asyncio.gather(*(worker.close() for worker in ready), *self.initializing)
