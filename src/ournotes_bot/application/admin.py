# L3: Authenticated read models; never expires requests, claims notices or refreshes sources.
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import secrets
import re
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone

from .backend_protocol import require, identity, PROTOCOL

STATES = ('processing', 'complete', 'partial', 'failed', 'uncertain', 'closed_unsent', 'unknown')


def business_state(value):
    return {'success': 'ok', 'no_result': 'no_result', 'empty': 'no_result',
            'unknown_entity': 'no_result', 'invalid_arguments': 'invalid',
            'ambiguous': 'invalid', 'unsupported': 'invalid',
            'data_unavailable': 'unavailable', 'unavailable': 'unavailable',
            'not_configured': 'unavailable', 'temporary_failure': 'unavailable', 'no_results':'no_result', 'mixed':'mixed'}.get(value, 'unknown')


def delivery_state(row, parts):
    """The sole projection used by overview, lists and detail. Platform facts only."""
    successful = sum(p['status'] == 'success' for p in parts)
    uncertain = any(p['status'] in ('sending', 'uncertain') for p in parts)
    if successful and successful == len(parts):
        return 'complete'
    if successful:
        return 'partial'
    if uncertain:
        return 'uncertain'
    if row['status'] in ('preparing', 'prepared'):
        return 'processing'
    if row['status'] in ('failed', 'failure') or any(p['status'] in ('failed', 'failure') for p in parts):
        return 'failed'
    if row['status'] in ('finished', 'expired', 'aborted'):
        return 'closed_unsent'
    return 'unknown'


def section(data=None, *, availability='available', reason=None, now=None):
    return {'availability': availability, 'observed_at': now, 'stale': False,
            'data': data, 'reason_code': reason}


