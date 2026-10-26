# L3
# Input: Explicit offline history copies and distinct output paths.
# Output: Verified compressed copy, legacy export or corruption diagnosis.
# Pos: Sources / history maintenance; see docs/CUTOFF_HISTORY.md.
# Effects: Local SQLite and files only; never swaps application data paths.
"""Copy-only migration/export for the existing history contracts (schemas 1/2/3)."""
import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from . import history_compression as c

BUSINESS = {'boards', 'observations', 'gaps', 'health', 'event_metadata',
            'observation_players', 'clock_calibrations', 'clock_quarantine', 'clock_pending'}
FIELDS = {'observations': 'scores', 'observation_players': 'players'}
PLAYER_SCHEMA = 'CREATE TABLE observation_players (observation_id INTEGER PRIMARY KEY, players TEXT NOT NULL, quality TEXT NOT NULL)'


def quoted(name):
    if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', name):
        raise c.Damage('invalid schema identifier')
    return '"' + name + '"'


def ro(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)


def file_sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def schema(db):
    return db.execute("SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' AND type IN ('table','index') ORDER BY type DESC,name").fetchall()


def digest(db, table):
    cols = [r[1] for r in db.execute('PRAGMA table_info(' + quoted(table) + ')')]
    h = hashlib.sha256()
    count = 0
    for row in db.execute('SELECT * FROM ' + quoted(table) + ' ORDER BY ' + ','.join(map(quoted, cols))):
        h.update(c.canonical(row).encode('utf8'))
        h.update(b'\n')
        count += 1
    return [count, h.hexdigest()]


def checked(db):
    if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise c.Damage('SQLite integrity check failed')
    if db.execute('PRAGMA foreign_key_check').fetchall():
        raise c.Damage('missing foreign key references')


def paths(source, target):
    source, target = Path(source).resolve(), Path(target).absolute()
    if target.is_symlink() or source == target.resolve():
        raise ValueError('output must be a separate regular file')
    if target.exists():
        raise FileExistsError(target)
    if not target.parent.is_dir():
        raise ValueError('output directory must already exist')
    partial = target.with_name(target.name + '.partial')
    if partial.is_symlink():
        raise ValueError('partial output cannot be a symbolic link')
    return source, target, partial


def publish(partial, target):
    # Windows rename refuses replacement; POSIX rename would overwrite a rival.
    if os.name == 'nt':
        os.rename(partial, target)
    else:
        os.link(partial, target)
        partial.unlink()


def create(partial, src, source_hash, legacy):
    items = schema(src)
    names = {n for t, n, _ in items if t == 'table'}
    if not {'boards', 'observations', 'gaps', 'health'} <= names or not names <= BUSINESS:
        raise c.Damage('unsupported history tables')
    if 'observation_players' not in names:
        items.append(('table', 'observation_players', PLAYER_SCHEMA))
    # Exclusive creation: never initialize an existing or foreign partial file.
    with partial.open('xb'):
        pass
    with closing(sqlite3.connect(partial)) as db, db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA synchronous=FULL')
        db.executescript('''
CREATE TABLE store_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE migration_progress(name TEXT PRIMARY KEY,cursor INTEGER NOT NULL);
CREATE TABLE payloads(id INTEGER PRIMARY KEY,kind TEXT NOT NULL,codec INTEGER NOT NULL,
 body BLOB NOT NULL,raw_sha TEXT NOT NULL,raw_len INTEGER NOT NULL);
CREATE INDEX payload_lookup ON payloads(kind,raw_sha);
CREATE TABLE player_dictionary(id INTEGER PRIMARY KEY,value TEXT NOT NULL UNIQUE,value_sha TEXT NOT NULL);
''')
        db.executemany('INSERT INTO store_meta VALUES(?,?)', [
            ('source_sha', source_hash), ('schema', c.canonical(items)),
            ('state', 'copying'), ('players_codec', 'dictionary'), ('legacy_version', str(legacy))])
        for typ, name, sql in items:
            if typ != 'table':
                continue
            quoted(name)
            field = FIELDS.get(name)
            if field:
                sql, count = re.subn(r'(?i)(CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?)' + re.escape(name) + r'\b',
                                     lambda m: m[1] + name + '_data', sql, count=1)
                if count != 1:
                    raise c.Damage('unsupported table declaration: ' + name)
                sql, count = re.subn(r'(?i)\b' + field + r'\s+TEXT\b', field + ' INTEGER REFERENCES payloads(id)', sql, count=1)
                if count != 1:
                    raise c.Damage('unsupported payload column: ' + name)
            db.execute(sql)
            db.execute('INSERT INTO migration_progress VALUES(?,0)', (name,))
            if field:
                cols = [r[1] for r in db.execute('PRAGMA table_info(' + quoted(name + '_data') + ')')]
                projection = ','.join("decode_id(d." + quoted(col) + ",'" + name + "') AS " + quoted(col)
                                      if col == field else 'd.' + quoted(col) for col in cols)
                db.execute('CREATE VIEW ' + quoted(name) + ' AS SELECT ' + projection + ' FROM ' + quoted(name + '_data') + ' d')
        for typ, name, sql in items:
            if typ == 'index':
                for table in FIELDS:
                    sql = re.sub(r'(?i)\bON\s+' + table + r'\s*\(', 'ON ' + table + '_data(', sql)
                db.execute(sql)
        db.execute('PRAGMA user_version=' + str(c.VERSION))


