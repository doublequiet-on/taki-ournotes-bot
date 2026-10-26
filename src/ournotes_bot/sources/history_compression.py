# L3
# Input: Explicit compressed history files and original score/player JSON.
# Output: Lossless payload references and checked logical SQLite views.
# Pos: Sources / cutoff history physical storage; see L2-2.md.
# Effects: SQLite only; no network, credentials, scheduling or implicit migration.
"""Opt-in schema 107: exact payload deduplication, zlib and player dictionary."""
import hashlib
import json
import logging
import sqlite3
import struct
import zlib
from collections import OrderedDict
from pathlib import Path

VERSION = 107
_LOGGER = logging.getLogger(__name__)

class Damage(sqlite3.DatabaseError):
    """Corrupt compressed storage; messages contain IDs, never player values."""

class Connection(sqlite3.Connection):
    pass


def open_store(path, *, write=False, legacy_version, require_complete=True):
    path = Path(path).resolve()
    db = sqlite3.connect(path.as_uri() + ('?mode=rw' if write else '?mode=ro'),
                         uri=True, timeout=.15, factory=Connection)
    try:
        if db.execute('PRAGMA user_version').fetchone()[0] != VERSION:
            raise Damage('unsupported compressed history version')
        meta = dict(db.execute('SELECT key,value FROM store_meta'))
        if meta.get('legacy_version') != str(legacy_version):
            raise Damage('wrong history source schema')
        if require_complete and meta.get('state') != 'complete':
            raise Damage('migration incomplete')
        if meta.get('players_codec') != 'dictionary':
            raise Damage('unsupported player codec')
        columns = {row[1] for row in db.execute('PRAGMA table_info(observations_data)')}
        required = {'id', 'server', 'event', 'challenge', 'music', 'source_ms',
                    'received_ms', 'scores', 'digest', 'quality'}
        if legacy_version in (2, 3):
            required |= {'source', 'time_kind', 'contract_version'}
        if not required <= columns:
            raise Damage('missing history columns')
        db.codec = 'dictionary'
        db.words = OrderedDict()
        db.decoded = OrderedDict()
        db.decode_calls = 0
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('PRAGMA synchronous=FULL')
        def decode(ident, kind):
            try:
                return get(db, ident, kind)
            except Damage as exc:
                _LOGGER.error('Compressed history corruption: %s', exc)
                raise
        db.create_function('decode_id', 2, decode)
        if write:
            db.execute('BEGIN IMMEDIATE')
        return db
    except BaseException:
        db.close()
        raise


def physical_table(db, name):
    return name + '_data' if isinstance(db, Connection) else name


def encode(db, kind, raw):
    return put(db, kind, raw) if isinstance(db, Connection) else raw

def varint(n):
    if type(n) is not int or n<0:raise ValueError('invalid unsigned integer')
    b=bytearray()
    while n>=128:b.append((n&127)|128);n>>=7
    b.append(n);return bytes(b)

def unvarint(data,pos):
    n=shift=0
    while pos<len(data) and shift<=4096:
        v=data[pos];pos+=1;n|=(v&127)<<shift
        if v<128:return n,pos
        shift+=7
    raise ValueError('truncated or oversized integer')

def envelope(raw):
    packed=zlib.compress(raw,6)
    return struct.pack('>I',zlib.crc32(raw))+(b'\1'+packed if len(packed)<len(raw) else b'\0'+raw)

def unenvelope(body):
    if len(body)<5:raise ValueError('truncated header')
    check,=struct.unpack('>I',body[:4]);flag=body[4];raw=body[5:]
    if flag==1:
        d=zlib.decompressobj();raw=d.decompress(raw,65537)
        if not d.eof or d.unused_data or d.unconsumed_tail:raise ValueError('invalid compressed payload')
    elif flag!=0:raise ValueError('unknown compression flag')
    if len(raw)>65536 or zlib.crc32(raw)!=check:raise ValueError('payload checksum mismatch')
    return raw

def pack_players(values,ids):
    if not isinstance(values,list) or len(values)>100:raise ValueError('invalid players')
    b=bytearray(varint(len(values)))
    for pair in values:
        if not isinstance(pair,list) or len(pair)!=2:raise ValueError('invalid player pair')
        for value in pair:
            if value is not None and not isinstance(value,str):raise ValueError('invalid player scalar')
            b+=varint(0 if value is None else ids[value])
    return envelope(bytes(b))

