# L3: Trigger-only audit and scoped, receipt-bound memory with source deletion.
# Effects: Private SQLite + independent deletion journal; no model/network/platform IO.
import json
import os
import sqlite3
import time
import unicodedata
from pathlib import Path

from ..domain.chat import PERSONA_VERSION
from ..domain.chat_memory import scope_key, message_id, search_terms

RETENTION_SECONDS=30*86400


def encoded(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))


def normalized(value):
    return unicodedata.normalize('NFKC',value).casefold()


class ChatStore:
    def __init__(self,path,identity,*,clock=time.time,deletions_path=None,restore_state=None):
        self.clock,self.identity=clock,identity
        self.path=Path(path)
        self.deletions_path=Path(deletions_path) if deletions_path else self.path.with_suffix('.deletions.json')
        self.db=sqlite3.connect(self.path)
        self.db.row_factory=sqlite3.Row
        self.db.create_function('chat_normalized',1,normalized,deterministic=True)
        try:
            self.db.execute('PRAGMA secure_delete=ON')
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            meta=dict(self.db.execute('SELECT key,value FROM meta'))
            if meta and meta!={'schema':'chat/1','identity':identity}: raise ValueError('chat_store_identity')
            if self.db.execute('PRAGMA user_version').fetchone()[0] not in (0,2): raise ValueError('chat_memory_version')
            with self.db:
                # Preserve the exact C1 meta contract for code-only rollback.
                self.db.executemany('INSERT OR IGNORE INTO meta VALUES (?,?)',[('schema','chat/1'),('identity',identity)])
                self.db.execute('''CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, scope TEXT NOT NULL, source_time REAL NOT NULL,
                    trigger TEXT NOT NULL, persona TEXT NOT NULL, profile TEXT NOT NULL,
                    context TEXT NOT NULL, visible TEXT NOT NULL DEFAULT '', status TEXT NOT NULL)''')
                self.db.execute('''CREATE TABLE IF NOT EXISTS events (
                    run_id TEXT NOT NULL, event_id TEXT NOT NULL, kind TEXT NOT NULL,
                    data TEXT NOT NULL, PRIMARY KEY(run_id,event_id))''')
                self.db.execute('CREATE INDEX IF NOT EXISTS runs_expiry ON runs(source_time)')
                self.db.executescript('''
                    CREATE TABLE IF NOT EXISTS memory_messages (
                        scope TEXT NOT NULL, message_id TEXT NOT NULL, role TEXT NOT NULL,
                        speaker_id TEXT NOT NULL, display_name TEXT NOT NULL, timestamp_ms INTEGER NOT NULL,
                        text TEXT NOT NULL, reply_to TEXT, PRIMARY KEY(scope,message_id));
                    CREATE INDEX IF NOT EXISTS memory_recent ON memory_messages(scope,timestamp_ms);
                    CREATE TABLE IF NOT EXISTS memory_media_sources (
                        scope TEXT NOT NULL,message_id TEXT NOT NULL,speaker_id TEXT NOT NULL,timestamp_ms INTEGER NOT NULL,
                        PRIMARY KEY(scope,message_id));
                    CREATE INDEX IF NOT EXISTS memory_media_expiry ON memory_media_sources(timestamp_ms,scope);
                    CREATE TABLE IF NOT EXISTS memory_run_sources (
                        run_id TEXT NOT NULL, scope TEXT NOT NULL, message_id TEXT NOT NULL,
                        PRIMARY KEY(run_id,scope,message_id));
                    CREATE INDEX IF NOT EXISTS memory_run_source ON memory_run_sources(scope,message_id);
                    CREATE TABLE IF NOT EXISTS memory_dependencies (
                        scope TEXT NOT NULL, message_id TEXT NOT NULL, source_message_id TEXT NOT NULL,
                        PRIMARY KEY(scope,message_id,source_message_id));
                    CREATE INDEX IF NOT EXISTS memory_dependency_source ON memory_dependencies(scope,source_message_id);
                    CREATE TABLE IF NOT EXISTS memory_summaries (
                        scope TEXT NOT NULL, summary_id TEXT NOT NULL, text TEXT NOT NULL,
                        created_ms INTEGER NOT NULL, PRIMARY KEY(scope,summary_id));
                    CREATE TABLE IF NOT EXISTS memory_summary_sources (
                        scope TEXT NOT NULL, summary_id TEXT NOT NULL, source_message_id TEXT NOT NULL,
                        PRIMARY KEY(scope,summary_id,source_message_id));
                    CREATE INDEX IF NOT EXISTS memory_summary_source ON memory_summary_sources(scope,source_message_id);
                    CREATE TABLE IF NOT EXISTS memory_tombstones (
                        scope TEXT NOT NULL, message_id TEXT NOT NULL, deleted_ms INTEGER NOT NULL,
                        PRIMARY KEY(scope,message_id));
                    CREATE TABLE IF NOT EXISTS memory_scope_deletions (
                        scope TEXT PRIMARY KEY, deleted_before_ms INTEGER NOT NULL);
                    CREATE TABLE IF NOT EXISTS memory_state (key TEXT PRIMARY KEY,value INTEGER NOT NULL);
                    CREATE TABLE IF NOT EXISTS chat_service_runs (
                        delivery TEXT PRIMARY KEY,scope TEXT NOT NULL,request_id TEXT NOT NULL,created REAL NOT NULL);
                    CREATE TABLE IF NOT EXISTS chat_service_receipts (
                        request_id TEXT PRIMARY KEY,scope TEXT NOT NULL,delivered_at REAL NOT NULL);
                    INSERT OR IGNORE INTO memory_state VALUES ('revision',0);
                    INSERT OR IGNORE INTO memory_state VALUES ('c1_imported',0);
                    CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                        scope UNINDEXED, item_id UNINDEXED, kind UNINDEXED, text, tokenize='trigram');
                ''')
                self.db.execute('PRAGMA user_version=2')
            if (self.db.execute("SELECT value FROM memory_state WHERE key='c1_imported'").fetchone()[0]==0 or self.db.execute(
                    'SELECT 1 FROM runs r WHERE NOT EXISTS (SELECT 1 FROM memory_run_sources s WHERE s.run_id=r.id) LIMIT 1').fetchone()):
                self._import_c1()
            # This journal is intentionally outside a restored SQLite snapshot.
            if self.deletions_path.exists(): self.restore_deletions(self._read_journal())
            if restore_state is not None: self.restore_deletions(restore_state)
            self.sweep()
        except BaseException:
            self.db.close()
            raise

    def _scope(self,scope):
        key=scope_key(scope)
        if scope['instance_id']+'\0'+scope['app_id']!=self.identity: raise ValueError('chat_memory_identity')
        return key

    def _changed(self):
        self.db.execute("UPDATE memory_state SET value=value+1 WHERE key='revision'")

    @property
    def revision(self):
        return self.db.execute("SELECT value FROM memory_state WHERE key='revision'").fetchone()[0]

    def _blocked(self,scope,mid,stamp):
        return (stamp<=int((self.clock()-RETENTION_SECONDS)*1000)
            or self.db.execute('SELECT 1 FROM memory_tombstones WHERE scope=? AND message_id=?',(scope,mid)).fetchone() is not None
            or self.db.execute('SELECT 1 FROM memory_scope_deletions WHERE scope=? AND deleted_before_ms>=?',(scope,stamp)).fetchone() is not None)

    def _put_message(self,scope,message):
        mid=message_id(message['message_id'])
        role=message['role'];speaker=message_id(message['speaker_id']);stamp=message['timestamp_ms']
        text=message['text'];name=message.get('display_name','');reply_to=message.get('reply_to')
        if (role not in ('user','assistant') or type(stamp) is not int or not 0<=stamp<10**14
                or stamp>int(self.clock()*1000)+1000 or not isinstance(text,str) or len(text.encode())>65536
                or not isinstance(name,str) or len(name)>256): raise ValueError('chat_memory_message')
        if reply_to is not None: message_id(reply_to)
        if self._blocked(scope,mid,stamp): return False
        media=self.db.execute('SELECT speaker_id,timestamp_ms FROM memory_media_sources WHERE scope=? AND message_id=?',(scope,mid)).fetchone()
        if media and (media['speaker_id'],media['timestamp_ms'])!=(speaker,stamp):raise ValueError('chat_memory_media_conflict')
        prior=self.db.execute('SELECT * FROM memory_messages WHERE scope=? AND message_id=?',(scope,mid)).fetchone()
        if prior:
            if any(prior[key]!=value for key,value in (('role',role),('speaker_id',speaker),('text',text),('reply_to',reply_to))):
                raise ValueError('chat_memory_message_conflict')
            return True
        self.db.execute('INSERT INTO memory_messages VALUES (?,?,?,?,?,?,?,?)',(scope,mid,role,speaker,name,stamp,text,reply_to))
        self.db.execute('INSERT INTO memory_fts VALUES (?,?,?,?)',(scope,mid,'message',normalized(text)))
        self._changed()
        return True

    def _context(self,run,scope,messages):
        for message in messages:
            source=json.loads(message['content'])
            if 'image_reference' in source:
                reference=source['image_reference']
                if (message.get('role')!='user' or not isinstance(reference,dict)
                        or set(reference)!={'message_id','speaker_id','timestamp_ms'}
                        or type(reference['timestamp_ms']) is not int or not 0<=reference['timestamp_ms']<=int(self.clock()*1000)+1000):
                    raise ValueError('chat_memory_media')
                mid=message_id(reference['message_id']);speaker=message_id(reference['speaker_id']);stamp=reference['timestamp_ms']
                if self._blocked(scope,mid,stamp):continue
                original=self.db.execute('SELECT role,speaker_id,timestamp_ms FROM memory_messages WHERE scope=? AND message_id=?',(scope,mid)).fetchone()
                if original and (original['role'],original['speaker_id'],original['timestamp_ms'])!=('user',speaker,stamp):raise ValueError('chat_memory_media_conflict')
                old=self.db.execute('SELECT speaker_id,timestamp_ms FROM memory_media_sources WHERE scope=? AND message_id=?',(scope,mid)).fetchone()
                if old and (old['speaker_id'],old['timestamp_ms'])!=(speaker,stamp):raise ValueError('chat_memory_media_conflict')
                if not old:
                    self.db.execute('INSERT INTO memory_media_sources VALUES (?,?,?,?)',(scope,mid,speaker,stamp));self._changed()
                self.db.execute('INSERT OR IGNORE INTO memory_run_sources VALUES (?,?,?)',(run,scope,mid))
                continue
            # A frozen business display is evidence material, not a chat event.
            if 'message_id' not in source: continue
            if message.get('role') not in ('user','assistant') or source.get('role')!=message['role']:
                raise ValueError('chat_memory_role')
            mid=message_id(source['message_id'])
            if message['role']=='user': accepted=self._put_message(scope,source)
            else:
                # An assistant label in a context cannot grant delivery. Only an
                # existing success receipt may have created this message.
                accepted=self.db.execute('SELECT 1 FROM memory_messages WHERE scope=? AND message_id=? AND role=\'assistant\'',(scope,mid)).fetchone()
            if accepted:
                self.db.execute('INSERT OR IGNORE INTO memory_run_sources VALUES (?,?,?)',(run,scope,mid))

    def _delivery(self,run,data,*,timestamp_ms=None):
        if data.get('status')!='success' or data.get('allow_context') is not True: return
        row=self.db.execute('SELECT scope,source_time,context FROM runs WHERE id=?',(run,)).fetchone()
        if row is None: return
        scope=self._scope(json.loads(row['scope']))
        sources=[r[0] for r in self.db.execute('SELECT message_id FROM memory_run_sources WHERE run_id=? AND scope=?',(run,scope))]
        reply_to=None
        for item in json.loads(row['context']):
            source=json.loads(item['content'])
            if item.get('role')=='user' and 'message_id' in source: reply_to=source['message_id']
        message=dict(message_id=data['message_id'],speaker_id='Taki',role='assistant',display_name='Taki',
                     text=data.get('text',''),timestamp_ms=timestamp_ms if timestamp_ms is not None else int(self.clock()*1000),reply_to=reply_to)
        if self._put_message(scope,message):
            self.db.execute('INSERT OR IGNORE INTO memory_run_sources VALUES (?,?,?)',(run,scope,message['message_id']))
            self.db.executemany('INSERT OR IGNORE INTO memory_dependencies VALUES (?,?,?)',
                               [(scope,message['message_id'],mid) for mid in sources if mid!=message['message_id']])

    def _import_c1(self):
        with self.db:
            rows=self.db.execute('''SELECT id,scope,context,source_time FROM runs r
                WHERE NOT EXISTS (SELECT 1 FROM memory_run_sources s WHERE s.run_id=r.id)
                ORDER BY source_time,id''').fetchall()
            for row in rows:
                scope=self._scope(json.loads(row['scope']))
                self._context(row['id'],scope,json.loads(row['context']))
            for row in rows:
                for event in self.db.execute("SELECT data FROM events WHERE run_id=? AND kind='delivery' ORDER BY event_id",(row['id'],)).fetchall():
                    # C1 did not timestamp ACKs; the older run time is conservative.
                    self._delivery(row['id'],json.loads(event['data']),timestamp_ms=int(row['source_time']*1000))
            # C1 run source_time is the oldest quoted message, not creation order.
            # A second pass binds previous delivered replies even if UUID order
            # placed a follow-up before its original run.
            for row in rows:
                self._context(row['id'],self._scope(json.loads(row['scope'])),json.loads(row['context']))
                for event in self.db.execute("SELECT data FROM events WHERE run_id=? AND kind='delivery'",(row['id'],)).fetchall():
                    self._delivery(row['id'],json.loads(event['data']),timestamp_ms=int(row['source_time']*1000))
            self.db.execute("UPDATE memory_state SET value=1 WHERE key='c1_imported'")

    def begin(self,work,messages,*,participating=True):
        if not participating: return
        self.sweep()
        scope=self._scope(work.request.scope)
        kept=[]
        source_times=[work.request.timestamp_ms/1000]
        for message in messages:
            source=json.loads(message['content'])
            if 'image_reference' in source:
                ref=source['image_reference']
                if self._blocked(scope,message_id(ref['message_id']),ref['timestamp_ms']):continue
            if 'message_id' in source:
                if self._blocked(scope,message_id(source['message_id']),source['timestamp_ms']): continue
                if message.get('role')=='assistant' and self.db.execute(
                        'SELECT 1 FROM memory_messages WHERE scope=? AND message_id=? AND role=\'assistant\'',
                        (scope,source['message_id'])).fetchone() is None: continue
            if 'timestamp_ms' in source: source_times.append(source['timestamp_ms']/1000)
            kept.append(message)
        if not kept: return
        with self.db:
            inserted=self.db.execute('INSERT OR IGNORE INTO runs VALUES (?,?,?,?,?,?,?,?,?)',(
                work.delivery,scope,min(source_times),work.request.trigger,getattr(work,'persona_version',PERSONA_VERSION),
                work.model_snapshot.profile.config_version,encoded(kept),'','running')).rowcount
            if inserted: self._context(work.delivery,scope,kept)

    def event(self,run_id,event_id,kind,data):
        with self.db:
            inserted=self.db.execute('INSERT OR IGNORE INTO events SELECT ?,?,?,? WHERE EXISTS (SELECT 1 FROM runs WHERE id=?)',
                                    (run_id,event_id,kind,encoded(data),run_id)).rowcount
            if inserted and kind=='delivery': self._delivery(run_id,data)

    def bind_sources(self,run_id,scope,message_ids):
        key=self._scope(scope)
        with self.db:
            if self.db.execute('SELECT 1 FROM runs WHERE id=? AND scope=?',(run_id,key)).fetchone() is None:return
            for mid in message_ids:
                if self.db.execute('SELECT 1 FROM memory_messages WHERE scope=? AND message_id=?',(key,mid)).fetchone() is None:
                    raise ValueError('chat_memory_source_missing')
                self.db.execute('INSERT OR IGNORE INTO memory_run_sources VALUES (?,?,?)',(run_id,key,mid))

    def generated(self,run_id,text):
        with self.db: self.db.execute("UPDATE runs SET visible=?,status='generated' WHERE id=?",(text,run_id))

    def finish(self,run_id,status):
        with self.db: self.db.execute('UPDATE runs SET status=? WHERE id=?',(status,run_id))

    def _purge(self,scope,message_ids):
        removed=set(message_ids)
        while removed:
            derived={r[0] for mid in removed for r in self.db.execute(
                'SELECT message_id FROM memory_dependencies WHERE scope=? AND source_message_id=?',(scope,mid))}
            fresh=derived-removed
            if not fresh: break
            removed.update(fresh)
        run_ids={r[0] for mid in removed for r in self.db.execute('SELECT run_id FROM memory_run_sources WHERE scope=? AND message_id=?',(scope,mid))}
        summary_ids={r[0] for mid in removed for r in self.db.execute('SELECT summary_id FROM memory_summary_sources WHERE scope=? AND source_message_id=?',(scope,mid))}
        for sid in summary_ids:
            self.db.execute('DELETE FROM memory_fts WHERE scope=? AND item_id=? AND kind=\'summary\'',(scope,sid))
            self.db.execute('DELETE FROM memory_summaries WHERE scope=? AND summary_id=?',(scope,sid))
            self.db.execute('DELETE FROM memory_summary_sources WHERE scope=? AND summary_id=?',(scope,sid))
        for mid in removed:
            self.db.execute('DELETE FROM memory_fts WHERE scope=? AND item_id=? AND kind=\'message\'',(scope,mid))
            self.db.execute('DELETE FROM memory_messages WHERE scope=? AND message_id=?',(scope,mid))
            self.db.execute('DELETE FROM memory_media_sources WHERE scope=? AND message_id=?',(scope,mid))
            self.db.execute('DELETE FROM memory_dependencies WHERE scope=? AND (message_id=? OR source_message_id=?)',(scope,mid,mid))
            self.db.execute('DELETE FROM memory_run_sources WHERE scope=? AND message_id=?',(scope,mid))
        # A C1 audit blob may repeat deleted text in context or visible output;
        # discard the dependent blob rather than leave a second readable copy.
        for run in run_ids:
            self.db.execute('DELETE FROM events WHERE run_id=?',(run,))
            self.db.execute('DELETE FROM runs WHERE id=?',(run,))
            self.db.execute('DELETE FROM memory_run_sources WHERE run_id=?',(run,))
        if removed or summary_ids or run_ids: self._changed()
        return len(removed)

    def sweep(self):
        cutoff=int((self.clock()-RETENTION_SECONDS)*1000)
        with self.db:
            scopes=[r[0] for r in self.db.execute('SELECT scope FROM memory_messages WHERE timestamp_ms<=? UNION SELECT scope FROM memory_media_sources WHERE timestamp_ms<=?',(cutoff,cutoff))]
            for scope in scopes:
                self._purge(scope,[r[0] for r in self.db.execute('SELECT message_id FROM memory_messages WHERE scope=? AND timestamp_ms<=? UNION SELECT message_id FROM memory_media_sources WHERE scope=? AND timestamp_ms<=?',(scope,cutoff,scope,cutoff))])
            self.db.execute('DELETE FROM events WHERE run_id IN (SELECT id FROM runs WHERE source_time<=?)',(cutoff/1000,))
            self.db.execute('DELETE FROM memory_run_sources WHERE run_id IN (SELECT id FROM runs WHERE source_time<=?)',(cutoff/1000,))
            self.db.execute('DELETE FROM runs WHERE source_time<=?',(cutoff/1000,))
            self.db.execute('DELETE FROM chat_service_runs WHERE created<=?',(cutoff/1000,))
            self.db.execute('DELETE FROM chat_service_receipts WHERE delivered_at<=?',(cutoff/1000,))

    @staticmethod
    def _message(row):
        return {key:row[key] for key in ('message_id','role','speaker_id','display_name','timestamp_ms','text','reply_to')}

    def recent(self,scope,*,limit=48,max_bytes=24576,since_ms=None):
        self.sweep();key=self._scope(scope)
        if type(limit) is not int or not 1<=limit<=120 or type(max_bytes) is not int or not 1<=max_bytes<=262144:
            raise ValueError('chat_memory_limit')
        floor=int((self.clock()-RETENTION_SECONDS)*1000) if since_ms is None else since_ms
        if type(floor) is not int: raise ValueError('chat_memory_time')
        rows=self.db.execute('SELECT * FROM memory_messages WHERE scope=? AND timestamp_ms>? ORDER BY timestamp_ms DESC,rowid DESC LIMIT ?',(key,floor,limit)).fetchall()
        result=[];size=0
        for row in rows:
            message=self._message(row);count=len(encoded(message).encode())
            if size+count>max_bytes: break
            result.append(message);size+=count
        return list(reversed(result))

    def read(self,scope,message_ids):
        self.sweep();key=self._scope(scope)
        if not isinstance(message_ids,(list,tuple)) or not 1<=len(message_ids)<=48: raise ValueError('chat_memory_limit')
        rows=[]
        for mid in dict.fromkeys(message_id(value) for value in message_ids):
            row=self.db.execute('SELECT * FROM memory_messages WHERE scope=? AND message_id=?',(key,mid)).fetchone()
            if row: rows.append(self._message(row))
        return {'status':'ok' if rows else 'no_results','messages':rows,'source_kind':'group_dialogue','revision':self.revision}

    def search(self,scope,query,*,limit=8,before_ms=None):
        self.sweep();key=self._scope(scope);terms=search_terms(query)
        if type(limit) is not int or not 1<=limit<=20: raise ValueError('chat_memory_limit')
        if before_ms is not None and (type(before_ms) is not int or not 0<=before_ms<10**14): raise ValueError('chat_memory_time')
        if not terms: return {'status':'no_results','messages':[],'source_kind':'group_dialogue','revision':self.revision}
        # FTS trigram handles arbitrary Japanese/Chinese substrings. One/two
        # character terms use scoped instr, since SQLite trigrams need 3 chars.
        indexed=[term for term in terms if len(term)>=3]
        conditions=['m.scope=?'];parameters=[key]
        for term in terms:
            conditions.append('instr(chat_normalized(m.text),?)>0');parameters.append(term)
        if before_ms is not None: conditions.append('m.timestamp_ms<=?');parameters.append(before_ms)
        parameters.append(limit)
        if indexed:
            match=' AND '.join('"'+term.replace('"','""')+'"' for term in indexed)
            sql='''SELECT m.* FROM memory_messages m JOIN memory_fts f
                ON f.scope=m.scope AND f.item_id=m.message_id AND f.kind='message'
                WHERE memory_fts MATCH ? AND '''+' AND '.join(conditions)+' ORDER BY m.timestamp_ms DESC,m.rowid DESC LIMIT ?'
            candidates=self.db.execute(sql,[match,*parameters]).fetchall()
        else:
            candidates=self.db.execute('SELECT m.* FROM memory_messages m WHERE '+' AND '.join(conditions)
                +' ORDER BY m.timestamp_ms DESC,m.rowid DESC LIMIT ?',parameters).fetchall()
        rows=[self._message(row) for row in candidates]
        return {'status':'ok' if rows else 'no_results','messages':rows,'source_kind':'group_dialogue','revision':self.revision}

    def put_summary(self,scope,summary_id,text,source_message_ids):
        self.sweep();key=self._scope(scope);sid=message_id(summary_id)
        if not isinstance(text,str) or not 0<len(text.encode())<=12000: raise ValueError('chat_memory_summary')
        sources=self.read(scope,source_message_ids)['messages']
        ids=list(dict.fromkeys(source_message_ids))
        if len(sources)!=len(ids): raise ValueError('chat_memory_source_missing')
        with self.db:
            self.db.execute('DELETE FROM memory_fts WHERE scope=? AND item_id=? AND kind=\'summary\'',(key,sid))
            self.db.execute('DELETE FROM memory_summary_sources WHERE scope=? AND summary_id=?',(key,sid))
            self.db.execute('INSERT OR REPLACE INTO memory_summaries VALUES (?,?,?,?)',(key,sid,text,int(self.clock()*1000)))
            self.db.executemany('INSERT INTO memory_summary_sources VALUES (?,?,?)',[(key,sid,mid) for mid in ids])
            self.db.execute('INSERT INTO memory_fts VALUES (?,?,?,?)',(key,sid,'summary',normalized(text)))
            self._changed()
        return {'summary_id':sid,'source_message_ids':ids,'expires_at_ms':min(r['timestamp_ms'] for r in sources)+RETENTION_SECONDS*1000}

    def summaries(self,scope,query='',*,limit=8):
        self.sweep();key=self._scope(scope)
        if type(limit) is not int or not 1<=limit<=20: raise ValueError('chat_memory_limit')
        terms=search_terms(query) if query else ()
        rows=[]
        for row in self.db.execute('SELECT * FROM memory_summaries WHERE scope=? ORDER BY created_ms DESC',(key,)):
            if not all(term in normalized(row['text']) for term in terms): continue
            ids=[r[0] for r in self.db.execute('SELECT source_message_id FROM memory_summary_sources WHERE scope=? AND summary_id=?',(key,row['summary_id']))]
            sources=self.read(scope,ids)['messages'] if ids else []
            if len(sources)!=len(ids) or not sources: continue
            rows.append(dict(summary_id=row['summary_id'],text=row['text'],source_message_ids=ids,
                             expires_at_ms=min(r['timestamp_ms'] for r in sources)+RETENTION_SECONDS*1000,source_kind='derived_summary'))
            if len(rows)>=limit: break
        return rows

    def deletion_state(self):
        return {'schema':'taki-chat-deletions/1','identity':self.identity,
                'messages':[dict(scope=json.loads(r['scope']),message_id=r['message_id'],deleted_ms=r['deleted_ms'])
                            for r in self.db.execute('SELECT * FROM memory_tombstones ORDER BY scope,message_id')],
                'scopes':[dict(scope=json.loads(r['scope']),deleted_before_ms=r['deleted_before_ms'])
                          for r in self.db.execute('SELECT * FROM memory_scope_deletions ORDER BY scope')]}

    def _read_journal(self):
        if self.deletions_path.is_symlink() or self.deletions_path.stat().st_size>16*1024*1024:
            raise ValueError('chat_deletion_journal')
        return json.loads(self.deletions_path.read_text(encoding='utf-8'))

    def _journal(self,state):
        payload=encoded(state)
        if len(payload.encode())>16*1024*1024: raise ValueError('chat_deletion_journal_capacity')
        temporary=self.deletions_path.with_name(self.deletions_path.name+'.tmp')
        with temporary.open('w',encoding='utf-8') as stream:
            stream.write(payload);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,self.deletions_path)

    def restore_deletions(self,state):
        if (not isinstance(state,dict) or set(state)!={'schema','identity','messages','scopes'}
                or state['schema']!='taki-chat-deletions/1' or state['identity']!=self.identity
                or not isinstance(state['messages'],list) or not isinstance(state['scopes'],list)):
            raise ValueError('chat_deletion_journal')
        messages={}
        scopes={}
        # Merge current DB and independent journal before writing either. A
        # partial crash leaves a durable delete to replay, never resurrected text.
        for source in (self.deletion_state(),state):
            for row in source['messages']:
                if set(row)!={'scope','message_id','deleted_ms'} or type(row['deleted_ms']) is not int or not 0<=row['deleted_ms']<10**14:
                    raise ValueError('chat_deletion_journal')
                key=self._scope(row['scope']);mid=message_id(row['message_id'])
                messages[(key,mid)]=max(messages.get((key,mid),0),row['deleted_ms'])
            for row in source['scopes']:
                if set(row)!={'scope','deleted_before_ms'} or type(row['deleted_before_ms']) is not int or not 0<=row['deleted_before_ms']<10**14:
                    raise ValueError('chat_deletion_journal')
                key=self._scope(row['scope']);scopes[key]=max(scopes.get(key,0),row['deleted_before_ms'])
        merged={'schema':state['schema'],'identity':self.identity,
                'messages':[dict(scope=json.loads(key),message_id=mid,deleted_ms=stamp) for (key,mid),stamp in sorted(messages.items())],
                'scopes':[dict(scope=json.loads(key),deleted_before_ms=stamp) for key,stamp in sorted(scopes.items())]}
        self._journal(merged)
        with self.db:
            self.db.executemany('INSERT OR REPLACE INTO memory_tombstones VALUES (?,?,?)',[(key,mid,stamp) for (key,mid),stamp in messages.items()])
            self.db.executemany('INSERT OR REPLACE INTO memory_scope_deletions VALUES (?,?)',list(scopes.items()))
            for key in set(scopes)|{key for key,_ in messages}:
                ids={mid for scope,mid in messages if scope==key}
                if key in scopes: ids.update(r[0] for r in self.db.execute('SELECT message_id FROM memory_messages WHERE scope=? AND timestamp_ms<=?',(key,scopes[key])))
                if key in scopes: ids.update(r[0] for r in self.db.execute('SELECT message_id FROM memory_media_sources WHERE scope=? AND timestamp_ms<=?',(key,scopes[key])))
                self._purge(key,ids)
            self._changed()
        self.sweep()

    def delete_messages(self,scope,message_ids):
        key=self._scope(scope)
        if not isinstance(message_ids,(list,tuple)) or not 1<=len(message_ids)<=120: raise ValueError('chat_memory_limit')
        ids=list(dict.fromkeys(message_id(mid) for mid in message_ids));state=self.deletion_state();stamp=int(self.clock()*1000)
        state['messages'].extend(dict(scope=json.loads(key),message_id=mid,deleted_ms=stamp) for mid in ids)
        self.restore_deletions(state)
        return {'status':'deleted','message_ids':ids,'revision':self.revision}

    def clear_scope(self,scope):
        key=self._scope(scope);state=self.deletion_state();stamp=int(self.clock()*1000)
        state['scopes'].append(dict(scope=json.loads(key),deleted_before_ms=stamp))
        self.restore_deletions(state)
        return {'status':'cleared','deleted_before_ms':stamp,'revision':self.revision}

    def status(self,scope):
        self.sweep();key=self._scope(scope)
        return {'schema':'chat/1','memory_version':2,'retention_days':30,'revision':self.revision,
                'messages':self.db.execute('SELECT count(*) FROM memory_messages WHERE scope=?',(key,)).fetchone()[0],
                'media_sources':self.db.execute('SELECT count(*) FROM memory_media_sources WHERE scope=?',(key,)).fetchone()[0],
                'summaries':self.db.execute('SELECT count(*) FROM memory_summaries WHERE scope=?',(key,)).fetchone()[0],
                'message_tombstones':self.db.execute('SELECT count(*) FROM memory_tombstones WHERE scope=?',(key,)).fetchone()[0],
                'clear_before_ms':next(iter(self.db.execute('SELECT deleted_before_ms FROM memory_scope_deletions WHERE scope=?',(key,))),[None])[0]}

    def source_valid(self,scope,mid,timestamp_ms):
        return not self._blocked(self._scope(scope),message_id(mid),timestamp_ms)

    def proactive_replies(self,scope):
        key=self._scope(scope)
        return tuple(row[0] for row in self.db.execute('SELECT delivered_at FROM chat_service_receipts WHERE scope=? AND delivered_at>?',
            (key,self.clock()-86400)))

    def service_run(self,delivery,scope,request_id):
        with self.db:self.db.execute('INSERT INTO chat_service_runs VALUES (?,?,?,?)',(delivery,self._scope(scope),request_id,self.clock()))

    def service_ack(self,delivery):
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO chat_service_receipts SELECT request_id,scope,? FROM chat_service_runs WHERE delivery=?',(self.clock(),delivery))

    def recover_service_receipts(self,ledger):
        # Delivery facts remain authoritative if an auxiliary receipt write failed.
        # The passive reply window gives a conservative upper time bound where
        # old delivery rows do not retain an exact ACK timestamp.
        rows=self.db.execute('''SELECT r.* FROM chat_service_runs r WHERE r.created>?
            AND NOT EXISTS (SELECT 1 FROM chat_service_receipts a WHERE a.request_id=r.request_id)''',(self.clock()-86700,)).fetchall()
        with self.db:
            for row in rows:
                if any(part['status']=='success' for part in ledger.parts(row['delivery'])):
                    self.db.execute('INSERT OR IGNORE INTO chat_service_receipts VALUES (?,?,?)',
                        (row['request_id'],row['scope'],min(self.clock(),row['created']+298)))

    def close(self):
        self.db.close()
