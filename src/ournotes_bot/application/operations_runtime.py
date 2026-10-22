# L3: Runtime recovery and bounded source checks; no service control or platform sends.
from .backend_protocol import require
from ..sources.moenotes_events import SourceError


class RuntimeOperations:
    def __init__(self, backend):
        self.backend=backend

    def prepare(self, params):
        require(not params,'ops_parameters')

    async def recover(self, params):
        require(self.backend.runtime_tasks is not None and not self.backend.closing,'runtime_unavailable',503)
        return self.backend.runtime_tasks.recover()

    def sample(self, params):
        owner=self.backend.runtime_tasks
        require(owner is not None and owner.sampler is not None and not owner.closing,'sampling_not_configured',409)
        return owner.sampler.sample_once()

    def events(self, params):
        source=self.backend.repository.event_cutoffs
        rows=[]
        servers=tuple(dict.fromkeys('tw' if s=='hk' else s for s in self.backend.settings.cutoff_sampling_servers))
        for server in servers:
            if source.foreground_active:
                rows.append({'server':server,'code':'foreground_yield'});continue
            try:
                event,observation=source.read_event_observed(server,source.deadline())
                rows.append({'server':server,'code':'event_checked','event_id':event.event_id,'status':event.status,
                             'song_count':len(event.songs),'source':event.source,**observation})
            except SourceError as exc:
                rows.append({'server':server,'code':exc.code,'retry_after_seconds':exc.retry_after})
        return {'code':'events_checked','regions':rows,'_state':'succeeded' if rows and all(r['code']=='event_checked' for r in rows) else 'partial'}

    def upstream(self, params):
        return {'code':'upstream_checked','source':'haneoka_catalog','versions':self.backend.repository.check_catalog_upstream()}

    def register(self):
        jobs=self.backend.operations
        jobs.register('tasks_recover',self.prepare,self.recover)
        jobs.register('events_check',self.prepare,self.events)
        if self.backend.runtime_tasks.sampler is not None:
            jobs.register('sample_once',self.prepare,self.sample)
        if self.backend.repository.data_source=='haneoka':
            jobs.register('upstream_check',self.prepare,self.upstream)
