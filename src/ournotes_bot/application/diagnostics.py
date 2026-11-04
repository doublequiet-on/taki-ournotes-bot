# L3: Bounded, best-effort observation store. Never grants delivery permission.
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path


class Diagnostics:
    """One backend writer; explicitly degraded on storage failure, without ledger changes."""

    def __init__(self, path, *, enabled=True):
        self.db = None
        self.reason = None
        self.dropped = 0
        self.next_cleanup = 0
        if not enabled:
            self.reason = 'diagnostics_disabled'
            return
        try:
            self.db = sqlite3.connect(Path(path), timeout=0)
            self.db.row_factory = sqlite3.Row
            self.db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS requests(
                    id TEXT PRIMARY KEY, delivery TEXT UNIQUE, epoch TEXT, created REAL,
                    scope TEXT, group_id TEXT, commands TEXT, parts TEXT);
                CREATE INDEX IF NOT EXISTS diagnostic_created ON requests(created);
                CREATE TABLE IF NOT EXISTS spans(
                    event_id TEXT PRIMARY KEY, request_id TEXT REFERENCES requests(id) ON DELETE CASCADE,
                    kind TEXT, position INTEGER, attempt TEXT, duration_ms REAL, observed_at REAL);
                CREATE INDEX IF NOT EXISTS diagnostic_spans_request ON spans(request_id);
                CREATE TABLE IF NOT EXISTS span_results(
                    event_id TEXT PRIMARY KEY REFERENCES spans(event_id) ON DELETE CASCADE,
                    outcome TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS chat_events(
                    id INTEGER PRIMARY KEY, request_id TEXT NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL, data TEXT NOT NULL, observed_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS diagnostic_chat_request ON chat_events(request_id);
                CREATE TABLE IF NOT EXISTS conversation_events(
                    id INTEGER PRIMARY KEY, request_id TEXT NOT NULL, group_id TEXT NOT NULL,
                    kind TEXT NOT NULL, data TEXT NOT NULL, observed_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS diagnostic_conversation_group
                    ON conversation_events(group_id,observed_at);
                PRAGMA foreign_keys=ON;
            ''')
        except (OSError, sqlite3.Error):
            self.reason = 'diagnostics_storage_unavailable'
            if self.db is not None:
                self.db.close()
            self.db = None

    def write(self, operation):
        if self.db is None:
            self.dropped += 1
            return False
        try:
            with self.db:
                operation(self.db)
            return True
        except (sqlite3.Error, ValueError, TypeError):
            self.reason = 'diagnostics_write_failed'
            self.dropped += 1
            return False

    def admitted(self, request, delivery, epoch, now, commands):
        def save(db):
            db.execute('DELETE FROM requests WHERE created<?', (now-86400,))
            db.execute('''DELETE FROM requests WHERE id IN
                (SELECT id FROM requests ORDER BY created DESC,id DESC LIMIT -1 OFFSET 4095)''')
            db.execute('INSERT OR IGNORE INTO requests VALUES(?,?,?,?,?,?,?,NULL)',
                       (request.request_id, delivery, epoch, now, request.kind,
                        request.conversation_id if request.kind == 'group' else None,
                        json.dumps(commands)))
        return self.write(save)

    def expire(self, now):
        if self.db is None or now < self.next_cleanup:return
        self.next_cleanup=now+60
        def clean(db):
            db.execute('DELETE FROM requests WHERE created<?',(now-86400,))
            db.execute('DELETE FROM conversation_events WHERE observed_at<?',(now-86400,))
        self.write(clean)

    @staticmethod
    def chat_metadata(kind, data):
        if kind not in ('context','image','tool','decision','error','finished','control','task','research'):
            raise ValueError('chat_event_kind')
        fields={'messages','assistant_messages','image_markers','input_images','vision_approved','image_detail',
                'automatic','loaded_images','status','name','decision','model_attempts','delivered',
                'action','total','completed','entities','elapsed_ms','task_missing','research_fallback',
                'source','reason','continuation','window_seconds',
                'input_required','input_limit','system_bytes','tool_bytes'}
        budget_fields={'hour_limit_micro','day_limit_micro','hour_cost_micro','day_cost_micro',
                       'hour_known_micro','hour_unknown_micro','hour_reserved_micro','unknown_cost_micro',
                       'requested_cost_micro','available_cost_micro','controller_cost_micro'}
        fields |= budget_fields
        codes={'status','name','decision','image_detail','action','source','reason'}
        if 'image_detail' in data and data['image_detail'] not in ('none','low','high'):
            raise ValueError('chat_event_data')
        if not set(data)<=fields or any(not (
                (type(value) is int and 0<=value<=10**12) if key in budget_fields else
                (type(value) is bool or type(value) is int and 0<=value<=10**6
                 or key in codes and isinstance(value,str) and re.fullmatch('[a-z_]{1,64}',value))
                ) for key,value in data.items()):
            raise ValueError('chat_event_data')
        return json.dumps(data)

    def chat_event(self, request_id, kind, now, **data):
        """Bounded operational metadata; never accepts dialogue, URLs or tool arguments."""
        def save(db):
            payload=self.chat_metadata(kind,data)
            if db.execute('SELECT count(*) FROM chat_events WHERE request_id=?',(request_id,)).fetchone()[0]>=32:
                self.dropped+=1;return
            db.execute('INSERT INTO chat_events(request_id,kind,data,observed_at) VALUES(?,?,?,?)',
                       (request_id,kind,payload,now))
        return self.write(save)

    def conversation_event(self, request_id, group_id, kind, now, **data):
        """Record a decision before admission, without creating a delivery or retaining its message."""
        def save(db):
            payload=self.chat_metadata(kind,data)
            db.execute('DELETE FROM conversation_events WHERE observed_at<?',(now-86400,))
            db.execute('''DELETE FROM conversation_events WHERE id IN
                (SELECT id FROM conversation_events ORDER BY observed_at DESC,id DESC LIMIT -1 OFFSET 4095)''')
            db.execute('INSERT INTO conversation_events(request_id,group_id,kind,data,observed_at) VALUES(?,?,?,?,?)',
                       (request_id,group_id,kind,payload,now))
        return self.write(save)

    def group_events(self, group_id, *, limit=50):
        """Newest scoped decisions and admitted execution events; contains metadata only."""
        if self.db is None:return []
        try:
            rows=self.db.execute('''SELECT request_id,kind,data,observed_at FROM (
                SELECT e.request_id,e.kind,e.data,e.observed_at FROM chat_events e
                JOIN requests r ON r.id=e.request_id WHERE r.group_id=?
                UNION ALL
                SELECT request_id,kind,data,observed_at FROM conversation_events WHERE group_id=?
                ) ORDER BY observed_at DESC LIMIT ?''',(group_id,group_id,max(1,min(limit,100))))
            return [dict(request_id=row['request_id'],kind=row['kind'],data=json.loads(row['data']),
                         observed_at=row['observed_at']) for row in rows]
        except (sqlite3.Error,ValueError,TypeError):
            self.reason='diagnostics_read_failed'
            return []

    def prepared(self, request_id, parts):
        data = [{'position': i, 'kind': 'image' if p.image else 'text',
                 'command_index': p.command_index,
                 'parse_origin': p.parse_origin, 'processing_route': p.processing_route,
                 'fallback_available': bool(p.image and p.text),
                 'business_status': p.business_status or 'unknown'} for i, p in enumerate(parts)]
        def save(db):
            row = db.execute('SELECT commands FROM requests WHERE id=?',(request_id,)).fetchone()
            if row:
                commands = json.loads(row[0])
                for part in data:
                    index = part['command_index']
                    if index is not None and 0 <= index < len(commands):
                        commands[index].update(parse_origin=part['parse_origin'],processing_route=part['processing_route'])
                db.execute('UPDATE requests SET parts=?,commands=? WHERE id=?',(json.dumps(data),json.dumps(commands),request_id))
        return self.write(save)

    def outcomes(self, request_id, outcomes):
        def save(db):
            row = db.execute('SELECT commands FROM requests WHERE id=?', (request_id,)).fetchone()
            if row:
                commands = json.loads(row[0])
                for command, outcome in zip(commands, outcomes):
                    command['outcome'] = outcome
                db.execute('UPDATE requests SET commands=? WHERE id=?', (json.dumps(commands), request_id))
        return self.write(save)

    def span(self, event_id, request_id, kind, duration_ms, now, position=None, attempt=None, outcome='unknown'):
        def save(db):
            if db.execute('SELECT 1 FROM spans WHERE event_id=?', (event_id,)).fetchone():
                return
            if db.execute('SELECT count(*) FROM spans WHERE request_id=?', (request_id,)).fetchone()[0] >= 32:
                self.dropped += 1
                return
            db.execute('INSERT INTO spans VALUES(?,?,?,?,?,?,?)',
                       (event_id, request_id, kind, position, attempt, duration_ms, now))
            db.execute('INSERT INTO span_results VALUES(?,?)', (event_id,outcome))
        return self.write(save)

    def event_signature(self, event_id):
        if self.db is None:
            return None
        try:
            row = self.db.execute('''SELECT request_id,kind,position,attempt,duration_ms,
                COALESCE(outcome,'unknown') FROM spans LEFT JOIN span_results USING(event_id)
                WHERE event_id=?''', (event_id,)).fetchone()
            return tuple(row) if row else None
        except sqlite3.Error:
            self.reason = 'diagnostics_read_failed'
            return None

    def read(self, request_id):
        if self.db is None:
            return None
        try:
            row = self.db.execute('SELECT * FROM requests WHERE id=?', (request_id,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            result['commands'] = json.loads(result['commands'])
            result['parts'] = json.loads(result['parts']) if result['parts'] is not None else None
            result['spans'] = [dict(s) for s in self.db.execute(
                '''SELECT kind,position,attempt,duration_ms,observed_at,COALESCE(outcome,'unknown') AS outcome
                   FROM spans LEFT JOIN span_results USING(event_id) WHERE request_id=? ORDER BY observed_at,event_id''', (request_id,))]
            result['chat_events'] = [dict(kind=s['kind'],data=json.loads(s['data']),observed_at=s['observed_at'])
                for s in self.db.execute('SELECT kind,data,observed_at FROM chat_events WHERE request_id=? ORDER BY id',(request_id,))]
            return result
        except (sqlite3.Error, ValueError, TypeError):
            self.reason = 'diagnostics_read_failed'
            return None

    def groups(self, since):
        if self.db is None:
            return []
        try:
            return [dict(row) for row in self.db.execute('''SELECT group_id,max(created) AS last_seen,
                count(*) AS accepted_requests FROM requests WHERE group_id IS NOT NULL AND created>=?
                GROUP BY group_id ORDER BY group_id LIMIT 128''', (since,))]
        except sqlite3.Error:
            self.reason = 'diagnostics_read_failed'
            return []

    def summaries(self):
        if self.db is None:
            return {}
        try:
            result = {}
            for row in self.db.execute("SELECT id,scope,group_id,commands,parts,EXISTS(SELECT 1 FROM chat_events e WHERE e.request_id=requests.id AND e.kind='error') AS chat_failed FROM requests"):
                result[row['id']] = {'scope': row['scope'], 'group_id': row['group_id'],
                    'commands': json.loads(row['commands']),'chat_failed':bool(row['chat_failed']),
                    'parts': json.loads(row['parts']) if row['parts'] is not None else None}
            return result
        except (sqlite3.Error, ValueError, TypeError):
            self.reason = 'diagnostics_read_failed'
            return {}

    def health(self):
        return {'availability': 'partial' if self.reason or self.dropped else 'available',
                'reason_code': self.reason, 'dropped_events': self.dropped,
                'retention_seconds': 86400, 'request_capacity': 4096, 'spans_per_request': 32,
                'chat_events_per_request': 32, 'conversation_event_capacity':4096,
                'cleanup_interval_seconds': 60}

    def close(self):
        if self.db is not None:
            self.db.close()
