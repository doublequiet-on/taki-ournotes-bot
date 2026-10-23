# L3: Single-process SQLite delivery facts; no bodies, SDK objects or image blobs.
# Effects: Exclusive file ownership, bounded retained metadata, atomic intent/ACK writes.
from __future__ import annotations
import os
import sqlite3
from pathlib import Path
from .backend_protocol import ProtocolError, require


class Ledger:
    def __init__(self, path, namespace, runtime, now, capacity=4096, retention=86400):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = self.path.with_suffix('.lock').open('a+b')
        self.lock.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                if not self.lock.read(1):
                    self.lock.write(b'0'); self.lock.flush()
                self.lock.seek(0)
                msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise ProtocolError('backend_already_running', 409) from None
        self.capacity, self.retention = capacity, retention
        self.db = None
        try:
            self.db = sqlite3.connect(self.path)
            self.db.row_factory = sqlite3.Row
            self.db.executescript('''
                PRAGMA foreign_keys=ON;
                PRAGMA synchronous=FULL;
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS epochs(epoch TEXT PRIMARY KEY, active INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS highwater(context TEXT PRIMARY KEY, seq INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS requests(
                    id TEXT PRIMARY KEY, digest TEXT NOT NULL, epoch TEXT NOT NULL, runtime TEXT NOT NULL,
                    status TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
                    delivery TEXT NOT NULL UNIQUE, context TEXT NOT NULL, ingress INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS parts(
                    delivery TEXT NOT NULL REFERENCES requests(delivery) ON DELETE CASCADE,
                    id TEXT NOT NULL, position INTEGER NOT NULL, mode TEXT NOT NULL,
                    status TEXT NOT NULL, attempt TEXT, message TEXT, receipt TEXT,
                    PRIMARY KEY(delivery,id));
            ''')
            with self.db:
                schema = self.db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
                require(schema is None or schema[0] == '1', 'ledger_schema', 409)
                self.db.execute("INSERT OR IGNORE INTO meta VALUES('schema','1')")
                row = self.db.execute("SELECT value FROM meta WHERE key='namespace'").fetchone()
                require(row is None or row[0] == namespace, 'ledger_namespace', 409)
                self.db.execute("INSERT OR IGNORE INTO meta VALUES('namespace',?)", (namespace,))
                self.db.execute("UPDATE requests SET status='expired' WHERE status IN ('preparing','prepared')")
                self.db.execute("UPDATE parts SET status='uncertain' WHERE status='sending'")
                self.db.execute("UPDATE parts SET status='expired' WHERE status='pending'")
                self.db.execute('UPDATE epochs SET active=0')
                self.db.execute('DELETE FROM highwater')
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.db is not None: self.db.close()
        self.lock.close()

    def request(self, request_id):
        row = self.db.execute('SELECT * FROM requests WHERE id=?', (request_id,)).fetchone()
        return dict(row) if row else None

    def by_delivery(self, delivery):
        row = self.db.execute('SELECT * FROM requests WHERE delivery=?', (delivery,)).fetchone()
        return dict(row) if row else None

    def parts(self, delivery):
        return [dict(row) for row in self.db.execute('SELECT * FROM parts WHERE delivery=? ORDER BY position', (delivery,))]

    def admin_rows(self):
        """Bounded read of retained facts, without expiry or lifecycle transitions."""
        return [dict(row) for row in self.db.execute(
            'SELECT id,delivery,created,status,runtime FROM requests ORDER BY created DESC,id DESC LIMIT ?', (self.capacity,))]

    def admin_parts(self):
        grouped = {}
        for row in self.db.execute('SELECT delivery,id,position,mode,status FROM parts ORDER BY delivery,position'):
            grouped.setdefault(row['delivery'], []).append(dict(row))
        return grouped

    def active(self, epoch):
        row = self.db.execute('SELECT active FROM epochs WHERE epoch=?', (epoch,)).fetchone()
        return bool(row and row[0])

    def register(self, epoch):
        row = self.db.execute('SELECT active FROM epochs WHERE epoch=?', (epoch,)).fetchone()
        if row:
            require(row[0], 'retired_epoch', 409)
            return False
        require(self.db.execute('SELECT count(*) FROM epochs').fetchone()[0] < self.capacity, 'epoch_capacity', 503)
        with self.db:
            self.db.execute('UPDATE epochs SET active=0')
            self.db.execute('INSERT INTO epochs VALUES(?,1)', (epoch,))
            self.db.execute('DELETE FROM highwater')
        return True

    def retire(self, epoch):
        with self.db:
            self.db.execute('UPDATE epochs SET active=0 WHERE epoch=?', (epoch,))

    def admit(self, request, runtime, delivery, now):
        require(self.active(request.gateway_epoch), 'inactive_epoch', 409)
        row = self.db.execute('SELECT seq FROM highwater WHERE context=?', (request.context_id,)).fetchone()
        require(row is None or request.ingress_seq > row[0], 'stale_admission', 409)
        with self.db:
            # Unresolved attempts are never forgotten by routine retention cleanup.
            self.db.execute("DELETE FROM requests WHERE created<? AND status NOT IN ('preparing','prepared') "
                            "AND NOT EXISTS (SELECT 1 FROM parts WHERE parts.delivery=requests.delivery "
                            "AND parts.status IN ('sending','uncertain'))", (now-self.retention,))
            require(self.db.execute('SELECT count(*) FROM requests').fetchone()[0] < self.capacity, 'ledger_capacity', 503)
            require(row is not None or self.db.execute('SELECT count(*) FROM highwater').fetchone()[0] < self.capacity,
                    'context_capacity', 503)
            self.db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?,?)',
                            (request.request_id, request.content_digest, request.gateway_epoch, runtime, 'preparing',
                             now, request.timestamp_ms/1000+298, delivery, request.context_id, request.ingress_seq))
            self.db.execute('INSERT OR REPLACE INTO highwater VALUES(?,?)', (request.context_id, request.ingress_seq))

    def processing_hint(self, delivery):
        with self.db:
            self.db.execute("INSERT INTO parts VALUES(?, '0', 0, 'primary', 'pending', NULL, NULL, NULL)",(delivery,))

    def prepared(self, delivery, count, *, start=0):
        with self.db:
            self.db.executemany("INSERT INTO parts VALUES(?,?,?,'primary','pending',NULL,NULL,NULL)",
                                ((delivery, str(i), i) for i in range(start,count)))
            self.db.execute("UPDATE requests SET status='prepared' WHERE delivery=?", (delivery,))

    def finish(self, delivery, status):
        with self.db:
            self.db.execute('UPDATE requests SET status=? WHERE delivery=?', (status, delivery))
            self.db.execute("UPDATE parts SET status='expired' WHERE delivery=? AND status='pending'", (delivery,))
            self.db.execute("UPDATE parts SET status='uncertain' WHERE delivery=? AND status='sending'", (delivery,))

    def fallback(self, delivery, part_id):
        with self.db:
            self.db.execute("UPDATE parts SET mode='fallback',id=? WHERE delivery=? AND id=?", (part_id+':f', delivery, part_id))

    def intent(self, delivery, part_id, attempt):
        row = next((p for p in self.parts(delivery) if p['id'] == part_id), None)
        require(row is not None, 'unknown_part', 404)
        if row['attempt']:
            require(row['attempt'] == attempt, 'attempt_conflict', 409)
            return 'existing'
        require(row['status'] == 'pending', 'part_not_pending', 409)
        with self.db:
            self.db.execute("UPDATE parts SET status='sending',attempt=? WHERE delivery=? AND id=?", (attempt, delivery, part_id))
        return 'granted'

    def ack(self, delivery, part_id, attempt, status, message):
        row = next((p for p in self.parts(delivery) if p['id'] == part_id), None)
        require(row is not None and row['attempt'] == attempt, 'attempt_mismatch', 409)
        if row['receipt'] is not None:
            require(row['receipt'] == status and row['message'] == message, 'receipt_conflict', 409)
            return False
        require(row['status'] in ('sending', 'uncertain'), 'receipt_transition', 409)
        with self.db:
            self.db.execute('UPDATE parts SET status=?,receipt=?,message=? WHERE delivery=? AND id=?',
                            (status, status, message, delivery, part_id))
        return True
