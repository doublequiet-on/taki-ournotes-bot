# L3: Backend-owned refresh, traits and optional history sampling; no platform connection.
import asyncio
import logging
import time
from ..sources.cutoff_sampler import HistorySampler

logger = logging.getLogger(__name__)


async def finish_thread(function):
    task = asyncio.create_task(asyncio.to_thread(function))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


class RuntimeTasks:
    def __init__(self, repository, settings):
        self.repository = repository
        self.tasks = []
        self.sampler = (HistorySampler(repository.event_cutoffs, servers=settings.cutoff_sampling_servers,
                                      interval=settings.cutoff_sampling_interval)
                        if settings.cutoff_sampling_enabled else None)
        repository.cutoff_sampler = self.sampler

    async def loop(self, function, interval, aligned=False):
        while True:
            if aligned:
                await asyncio.sleep(interval - time.time() % interval)
            try:
                await finish_thread(function)
            except Exception as exc:
                logger.error('Background refresh failed type=%s', type(exc).__name__)
            if not aligned:
                await asyncio.sleep(interval)

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
