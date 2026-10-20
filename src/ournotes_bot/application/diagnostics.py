# L3: Bounded, best-effort observation store. Never grants delivery permission.
from __future__ import annotations

import json
import sqlite3
from pathlib import Path


class Diagnostics:
    """One backend writer; explicitly degraded on storage failure, without ledger changes."""

    def __init__(self, path, *, enabled=True):
        self.db = None
        self.reason = None
        self.dropped = 0
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
            for row in self.db.execute('SELECT id,scope,group_id,commands,parts FROM requests'):
                result[row['id']] = {'scope': row['scope'], 'group_id': row['group_id'],
                    'commands': json.loads(row['commands']),
                    'parts': json.loads(row['parts']) if row['parts'] is not None else None}
            return result
        except (sqlite3.Error, ValueError, TypeError):
            self.reason = 'diagnostics_read_failed'
            return {}

    def health(self):
        return {'availability': 'partial' if self.reason or self.dropped else 'available',
                'reason_code': self.reason, 'dropped_events': self.dropped,
                'retention_seconds': 86400, 'request_capacity': 4096, 'spans_per_request': 32}

    def close(self):
        if self.db is not None:
            self.db.close()