def migrate(source, target):
    source, target, partial = paths(source, target)
    # This command takes an offline backup, not an online main DB without its WAL.
    for suffix in ('-wal', '-journal'):
        sidecar = Path(str(source) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError('source must be a closed, checkpointed backup')
    source_hash = file_sha(source)
    with closing(ro(source)) as src:
        src.execute('BEGIN')
        legacy = src.execute('PRAGMA user_version').fetchone()[0]
        if legacy not in (1, 2, 3):
            raise c.Damage('expected legacy history schema 1, 2 or 3')
        checked(src)
        if not partial.exists():
            create(partial, src, source_hash, legacy)
        with closing(c.open_store(partial, write=True, legacy_version=legacy, require_complete=False)) as db:
            if db.execute("SELECT value FROM store_meta WHERE key='source_sha'").fetchone()[0] != source_hash:
                raise c.Damage('migration source changed')
            db.commit()
            for typ, name, _ in schema(src):
                if typ != 'table':
                    continue
                cursor = db.execute('SELECT cursor FROM migration_progress WHERE name=?', (name,)).fetchone()[0]
                cols = [r[1] for r in src.execute('PRAGMA table_info(' + quoted(name) + ')')]
                field = FIELDS.get(name)
                while True:
                    rows = src.execute('SELECT rowid,* FROM ' + quoted(name) + ' WHERE rowid>? ORDER BY rowid LIMIT 256', (cursor,)).fetchall()
                    if not rows:
                        break
                    with db:
                        db.execute('BEGIN IMMEDIATE')
                        for rowid, *values in rows:
                            if field:
                                values[cols.index(field)] = c.put(db, name, values[cols.index(field)])
                            table = name + '_data' if field else name
                            db.execute('INSERT INTO ' + quoted(table) + ' VALUES(' + ','.join('?' for _ in values) + ')', values)
                        cursor = rows[-1][0]
                        db.execute('UPDATE migration_progress SET cursor=? WHERE name=?', (cursor, name))
            checked(db)
            expected = {n: digest(src, n) for t, n, _ in schema(src) if t == 'table'}
            if {n: digest(db, n) for n in expected} != expected:
                raise c.Damage('migration logical checksum mismatch')
            if file_sha(source) != source_hash:
                raise c.Damage('migration source changed')
            with db:
                db.execute("UPDATE store_meta SET value='complete' WHERE key='state'")
            if db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]:
                raise c.Damage('output checkpoint busy')
    publish(partial, target)
    return {'legacy_version': legacy, 'tables': expected, 'bytes': target.stat().st_size}


def legacy_version(source):
    with closing(ro(source)) as db:
        if db.execute('PRAGMA user_version').fetchone()[0] != c.VERSION:
            raise c.Damage('expected compressed history schema')
        row = db.execute("SELECT value FROM store_meta WHERE key='legacy_version'").fetchone()
        if not row or row[0] not in ('1', '2', '3'):
            raise c.Damage('invalid legacy schema')
        return int(row[0])


def export_old(source, target):
    source, target, partial = paths(source, target)
    if partial.exists():
        raise FileExistsError(partial)
    legacy = legacy_version(source)
    with closing(c.open_store(source, legacy_version=legacy)) as db:
        db.execute('BEGIN')
        items = json.loads(db.execute("SELECT value FROM store_meta WHERE key='schema'").fetchone()[0])
        names = {n for _, n, _ in items}
        for typ, name, sql in schema(db):
            if typ == 'table' and name in BUSINESS and name not in names:
                items.append((typ, name, sql)); names.add(name)
        business = {n for t, n, _ in items if t == 'table'}
        for name, owner, sql in db.execute("SELECT name,tbl_name,sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"):
            owner = owner.removesuffix('_data')
            if owner in business and name not in names:
                sql = re.sub(r'(?i)\bON\s+' + owner + '_data' + r'\s*\(', 'ON ' + owner + '(', sql)
                items.append(('index', name, sql)); names.add(name)
        with partial.open('xb'):
            pass
        with closing(sqlite3.connect(partial)) as out, out:
            expected = {}
            for typ, name, sql in items:
                if typ != 'table':
                    continue
                out.execute(sql)
                cols = [r[1] for r in db.execute('PRAGMA table_info(' + quoted(name) + ')')]
                out.executemany('INSERT INTO ' + quoted(name) + ' VALUES(' + ','.join('?' for _ in cols) + ')',
                                db.execute('SELECT * FROM ' + quoted(name)))
                expected[name] = digest(db, name)
            for typ, name, sql in items:
                if typ == 'index':
                    out.execute(sql)
            out.execute('PRAGMA user_version=' + str(legacy))
            checked(out)
            if {n: digest(out, n) for n in expected} != expected:
                raise c.Damage('export logical checksum mismatch')
    publish(partial, target)
    return {'legacy_version': legacy, 'tables': expected, 'bytes': target.stat().st_size}


def diagnose(source):
    legacy = legacy_version(source)
    with closing(c.open_store(source, legacy_version=legacy)) as db:
        db.execute('BEGIN')
        checked(db)
        count = 0
        for ident, kind in db.execute('SELECT id,kind FROM payloads'):
            c.get(db, ident, kind)
            count += 1
        return {'valid': True, 'payloads': count, 'legacy_version': legacy}
