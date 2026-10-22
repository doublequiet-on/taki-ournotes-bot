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
from collections import Counter

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
        self.last_result = None
        self.sample_lock = threading.Lock()
        self.observation_lock = threading.Lock()
        self.observation = {'last_attempt_at':None,'last_successful_at':None,'consecutive_failures':0,'next_run_at':None,'running':False}

    def observation_snapshot(self):
        with self.observation_lock:return {**self.observation,'last_result':self.last_result}

    def sample_once(self):
        if not self.sample_lock.acquire(blocking=False):
            return {'code':'sampling_busy','_state':'partial'}
        try:
            with self.observation_lock:self.observation.update(last_attempt_at=time.time(),running=True)
            result=self._sample_once()
            with self.observation_lock:
                self.last_result=dict(result)
                self.observation['consecutive_failures']=self.observation['consecutive_failures']+1 if result.get('_state')=='partial' else 0
                if result.get('_state')!='partial' and any(row['code']=='sampled' for row in result.get('regions',[])):
                    self.observation['last_successful_at']=time.time()
            return result
        finally:
            with self.observation_lock:self.observation['running']=False
            self.sample_lock.release()

    def _sample_once(self):
        history=self.source.history
        if not history or not history.writable():
            self.last_state='历史写入关闭或暂停'
            return {'code':'history_unavailable','_state':'partial'}
        lookups,stored=Counter(),Counter()
        regions=[];partial=False;unknown_history=False
        for index,server in enumerate(self.servers):
            if self.stopping.is_set():
                regions.append({'server':server,'code':'sampling_stopped'});partial=True;break
            if self.source.foreground_active:
                for skipped in self.servers[index:]:history.failure(skipped,code='foreground_yield')
                self.last_state='让位于前台查询';regions.append({'server':server,'code':'foreground_yield'});partial=True;break
            if self.discovery_after.get(server,0)>self.clock():
                regions.append({'server':server,'code':'discovery_backoff'});continue
            try:
                deadline=self.source.deadline()
                observed=getattr(self.source,'read_event_observed',None)
                event=observed(server,deadline)[0] if callable(observed) else self.source.event(server,deadline)
                songs=[song for song in event.songs if song.enabled and song.collect_status in {'collecting','finalizing'}]
                if not songs:
                    self.discovery_after[server]=self.clock()+max(900,self.interval)
                    regions.append({'server':server,'code':'no_collecting_songs'});continue
                count=0;paused=False
                for song in songs:
                    if self.stopping.is_set() or self.source.foreground_active or not history.writable():
                        if self.source.foreground_active:
                            for skipped in self.servers[index:]:history.failure(skipped,code='foreground_yield')
                        self.last_state='采样暂停';regions.append({'server':server,'code':'sampling_paused'});partial=True;paused=True;break
                    observed=getattr(self.source,'read_board_observed',None)
                    if callable(observed):board,observation=observed(event,song,deadline)
                    else:board,observation=self.source.board(event,song,deadline),{'retrieval':'unknown','history_result':None}
                    lookups[observation['retrieval']]+=1
                    if observation.get('history_result') is None:unknown_history=True
                    else:stored[observation['history_result']]+=1
                    if not board.scores:partial=True
                    count+=1
                if paused:break
                regions.append({'server':server,'code':'sampled','board_count':count})
                self.last_state='周期采样已运行'
            except SourceError as exc:
                history.failure(server,code=exc.code)
                self.discovery_after[server]=self.clock()+max(self.interval,exc.retry_after)
                self.last_state='来源暂不可用，等待退避'
                regions.append({'server':server,'code':exc.code});partial=True
            except Exception as exc:
                history.failure(server,code='sampling_error')
                self.last_state='采样失败，等待下一周期'
                regions.append({'server':server,'code':'sampling_error','error_type':type(exc).__name__});partial=True
        pending=bool(history.status().get('clock_pending'))
        if pending:
            self.last_state='采样告警：存在时钟异常待核验观测，详见榜线历史状态';partial=True
        return {'code':'sampling_checked','regions':regions,'board_lookups':dict(lookups),'history_results':dict(stored),
                'new_valid_observations':None if unknown_history else stored.get('valid',0),
                'clock_pending':pending,'_state':'partial' if partial else 'succeeded'}

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
                delay=max(0,next_tick-now)
                with self.observation_lock:self.observation['next_run_at']=time.time()+delay
                await asyncio.sleep(delay)
        finally:
            self.stopping.set()
            self.last_state = "已停止"
            with self.observation_lock:self.observation.update(next_run_at=None,running=False)

    def start(self):
        if self.task is None or self.task.done():
            self.stopping.clear()
            self.task = asyncio.create_task(self.run())
        return self.task

    def stop(self):
        self.stopping.set()
        if self.task and not self.task.done() and not self.task.get_loop().is_closed():
            self.task.cancel()
