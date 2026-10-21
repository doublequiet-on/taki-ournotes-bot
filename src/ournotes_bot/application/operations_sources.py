# L3: Fixed catalog/artwork administrator actions using the live repository owner.
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from .backend_protocol import require
from ..config import runtime_data_dir
from ..sources.haneoka import prepare_catalog_assets as artwork
from ..sources.haneoka.game_assets import MANIFEST, prepare_note_assets, invalidate_assets as invalidate_game_assets
from ..sources.errors import ChartDataError


class SourceOperations:
    def __init__(self, backend):
        self.backend, self.repository = backend, backend.repository

    def target(self):
        metadata = self.repository.capture_catalog().metadata
        return {key:metadata.get(key) for key in ('data_version','text_version')}

    def prepare(self, params):
        require(not params, 'ops_parameters')
        require(self.repository.data_source == 'haneoka', 'ops_source_unsupported', 409)
        return {'snapshot':self.target()}

    def snapshot(self, params):
        require(self.target() == params['snapshot'], 'ops_snapshot_changed', 409)
        saved = self.repository.capture_source_snapshot()
        require(saved is not None, 'ops_catalog_unavailable', 409)
        require({k:saved['identities'][s]['releaseId'] for k,s in (('data_version','jp'),('text_version','intl'))} == params['snapshot'],
                'ops_snapshot_changed', 409)
        artwork.validate(saved, time.time())
        return saved

    def synchronize(self, params, force=False):
        require(self.target() == params['snapshot'], 'ops_snapshot_changed', 409)
        result = self.repository.synchronize_catalog(force, params['snapshot'])
        if result['code'] in ('updated_unsaved','refresh_failed_cached','snapshot_changed'):
            result['_state'] = 'partial'
        elif result['code'] == 'no_available_data':
            result['_state'] = 'failed'
        return result

    def assets(self, params, *, repair=False, retry=False):
        saved = self.snapshot(params)
        base = runtime_data_dir()
        cache = base/'haneoka-asset-cache'
        cache.mkdir(parents=True, exist_ok=True)
        manifest_path = cache/'prepared-artwork-v1.json'
        prior = {}
        if manifest_path.is_file():
            try:
                require(manifest_path.stat().st_size <= 4_000_000, 'ops_manifest_size')
                prior = json.loads(manifest_path.read_text('utf-8')).get('assets', {})
                if not isinstance(prior,dict):
                    prior = {}
            except (OSError, ValueError):
                prior = {}
        urls = artwork.catalog_urls(saved,time.time())
        failed_path = cache/'repair-failures-v1.json'
        fixed_retry = None
        if retry:
            require(failed_path.is_file() and failed_path.stat().st_size <= 1_000_000, 'ops_retry_unavailable', 409)
            failure = json.loads(failed_path.read_text('utf-8'))
            require(failure['identities'] == saved['identities'], 'ops_snapshot_changed', 409)
            urls = tuple(url for url in urls if url in failure['urls'])
            fixed_retry = set(failure.get('fixed_assets', []))
        deadline = time.monotonic()+1200
        def one(url):
            path = cache/(hashlib.sha256(url.encode()).hexdigest()+'.png')
            if time.monotonic() >= deadline:
                return url,None,'budget_exhausted'
            try:
                if path.is_file() and path.stat().st_size <= 6_000_000:
                    raw = path.read_bytes(); digest = artwork.check_asset(raw)
                    if url not in prior or prior[url]['sha256'] == digest:
                        return url,{'sha256':digest,'bytes':len(raw),'file':path.name},'reused'
            except (OSError,ValueError,KeyError,TypeError):
                pass
            if not repair:
                return url,None,'missing_or_invalid'
            try:
                raw = artwork.download_asset(url,timeout=45);digest=artwork.check_asset(raw)
                artwork.atomic_write(path,raw)
                return url,{'sha256':digest,'bytes':len(raw),'file':path.name},'repaired'
            except (OSError,ValueError,ChartDataError):
                return url,None,'download_or_validation_failed'
        with ThreadPoolExecutor(max_workers=2) as pool:
            rows = list(pool.map(one,urls))
        repaired = [url for url,_,state in rows if state == 'repaired']
        failures = [url for url,item,_ in rows if item is None]
        manifest_saved = None
        if repair:
            from ..visuals import invalidate_visual_assets
            invalidate_visual_assets(repaired)
            valid = {url:item for url,item,_ in rows if item is not None}
            if retry:
                valid = {**prior, **valid}
            try:
                artwork.atomic_write(manifest_path,json.dumps({'schema':1,'identities':saved['identities'],'assets':valid},sort_keys=True).encode())
                manifest_saved = True
            except OSError:
                manifest_saved = False
        fixed_failed, fixed_repaired = [], []
        for name,spec in MANIFEST.items():
            if fixed_retry is not None and name not in fixed_retry:
                continue
            if time.monotonic() >= deadline:
                fixed_failed.append(name)
                continue
            path = base/'game-assets'/(spec['sha256']+Path(name).suffix)
            try:
                valid = path.is_file() and path.stat().st_size<=4_000_000 and hashlib.sha256(path.read_bytes()).hexdigest()==spec['sha256']
                if not valid and repair:
                    prepare_note_assets(base/'game-assets',names=(name,));fixed_repaired.append(name)
                elif not valid:
                    fixed_failed.append(name)
            except (OSError,ValueError,ChartDataError):
                fixed_failed.append(name)
        if repair:
            invalidate_game_assets(fixed_repaired)
            try:
                artwork.atomic_write(failed_path,json.dumps({'identities':saved['identities'],'urls':failures,'fixed_assets':fixed_failed}).encode())
            except OSError:
                manifest_saved = False
        count = len(failures)+len(fixed_failed)
        partial = count or manifest_saved is False
        return {'code':'assets_partial' if partial else 'assets_verified','snapshot':params['snapshot'],'manifest_saved':manifest_saved,
                'checked':len(rows),'reused':sum(state=='reused' for _,_,state in rows),
                'repaired':len(repaired)+len(fixed_repaired),'failed':count,'failure_codes':sorted({state for _,item,state in rows if item is None}),
                'fixed_asset_failures':fixed_failed,'scope':'catalog_snapshot_and_builtin_assets','_state':'partial' if partial else 'succeeded'}

    def register(self):
        if self.repository.data_source != 'haneoka':
            return
        jobs = self.backend.operations
        jobs.register('catalog_sync', self.prepare, self.synchronize)
        jobs.register('catalog_reload', self.prepare, lambda p:self.synchronize(p,True))
        jobs.register('assets_verify', self.prepare, self.assets)
        jobs.register('assets_repair', self.prepare, lambda p:self.assets(p,repair=True))
        jobs.register('assets_retry', self.prepare, lambda p:self.assets(p,repair=True,retry=True))
