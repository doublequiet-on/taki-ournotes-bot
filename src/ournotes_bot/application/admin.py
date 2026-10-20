# L3: Authenticated read models; never expires requests, claims notices or refreshes sources.
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import secrets
import re
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone

from .backend_protocol import require, identity

STATES = ('processing', 'complete', 'partial', 'failed', 'uncertain', 'closed_unsent', 'unknown')


def business_state(value):
    return {'success': 'ok', 'no_result': 'no_result', 'empty': 'no_result',
            'unknown_entity': 'no_result', 'invalid_arguments': 'invalid',
            'ambiguous': 'invalid', 'unsupported': 'invalid',
            'data_unavailable': 'unavailable', 'unavailable': 'unavailable'}.get(value, 'unknown')


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
                'capabilities': ['admin_read_v1', 'diagnostics_v1'], 'sections': sections}

    def view(self, row, *, detail=False, parts=None, metadata=False):
        if parts is None:
            parts = self.backend.ledger.parts(row['delivery'])
        if metadata is False:
            metadata = self.backend.diagnostics.read(row['id'])
        view = {'request_id': row['id'], 'delivery_id': row['delivery'], 'created_at': row['created'],
                'lifecycle': row['status'], 'delivery_state': delivery_state(row, parts),
                'uncertain_parts': sum(p['status'] in ('sending', 'uncertain') for p in parts),
                'confirmed_parts': sum(p['status'] == 'success' for p in parts), 'part_count': len(parts),
                'scope': metadata['scope'] if metadata else None,
                'group_id': metadata['group_id'] if metadata else None,
                'command_kinds': [c['kind'] for c in metadata['commands']] if metadata else None,
                'business_outcome': 'unknown',
                'observation': 'partial',
                'observation_reason': 'transport_coverage_not_guaranteed' if metadata else 'historical_metadata_not_collected',
                'missing_reason': None if metadata else 'historical_metadata_not_collected'}
        if metadata and metadata['commands']:
            outcomes = {business_state(c['outcome']) for c in metadata['commands']}
            view['business_outcome'] = next(iter(outcomes)) if len(outcomes) == 1 else 'mixed'
        if detail:
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
        views = [v for v in self.views() if now-86400 <= v['created_at'] < now]
        counts = Counter(v['delivery_state'] for v in views)
        return self.envelope(
            service=section({'ready': self.backend.ready, 'closing': self.backend.closing}, now=now),
            requests=section({'from': now-86400, 'to': now, 'total': len(views),
                              'states': {s: counts[s] for s in STATES},
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
        return self.envelope(catalog=section({'cache_state': catalog.cache_state,
            'song_count': len(catalog.songs), 'last_successful_sync_at': catalog.last_successful_sync_at},
            now=self.backend.clock()), other_sources=section(availability='unsupported',
            reason='source_snapshot_not_instrumented', now=self.backend.clock()))

    def notices(self):
        owner = self.backend.notices
        if owner is None:
            return self.envelope(notices=section(availability='unsupported', reason='notifications_disabled'))
        # Use the existing owner connection, never pending()/claims() or report generation.
        with closing(owner.store.connect()) as db:
            items = [dict(zip(('group_id','revision','status'), row)) for row in db.execute(
                'SELECT gid,revision,status FROM notices WHERE app=? ORDER BY revision DESC,gid LIMIT 100',
                (owner.store.app_id,))]
            groups = [dict(zip(('group_id','present','allowed','observed_at'), row)) for row in db.execute(
                'SELECT gid,present,allowed,event_at FROM groups WHERE app=? ORDER BY gid LIMIT 128',
                (owner.store.app_id,))]
        return self.envelope(notices=section({'items': items, 'groups': groups}, now=self.backend.clock()))

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
        for item in value['events']:
            require(isinstance(item, dict) and set(item) == {'event_id','request_id','delivery_id','kind','position','attempt','duration_ms'}, 'telemetry_event')
            require(identity(item['event_id']) and identity(item['request_id']), 'telemetry_identity')
            row = self.backend.ledger.request(item['request_id'])
            require(row is not None and row['delivery'] == item['delivery_id'] and row['epoch'] == epoch
                    and row['runtime'] == self.backend.runtime, 'telemetry_association', 409)
            require(item['kind'] in ('upload','send','receipt_submit'), 'telemetry_kind')
            require(type(item['duration_ms']) in (int,float) and math.isfinite(item['duration_ms'])
                    and 0 <= item['duration_ms'] <= 600000, 'telemetry_duration')
            parts = self.backend.ledger.parts(row['delivery'])
            part = next((p for p in parts if p['position'] == item['position']), None)
            require(type(item['position']) is int and part is not None, 'telemetry_part')
            require((item['kind'] == 'upload' and item['attempt'] is None) or
                    (item['attempt'] is not None and item['attempt'] == part['attempt']), 'telemetry_attempt')
            validated.append(item)
        for item in validated:
            self.backend.diagnostics.span(item['event_id'], item['request_id'], item['kind'],
                item['duration_ms'], self.backend.clock(), item['position'], item['attempt'])
        return {'accepted': len(validated)}
