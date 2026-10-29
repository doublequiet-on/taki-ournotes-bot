# L3: Trigger-only visible chat audit with independent schema and 30-day expiry.
# Effects: One private SQLite file; never stores provider bodies or reasoning.
import json
import sqlite3
import time
from pathlib import Path

from ..domain.chat import PERSONA_VERSION


class ChatStore:
    def __init__(self,path,identity,*,clock=time.time):
        self.clock=clock
        self.path=Path(path)
        self.db=sqlite3.connect(self.path)
        self.db.execute('PRAGMA secure_delete=ON')
        self.db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        meta=dict(self.db.execute('SELECT key,value FROM meta'))
        if meta and meta!={'schema':'chat/1','identity':identity}:
            self.db.close()
            raise ValueError('chat_store_identity')
        with self.db:
            self.db.executemany('INSERT OR IGNORE INTO meta VALUES (?,?)',[('schema','chat/1'),('identity',identity)])
            self.db.execute('''CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, source_time REAL NOT NULL,
                trigger TEXT NOT NULL, persona TEXT NOT NULL, profile TEXT NOT NULL,
                context TEXT NOT NULL, visible TEXT NOT NULL DEFAULT '', status TEXT NOT NULL)''')
            self.db.execute('''CREATE TABLE IF NOT EXISTS events (
                run_id TEXT NOT NULL, event_id TEXT NOT NULL, kind TEXT NOT NULL,
                data TEXT NOT NULL, PRIMARY KEY(run_id,event_id))''')
            self.db.execute('CREATE INDEX IF NOT EXISTS runs_expiry ON runs(source_time)')
        self.sweep()

    def sweep(self):
        with self.db:
            self.db.execute('DELETE FROM events WHERE run_id IN (SELECT id FROM runs WHERE source_time<=?)',
                            (self.clock()-30*86400,))
            self.db.execute('DELETE FROM runs WHERE source_time<=?',(self.clock()-30*86400,))

    def begin(self,work,messages):
        self.sweep()
        # Repeating an old excerpt must not extend its source's retention.
        source_times=[work.request.timestamp_ms/1000]
        for message in messages:
            source=json.loads(message['content'])
            if 'timestamp_ms' in source: source_times.append(source['timestamp_ms']/1000)
        source_time=min(source_times)
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO runs VALUES (?,?,?,?,?,?,?,?,?)',(
                work.delivery,json.dumps(work.request.scope,sort_keys=True),source_time,
                work.request.trigger,PERSONA_VERSION,work.model_snapshot.profile.config_version,
                json.dumps(messages,ensure_ascii=False),'','running'))

    def event(self,run_id,event_id,kind,data):
        # Callers pass a closed metadata projection, never arbitrary provider JSON.
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO events SELECT ?,?,?,? WHERE EXISTS (SELECT 1 FROM runs WHERE id=?)',
                            (run_id,event_id,kind,json.dumps(data,ensure_ascii=False),run_id))

    def generated(self,run_id,text):
        with self.db:
            self.db.execute("UPDATE runs SET visible=?,status='generated' WHERE id=?",(text,run_id))

    def finish(self,run_id,status):
        with self.db:
            self.db.execute('UPDATE runs SET status=? WHERE id=?',(status,run_id))

    def close(self):
        self.db.close()
