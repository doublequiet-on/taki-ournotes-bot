# L3: Backend-owned refresh, traits and optional history sampling; no platform connection.
import asyncio
import logging
import time
import threading
from ..sources.cutoff_sampler import HistorySampler

logger = logging.getLogger(__name__)


async def drain_task(task):
    """Keep ownership until task settles, then propagate the caller's cancellation."""
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.done() and task.cancelled():
                raise
            cancelled = True
        except BaseException:
            if cancelled:
                raise asyncio.CancelledError() from None
            raise
    if cancelled:
        raise asyncio.CancelledError()
    return result


async def finish_thread(function, *args):
    # The awaiting owner stays registered even after repeated cancellation.
    return await drain_task(asyncio.create_task(asyncio.to_thread(function, *args)))


class RuntimeTasks:
    def __init__(self, repository, settings, knowledge=None):
        self.repository = repository
        self.knowledge = knowledge
        self.tasks = []
        self.named_tasks = {}
        self.closing = False
        self.resource_locks = {}
        self.observations = {}
        self.observation_lock = threading.Lock()
        self.sampler = (HistorySampler(repository.event_cutoffs, servers=settings.cutoff_sampling_servers,
                                      interval=settings.cutoff_sampling_interval)
                        if settings.cutoff_sampling_enabled else None)
        repository.cutoff_sampler = self.sampler

    def execute(self, function, name=None):
        name=name or function.__name__
        with self.resource_locks.setdefault(name,threading.Lock()):
            with self.observation_lock:
                previous=self.observations.get(name,{})
                self.observations[name]={**previous,'last_attempt_at':time.time(),'running':True}
            try:
                result=function()
                code=result.get('code') if isinstance(result,dict) else None
                success=code in (None,'fresh','cached','synchronized','upstream_unchanged','ready','empty')
                with self.observation_lock:
                    current=self.observations[name]
                    current.update(running=False,last_finished_at=time.time(),last_error_code=None if success else code,
                        consecutive_failures=0 if success else current.get('consecutive_failures',0)+1)
                    if success:current['last_successful_at']=time.time()
                return result
            except Exception as exc:
                with self.observation_lock:
                    current=self.observations[name]
                    current.update(running=False,last_finished_at=time.time(),last_error_code=type(exc).__name__,
                        consecutive_failures=current.get('consecutive_failures',0)+1)
                raise

    async def loop(self, function, interval, aligned=False):
        name=function.__name__
        try:
            while True:
                delay=interval-time.time()%interval if aligned else 0
                with self.observation_lock:
                    self.observations.setdefault(name,{})['next_run_at']=time.time()+delay
                if delay:await asyncio.sleep(delay)
                try:
                    await finish_thread(lambda:self.execute(function))
                except Exception as exc:
                    logger.error('Background refresh failed type=%s',type(exc).__name__)
                if not aligned:
                    with self.observation_lock:
                        self.observations[name]['next_run_at']=time.time()+interval
                    await asyncio.sleep(interval)
        finally:
            with self.observation_lock:
                self.observations.setdefault(name,{}).update(running=False,next_run_at=None)

    def synchronize_catalog(self,force,expected):
        return self.execute(lambda:self.repository.synchronize_catalog(force,expected),'refresh_catalog')

    def observation_snapshot(self):
        with self.observation_lock:
            result = {key: dict(value) for key,value in self.observations.items()}
        return {'refreshes': result, 'observation_scope':'current_process','tasks':{name:{'alive':not task.done()} for name,task in self.named_tasks.items()},'sampling': {'enabled': self.sampler is not None,
            'state': self.sampler.last_state if self.sampler else None,
            'servers': list(self.sampler.servers) if self.sampler else None,
            'interval_seconds': self.sampler.interval if self.sampler else None,
            'observation':self.sampler.observation_snapshot() if self.sampler else None}}

    def refresh_catalog(self):
        self.repository.refresh()
        snapshot = self.repository.capture_catalog()
        log = logger.info if snapshot.cache_state in ('fresh','cached') else logger.warning
        log('Catalog refresh state=%s songs=%d', snapshot.cache_state, len(snapshot.songs))
        return {'code':snapshot.cache_state}

    def watch_knowledge(self):
        return self.knowledge.watch()

    def refresh_knowledge(self):
        return self.knowledge.refresh()

    def start(self):
        return self.recover()

    def recover(self):
        if self.closing:raise RuntimeError('runtime_closing')
        restored,unchanged=[],[]
        definitions={'catalog':(self.refresh_catalog,1800,True),'traits':(self.repository.refresh_song_traits,300,False)}
        if self.knowledge is not None:
            definitions.update(knowledge_watch=(self.watch_knowledge, 1, False),
                               knowledge_refresh=(self.refresh_knowledge, 30, False))
        for name,(function,interval,aligned) in definitions.items():
            old=self.named_tasks.get(name)
            if old is not None and not old.done():
                unchanged.append(name);continue
            if old is not None and not old.cancelled():old.exception()
            self.named_tasks[name]=asyncio.create_task(self.loop(function,interval,aligned));restored.append(name)
        if self.sampler:
            old=self.named_tasks.get('sampling')
            if old is None or old.done():
                self.named_tasks['sampling']=self.sampler.start();restored.append('sampling')
            else:unchanged.append('sampling')
        self.tasks=list(self.named_tasks.values())
        return {'code':'background_tasks_checked','restored':restored,'unchanged':unchanged,'sampling_enabled':self.sampler is not None}

    async def close(self):
        self.closing=True
        if self.sampler:
            self.sampler.stop()
        for task in self.tasks:
            task.cancel()
        try:
            await drain_task(asyncio.gather(*self.tasks, return_exceptions=True))
        finally:
            self.tasks.clear()
            self.named_tasks.clear()
