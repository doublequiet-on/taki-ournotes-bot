# L3: Backend-owned refresh, traits and optional history sampling; no platform connection.
import asyncio
import logging
import time
import threading
from ..sources.cutoff_sampler import HistorySampler

logger = logging.getLogger(__name__)


async def finish_thread(function):
    task = asyncio.create_task(asyncio.to_thread(function))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Preserve cancellation even if the thread fails while being drained.
        # Letting its exception escape here turns shutdown back into a retry loop.
        await asyncio.gather(task, return_exceptions=True)
        raise


class RuntimeTasks:
    def __init__(self, repository, settings):
        self.repository = repository
        self.tasks = []
        self.observations = {}
        self.observation_lock = threading.Lock()
        self.sampler = (HistorySampler(repository.event_cutoffs, servers=settings.cutoff_sampling_servers,
                                      interval=settings.cutoff_sampling_interval)
                        if settings.cutoff_sampling_enabled else None)
        repository.cutoff_sampler = self.sampler

    async def loop(self, function, interval, aligned=False):
        while True:
            if aligned:
                await asyncio.sleep(interval - time.time() % interval)
            try:
                name = function.__name__
                with self.observation_lock:
                    previous = self.observations.get(name, {})
                    self.observations[name] = {**previous, 'last_attempt_at': time.time(), 'running': True}
                await finish_thread(function)
                with self.observation_lock:
                    self.observations[name] = {**self.observations[name], 'running': False,
                        'last_finished_at': time.time(), 'last_error_code': None}
            except Exception as exc:
                with self.observation_lock:
                    self.observations[name] = {**self.observations[name], 'running': False,
                        'last_finished_at': time.time(), 'last_error_code': type(exc).__name__}
                logger.error('Background refresh failed type=%s', type(exc).__name__)
            if not aligned:
                await asyncio.sleep(interval)

    def observation_snapshot(self):
        with self.observation_lock:
            result = {key: dict(value) for key,value in self.observations.items()}
        return {'refreshes': result, 'sampling': {'enabled': self.sampler is not None,
            'state': self.sampler.last_state if self.sampler else None,
            'servers': list(self.sampler.servers) if self.sampler else None,
            'interval_seconds': self.sampler.interval if self.sampler else None}}

    def refresh_catalog(self):
        self.repository.refresh()
        snapshot = self.repository.capture_catalog()
        log = logger.info if snapshot.cache_state in ('fresh','cached') else logger.warning
        log('Catalog refresh state=%s songs=%d', snapshot.cache_state, len(snapshot.songs))

    def start(self):
        if self.tasks:
            return
        self.tasks = [asyncio.create_task(self.loop(self.refresh_catalog, 1800, True)),
                      asyncio.create_task(self.loop(self.repository.refresh_song_traits, 300))]
        if self.sampler:
            self.tasks.append(self.sampler.start())

    async def close(self):
        if self.sampler:
            self.sampler.stop()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
