# L3
# Input: Existing cutoff repository, explicit server/interval settings and stop signal.
# Output: One bounded runtime worker; four-server collection and discovery status.
# Pos: Data / MoeNotes sampler; see L2-2.md.
# Effects: Uses existing source coalescing/rate limits; writes independent history only.
"""Runtime-owned sampler. Importing or constructing this class starts nothing."""
from __future__ import annotations

import asyncio
import threading
import time

from .moenotes_events import SERVERS, SourceError


class HistorySampler:
    def __init__(self, source, *, servers=("jp", "tw", "kr", "en"), interval=300,
                 clock=time.monotonic):
        self.source = source
        self.servers = tuple(dict.fromkeys("tw" if s == "hk" else s for s in servers))
        if not self.servers or any(s not in SERVERS for s in self.servers):
            raise ValueError("invalid sampling servers")
        self.interval = max(60, int(interval))
        self.clock = clock
        self.task = None
        self.stopping = threading.Event()
        self.discovery_after = {}
        self.last_state = "未启动"

    def sample_once(self):
        history = self.source.history
        if not history or not history.writable():
            self.last_state = "历史写入关闭或暂停"
            return
        for index, server in enumerate(self.servers):
            if self.stopping.is_set():
                return
            if self.source.foreground_active:
                for skipped in self.servers[index:]:
                    history.failure(skipped, code="foreground_yield")
                self.last_state = "让位于前台查询"
                return
            if self.discovery_after.get(server, 0) > self.clock():
                continue
            try:
                deadline = self.source.deadline()
                event = self.source.event(server, deadline)
                songs = [s for s in event.songs if s.enabled and s.collect_status in {"collecting", "finalizing"}]
                if not songs:
                    self.discovery_after[server] = self.clock() + max(900, self.interval)
                for song in songs:
                    if self.stopping.is_set() or self.source.foreground_active or not history.writable():
                        if self.source.foreground_active:
                            for skipped in self.servers[index:]:
                                history.failure(skipped, code="foreground_yield")
                        self.last_state = "采样暂停"
                        return
                    self.source.board(event, song, deadline)
                self.last_state = "周期采样已运行"
            except SourceError as exc:
                history.failure(server, code=exc.code)
                self.discovery_after[server] = self.clock() + max(self.interval, exc.retry_after)
                self.last_state = "来源暂不可用，等待退避"
            except Exception:
                history.failure(server, code="sampling_error")
                self.last_state = "采样失败，等待下一周期"

    async def run(self):
        next_tick = self.clock()
        try:
            while not self.stopping.is_set():
                work = asyncio.create_task(asyncio.to_thread(self.sample_once))
                try:
                    await asyncio.shield(work)
                except asyncio.CancelledError:
                    self.stopping.set()
                    await work
                    raise
                # Keep a 300s cadence without adding network duration each cycle.
                # Skip missed ticks instead of queueing catch-up observations.
                next_tick += self.interval
                now = self.clock()
                if next_tick <= now:
                    next_tick += (int((now - next_tick) // self.interval) + 1) * self.interval
                await asyncio.sleep(max(0, next_tick - now))
        finally:
            self.stopping.set()
            self.last_state = "已停止"

    def start(self):
        if self.task is None or self.task.done():
            self.stopping.clear()
            self.task = asyncio.create_task(self.run())
        return self.task

    def stop(self):
        self.stopping.set()
        if self.task and not self.task.done() and not self.task.get_loop().is_closed():
            self.task.cancel()