class Admin:
    def __init__(self, backend):
        self.backend = backend
        self.cursor_key = secrets.token_bytes(32)

    def envelope(self, **sections):
        return {'schema_version': 1, 'generated_at': self.backend.clock(),
                'backend_epoch': self.backend.runtime,
                'capabilities': ['admin_read_v1', 'diagnostics_v1', 'ops_v1'], 'sections': sections}

    def export(self, request_id=None):
        """Bounded facts, not raw messages/configuration/logs. Aliases last one export."""
        if request_id is not None:
            data = {'request': self.detail(request_id)}
        else:
            data = {'overview': self.overview(), 'sources': self.sources(),
                    'attention': self.listing({'attention': 'true', 'limit': '25'}),'metrics':self.metrics(),'notices':self.notices()}
            notice=data['notices']['sections']['notices']
            if isinstance(notice.get('data'),dict):
                notice['data']={k:v for k,v in notice['data'].items() if k in ('items','targets','deployment_revision','report_status','candidate_count','time_semantics')}
        aliases = {}
        def anonymous(value):
            if isinstance(value, dict):
                return {k: (aliases.setdefault(v, 'group-'+str(len(aliases)+1)) if k == 'group_id' and v else anonymous(v))
                        for k,v in value.items() if k not in ('label', 'error', 'message', 'body')}
            if isinstance(value, list):
                return [anonymous(v) for v in value]
            return value
        result = {'schema': 'taki-diagnostic-v1', 'generated_at': self.backend.clock(),
                  'protocol': PROTOCOL, 'data': anonymous(data),
                  'scope': 'one_request' if request_id else 'instance_and_first_25_attention_requests'}
        require(len(json.dumps(result).encode()) <= 120000, 'diagnostic_export_too_large', 409)
        return result

    def view(self, row, *, detail=False, parts=None, metadata=False):
        if parts is None:
            parts = self.backend.ledger.parts(row['delivery'])
        if metadata is False:
            metadata = self.backend.diagnostics.read(row['id'])
        view = {'request_id': row['id'], 'delivery_id': row['delivery'], 'created_at': row['created'],
                'backend_epoch': row['runtime'],
                'lifecycle': row['status'], 'delivery_state': delivery_state(row, parts),
                'uncertain_parts': sum(p['status'] in ('sending', 'uncertain') for p in parts),
                'confirmed_parts': sum(p['status'] == 'success' for p in parts), 'part_count': len(parts),
                'scope': metadata['scope'] if metadata else None,
                'group_id': metadata['group_id'] if metadata else None,
                'command_kinds': [c['kind'] for c in metadata['commands']] if metadata else None,
                'command_count': metadata['commands'][0].get('total_commands') if metadata and metadata['commands'] else None,
                'business_outcome': 'unknown',
                'observation': 'partial',
                'observation_reason': 'transport_coverage_not_guaranteed' if metadata else 'historical_metadata_not_collected',
                'missing_reason': None if metadata else 'historical_metadata_not_collected'}
        view['delivery_reason'] = ('completed_without_output' if row['status'] == 'finished' and not parts else
            'platform_result_unconfirmed' if view['uncertain_parts'] else None)
        if metadata and metadata['commands']:
            outcomes = {business_state(c['outcome']) for c in metadata['commands']}
            view['business_outcome'] = next(iter(outcomes)) if len(outcomes) == 1 else 'mixed'
        if detail:
            view['model_attempts'] = self.backend.model_runtime.usage.request_view(row['id']) if self.backend.model_runtime else None
            view['parts'] = [{k: p[k] for k in ('id', 'position', 'mode', 'status')} |
                             {'platform_confirmed': p['status'] == 'success',
                              'primary_kind': next((m['kind'] for m in (metadata['parts'] or [])
                                                    if m['position'] == p['position']), None) if metadata else None}
                             for p in parts]
            view['spans'] = metadata['spans'] if metadata else []
            view['commands'] = metadata['commands'] if metadata else None
            view['part_observations'] = metadata['parts'] if metadata else None
        return view

    def rows(self):
        # The business owner already bounds this table to 4096. No lookup()/expiry side effects.
        return self.backend.ledger.admin_rows()

    def views(self):
        parts = self.backend.ledger.admin_parts()
        metadata = self.backend.diagnostics.summaries()
        return (self.view(row, parts=parts.get(row['delivery'], []), metadata=metadata.get(row['id']))
                for row in self.rows())

    def overview(self):
        now = self.backend.clock()
        retained = list(self.views())
        views = [v for v in retained if now-86400 <= v['created_at'] < now]
        counts = Counter(v['delivery_state'] for v in views)
        return self.envelope(
            service=section({'ready': self.backend.ready, 'closing': self.backend.closing,
                'natural_query': self.backend.parser.policy_capabilities() if hasattr(self.backend.parser,'policy_capabilities') else None,
                'native_model': self.backend.model_runtime.config.status() if self.backend.model_runtime else None,
                'model_scheduler':self.backend.models.status(),
                'model_permissions':self.backend.permissions.status(),
                'weighted_usage': self.backend.model_runtime.usage.summary() if self.backend.model_runtime else None}, now=now),
            requests=section({'from': now-86400, 'to': now, 'total': len(views),
                              'states': {s: counts[s] for s in STATES},
                              'retained_unresolved':sum(v['delivery_state'] in ('uncertain','partial') for v in retained),
                              'metadata_missing': sum(v['scope'] is None for v in views),
                              'recent': views[:5]}, now=now),
            observation=section(self.backend.diagnostics.health(), now=now))

    def listing(self, params):
        require(set(params) <= {'limit', 'from', 'to', 'cursor', 'state', 'group_id', 'attention'}, 'admin_parameters')
        try:
            limit = int(params.get('limit', 25))
            require(1 <= limit <= 100, 'admin_limit')
            now = self.backend.clock()
            filters = {k: params[k] for k in ('state', 'group_id', 'attention') if k in params}
            require(filters.get('state', 'unknown') in STATES, 'admin_state')
            require(filters.get('attention', 'false') in ('true', 'false'), 'admin_attention')
            if 'group_id' in filters:
                require(isinstance(filters['group_id'], str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', filters['group_id']), 'admin_group')
            start, end = float(params.get('from', now-86400)), float(params.get('to', now))
            point = None
            if params.get('cursor'):
                require(len(params['cursor']) <= 2048, 'admin_cursor')
                raw, signature = params['cursor'].split('.')
                require(hmac.compare_digest(hmac.new(self.cursor_key, raw.encode(), hashlib.sha256).hexdigest(), signature), 'admin_cursor')
                cursor = json.loads(base64.urlsafe_b64decode(raw + '='*(-len(raw)%4)))
                require(isinstance(cursor, dict) and set(cursor) == {'filters','from','to','point'}, 'admin_cursor')
                require(cursor['filters'] == filters, 'admin_cursor_filters')
                start, end, point = cursor['from'], cursor['to'], cursor['point']
                require(type(start) in (int,float) and type(end) in (int,float)
                        and isinstance(point, list) and len(point) == 2 and type(point[0]) in (int,float)
                        and math.isfinite(point[0]) and identity(point[1]), 'admin_cursor')
                require(('from' not in params or float(params['from']) == start) and
                        ('to' not in params or float(params['to']) == end), 'admin_cursor_window')
            require(math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= now+30 and end-start <= 604800, 'admin_window')
        except (ValueError, TypeError, KeyError, UnicodeError):
            require(False, 'admin_parameters')
        selected = []
        for view in self.views():
            if filters.get('attention') == 'true':
                if view['delivery_state'] not in ('uncertain', 'partial', 'processing'):
                    continue
            elif not start <= view['created_at'] < end:
                continue
            if point and (view['created_at'], view['request_id']) >= tuple(point):
                continue
            if 'state' in filters and view['delivery_state'] != filters['state']:
                continue
            if 'group_id' in filters and view['group_id'] != filters['group_id']:
                continue
            selected.append(view)
            if len(selected) > limit:
                break
        token = None
        if len(selected) > limit:
            selected = selected[:limit]
            last = selected[-1]
            raw = base64.urlsafe_b64encode(json.dumps({'from': start, 'to': end, 'filters': filters,
                'point': [last['created_at'], last['request_id']]}, separators=(',', ':')).encode()).decode().rstrip('=')
            token = raw+'.'+hmac.new(self.cursor_key, raw.encode(), hashlib.sha256).hexdigest()
        return self.envelope(requests=section({'items': selected, 'next_cursor': token, 'from': start, 'to': end}, now=now))

    def detail(self, request_id):
        require(identity(request_id), 'admin_request_id')
        row = self.backend.ledger.request(request_id)
        require(row is not None, 'unknown_request', 404)
        return self.envelope(request=section(self.view(row, detail=True), now=self.backend.clock()))

    def groups(self):
        return self.envelope(groups=section(self.backend.diagnostics.groups(self.backend.clock()-86400),
            availability='partial', reason='observed_groups_not_platform_roster', now=self.backend.clock()))

    def sources(self):
        catalog = self.backend.repository.capture_catalog()
        owner = self.backend.runtime_tasks
        observation = owner.observation_snapshot() if owner else None
        metadata = getattr(catalog, 'metadata', {})
        knowledge=self.backend.knowledge.status() if self.backend.knowledge else None
        if knowledge:
            knowledge={**{k:v for k,v in knowledge.items() if k not in ('issues','error')},
                       'reason_code':knowledge['error'],'issue_count':len(knowledge['issues'])}
        metrics=getattr(self.backend.repository,'song_metrics',None)
        return self.envelope(meta=section(metrics.status() if metrics else None,
            availability='available' if metrics else 'unsupported',now=self.backend.clock()),
            knowledge=section(knowledge,availability='available' if knowledge else 'unavailable',
                              reason=None if knowledge else 'knowledge_not_configured',now=self.backend.clock()),
            catalog=section({'cache_state': catalog.cache_state,
            'song_count': len(catalog.songs), 'data_version':metadata.get('data_version'),'text_version':metadata.get('text_version'),
            'card_count':metadata.get('card_count'),'support_card_count':metadata.get('support_card_count'),
            'last_successful_sync_at': catalog.last_successful_sync_at},
            availability='available' if catalog.cache_state == 'fresh' else 'partial',
            now=self.backend.clock()), background=section(observation, availability='partial',
            reason=None if observation else 'background_observation_not_collected', now=self.backend.clock()))

    def notice_targets(self, db, owner, groups):
        tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        has_claims='gateway_notice_claims' in tables
        claims={}
        if has_claims:
            claims={(gid,revision):dict(notice_id=nid,status=status,expires_at=expires) for nid,gid,revision,status,expires in db.execute(
                'SELECT nid,gid,revision,status,expires FROM gateway_notice_claims WHERE app=?',(owner.store.app_id,))}
        getter=getattr(owner,'deployment_identity',None)
        try:identity=getter() if callable(getter) else None
        except (OSError,ValueError,TypeError):identity=None
        revision=identity[0] if identity else None
        targets=[]
        notices={(gid,rev):status for gid,rev,status in db.execute('SELECT gid,revision,status FROM notices WHERE app=?',(owner.store.app_id,))}
        for group in groups:
            gid=group['group_id'];claim=claims.get((gid,revision));status=notices.get((gid,revision))
            if status in ('sent','failure','uncertain','sending','disabled'):stage=status
            elif claim:stage='lease_expired' if claim['status']=='claimed' and claim['expires_at']<=self.backend.clock() else claim['status']
            elif not revision:stage='deployment_target_unknown'
            elif not group['present']:stage='group_removed'
            elif group['allowed']==0:stage='permission_denied'
            elif group['allowed'] is None:stage='permission_unknown'
            else:stage='pending'
            targets.append({'group_id':gid,'deployment_revision':revision,'stage':stage,'ledger_status':status,'claim':claim,
                'observation_times':self.backend.operations.notice_times(claim['notice_id']) if claim else None})
        report_status=None
        if revision:
            row=db.execute('SELECT status FROM release_reports WHERE app=? AND revision=?',(owner.store.app_id,revision)).fetchone()
            report_status=row[0] if row else None
        return {'deployment_revision':revision,'report_status':report_status,'targets':targets,'claims_collected':has_claims,
                'candidate_count':db.execute('SELECT count(*) FROM groups WHERE app=?',(owner.store.app_id,)).fetchone()[0],
                'time_semantics':'server_observations_only; historical_times_unknown'}

    def notice_target(self, gid):
        try:return self._notice_target(gid)
        except sqlite3.Error:
            return self.envelope(notice_target=section(availability='unavailable',reason='notice_read_unavailable',now=self.backend.clock()))

    def _notice_target(self, gid):
        require(isinstance(gid,str) and re.fullmatch('[A-Za-z0-9_-]{1,128}',gid), 'admin_group')
        owner=self.backend.notices
        if owner is None:return self.envelope(notice_target=section(availability='unsupported',reason='notifications_disabled'))
        with closing(owner.store.connect()) as db:
            db.execute('PRAGMA query_only=ON');db.execute('PRAGMA busy_timeout=0');db.execute('BEGIN')
            groups=[dict(zip(('group_id','present','allowed','observed_at'),row)) for row in db.execute(
                'SELECT gid,present,allowed,event_at FROM groups WHERE app=? AND gid=?',(owner.store.app_id,gid))]
            data=self.notice_targets(db,owner,groups)
        return self.envelope(notice_target=section(data,availability='partial',reason='historical_notice_timestamps_missing',now=self.backend.clock()))

    def notices(self, params=None):
        try:return self._notices(params)
        except sqlite3.Error:
            return self.envelope(notices=section(availability='unavailable',reason='notice_read_unavailable',now=self.backend.clock()))

    def _notices(self, params=None):
        params = params or {}
        require(set(params) <= {'limit','cursor'}, 'admin_parameters')
        try:
            limit = int(params.get('limit',25))
            require(1 <= limit <= 100, 'admin_limit')
            window = point = None
            if params.get('cursor'):
                require(len(params['cursor']) <= 2048, 'admin_cursor')
                raw, signature = params['cursor'].split('.')
                require(hmac.compare_digest(hmac.new(self.cursor_key,raw.encode(),hashlib.sha256).hexdigest(),signature), 'admin_cursor')
                cursor = json.loads(base64.urlsafe_b64decode(raw+'='*(-len(raw)%4)))
                require(isinstance(cursor,dict) and set(cursor) == {'resource','window','point'}
                        and cursor['resource'] == 'notices', 'admin_cursor')
                window,point = cursor['window'],cursor['point']
                require(type(window) is int and type(point) is int and 0 < point <= window, 'admin_cursor')
        except (ValueError,TypeError,KeyError,UnicodeError):
            require(False,'admin_parameters')
        owner = self.backend.notices
        if owner is None:
            return self.envelope(notices=section(availability='unsupported', reason='notifications_disabled'))
        # Use the existing owner connection, never pending()/claims() or report generation.
        with closing(owner.store.connect()) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('PRAGMA busy_timeout=0')
            db.execute('BEGIN')
            if window is None:
                window = db.execute('SELECT COALESCE(MAX(rowid),0) FROM notices WHERE app=?',(owner.store.app_id,)).fetchone()[0]
            rows = db.execute('SELECT rowid,gid,revision,status FROM notices WHERE app=? AND rowid<=? AND rowid<? ORDER BY rowid DESC LIMIT ?',
                (owner.store.app_id,window,point if point is not None else window+1,limit+1)).fetchall()
            token = None
            if len(rows) > limit:
                rows = rows[:limit]
                raw = base64.urlsafe_b64encode(json.dumps({'resource':'notices','window':window,'point':rows[-1][0]},separators=(',',':')).encode()).decode().rstrip('=')
                token = raw+'.'+hmac.new(self.cursor_key,raw.encode(),hashlib.sha256).hexdigest()
            items = [dict(zip(('group_id','revision','status'),row[1:])) for row in rows]
            groups = [dict(zip(('group_id','present','allowed','observed_at'), row)) for row in db.execute(
                'SELECT gid,present,allowed,event_at FROM groups WHERE app=? ORDER BY gid LIMIT 128',
                (owner.store.app_id,))]
            projection=self.notice_targets(db,owner,groups[:limit])
        return self.envelope(notices=section({'items':items,'groups':groups,'next_cursor':token,**projection,
            'order':'record_insertion_desc','time_window':None},availability='partial',
            reason='notice_timestamps_not_collected',now=self.backend.clock()))

    def metrics(self):
        snapshot = self.backend.parser.metrics_snapshot()
        if not snapshot['available']:
            return self.envelope(metrics=section(snapshot, availability='unavailable', reason=snapshot['reason_code']))
        today = datetime.fromtimestamp(self.backend.clock(), timezone(timedelta(hours=8))).date()
        days = {str(today-timedelta(days=i)): snapshot['days'].get(str(today-timedelta(days=i))) for i in range(7)}
        observed = [bucket for bucket in days.values() if bucket is not None]
        totals = dict(sum((Counter(bucket) for bucket in observed), Counter()))
        return self.envelope(metrics=section({'today': days[str(today)], 'last_seven_days': totals,
            'observed_days': len(observed), 'missing_days': 7-len(observed), 'timezone': snapshot['timezone'],
            'write_available': snapshot['write_available'],
            'denominator': 'ask_total; route counts overlap work counters; no delivery or cost inference'},
            availability='available' if len(observed) == 7 else 'partial', now=self.backend.clock()))

    def telemetry(self, value, epoch):
        require(isinstance(value, dict) and set(value) == {'backend_epoch', 'events'}, 'telemetry_shape')
        require(value['backend_epoch'] == self.backend.runtime and self.backend.ledger.active(epoch), 'telemetry_epoch', 409)
        require(isinstance(value['events'], list) and len(value['events']) <= 32 and
                len(json.dumps(value).encode()) <= 24576, 'telemetry_size')
        validated = []
        seen = {}
        for item in value['events']:
            require(isinstance(item, dict) and set(item) == {'event_id','request_id','delivery_id','kind','position','attempt','duration_ms','outcome'}, 'telemetry_event')
            require(identity(item['event_id']) and identity(item['request_id']), 'telemetry_identity')
            row = self.backend.ledger.request(item['request_id'])
            require(row is not None and row['delivery'] == item['delivery_id'] and row['epoch'] == epoch
                    and row['runtime'] == self.backend.runtime, 'telemetry_association', 409)
            require(item['kind'] in ('upload','send','receipt_submit'), 'telemetry_kind')
            require(item['outcome'] in ('ok','error','unknown'), 'telemetry_outcome')
            require(type(item['duration_ms']) in (int,float) and math.isfinite(item['duration_ms'])
                    and 0 <= item['duration_ms'] <= 600000, 'telemetry_duration')
            parts = self.backend.ledger.parts(row['delivery'])
            part = next((p for p in parts if p['position'] == item['position']), None)
            require(type(item['position']) is int and part is not None, 'telemetry_part')
            require((item['kind'] == 'upload' and item['attempt'] is None) or
                    (item['attempt'] is not None and item['attempt'] == part['attempt']), 'telemetry_attempt')
            signature = tuple(item[k] for k in ('request_id','kind','position','attempt','duration_ms','outcome'))
            existing = seen.get(item['event_id']) or self.backend.diagnostics.event_signature(item['event_id'])
            require(existing is None or existing == signature, 'telemetry_conflict', 409)
            seen[item['event_id']] = signature
            validated.append(item)
        for item in validated:
            self.backend.diagnostics.span(item['event_id'], item['request_id'], item['kind'],
                item['duration_ms'], self.backend.clock(), item['position'], item['attempt'], item['outcome'])
        return {'accepted': len(validated)}