def unpack_players(body,words):
    data=unenvelope(body);count,pos=unvarint(data,0)
    if count>100:raise ValueError('too many players')
    values=[]
    for _ in range(count):
        pair=[]
        for _ in range(2):
            ident,pos=unvarint(data,pos)
            if ident and ident not in words:raise ValueError('missing dictionary entry')
            pair.append(words[ident] if ident else None)
        values.append(pair)
    if pos!=len(data):raise ValueError('trailing player bytes')
    return values

def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))

def get(db, ident, kind):
    if kind not in {'observations', 'observation_players'}:
        raise Damage(f'payload={ident} unknown_kind')
    key = (ident, kind)
    if key in db.decoded:
        db.decoded.move_to_end(key); return db.decoded[key]
    row = sqlite3.Connection.execute(db, 'select kind,codec,body,raw_sha,raw_len from payloads where id=?', (ident,)).fetchone()
    prefix = f'payload={ident} kind={kind}'
    if not row:
        raise Damage(prefix+' missing_reference')
    stored_kind, codec, body, sha, length = row
    if stored_kind != kind:
        raise Damage(prefix+' wrong_kind')
    try:
        if codec == 0:
            d = zlib.decompressobj(); raw = d.decompress(body, 1048577)
            if len(raw)>1048576 or not d.eof or d.unused_data or d.unconsumed_tail:
                raise Damage(prefix+' invalid_zlib_frame')
            raw = raw.decode('utf8')
        elif codec == 2 and kind == 'observation_players':
            # Fetch each referenced value once per connection; scores never load words.
            class Words:
                def __contains__(self, word):
                    if word not in db.words:
                        r = sqlite3.Connection.execute(db, 'select value,value_sha from player_dictionary where id=?', (word,)).fetchone()
                        if not r:
                            raise Damage(prefix+f' dictionary={word} missing_word')
                        if hashlib.sha256(r[0].encode()).hexdigest()!=r[1]:
                            raise Damage(prefix+f' dictionary={word} word_checksum')
                        db.words[word]=r[0]
                        if len(db.words) > 4096:
                            db.words.popitem(last=False)
                    return True
                def __getitem__(self, word):
                    return db.words[word]
            raw = canonical(unpack_players(body, Words()))
        else:
            raise Damage(prefix+' unknown_codec')
    except Damage:
        raise
    except (ValueError, zlib.error, UnicodeError, IndexError, struct.error,
            TypeError, AttributeError, OverflowError) as exc:
        raise Damage(prefix+' invalid_payload:'+type(exc).__name__) from None
    if len(raw.encode())!=length or hashlib.sha256(raw.encode()).hexdigest()!=sha:
        raise Damage(prefix+' raw_checksum')
    db.decode_calls += 1
    db.decoded[key] = raw
    if len(db.decoded)>256:
        db.decoded.popitem(last=False)
    return raw

def put(db, kind, raw):
    if len(raw.encode('utf8')) > 1048576:
        raise Damage('payload exceeds 1 MiB')
    sha = hashlib.sha256(raw.encode()).hexdigest()
    for (ident,) in sqlite3.Connection.execute(db, 'select id from payloads where kind=? and raw_sha=?', (kind, sha)):
        if get(db, ident, kind)==raw:
            return ident  # exact equality, hash is only an index
    codec = 0; body = zlib.compress(raw.encode(), 6)
    if kind=='observation_players' and db.codec=='dictionary':
        values = json.loads(raw)
        if canonical(values)==raw:
            ids={}
            for pair in values:
                for word in pair:
                    if word is not None and word not in ids:
                        word_sha=hashlib.sha256(word.encode()).hexdigest()
                        db.execute('insert or ignore into player_dictionary(value,value_sha) values(?,?)', (word,word_sha))
                        ident, actual_sha=db.execute('select id,value_sha from player_dictionary where value=?',(word,)).fetchone()
                        if word_sha!=actual_sha:
                            raise Damage(f'dictionary={ident} word_checksum')
                        ids[word]=ident
            body=pack_players(values,ids); codec=2
    return db.execute('insert into payloads(kind,codec,body,raw_sha,raw_len) values(?,?,?,?,?)',
        (kind,codec,body,sha,len(raw.encode()))).lastrowid
