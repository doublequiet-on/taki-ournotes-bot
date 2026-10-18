"""Recover public player IDs/names from exact cached observations, without fetching or sending.

Dry-run by default. --apply requires a new SQLite online backup in --backup-dir.
Only the additive observation_players table is changed; scores and quality remain intact.
"""
import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ournotes_bot.sources.cutoff_history import CutoffHistory, HaneokaCutoffHistory
from ournotes_bot.sources.moenotes_events import BoardSnapshot, EventSnapshot, EventSong


def candidates(cache_dir, history_file, source):
    """Only allowlisted cache fields; never copy decks, profile details or headers."""
    with closing(sqlite3.connect(history_file.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        music = {(r[0], r[1], r[2]): r[3] for r in db.execute('SELECT server,event,challenge,music FROM boards')}
    for path in sorted(cache_dir.glob('*.json')):
        if path.stat().st_size > 4_000_000:
            continue
        data = json.loads(path.read_text('utf-8'))
        key = data.get('key', '')
        if not key.startswith('board:'):
            continue
        pattern = r'board:(jp|tw|kr|en):([1-9][0-9]{0,19}):([1-9][0-9]{0,19})' + (r':[a-f0-9]{64}(?::avatar-v2)?' if source == 'haneoka' else '')
        match = re.fullmatch(pattern, key)
        if (not match or data.get('schema') != 1 or path.stem != hashlib.sha256(key.encode()).hexdigest()):
            raise ValueError('invalid board cache identity: ' + path.name)
        region, event_id, challenge = match.groups()
        mid = music.get((region, event_id, challenge))
        if mid is None:
            continue
        payload = data['payload']
        headers = data.get('headers', {})
        scores = payload['scores']
        if (not isinstance(scores, list) or not 1 <= len(scores) <= 100
                or any(n is not None and (type(n) is not int or n < 0) for n in scores)):
            raise ValueError('invalid cached scores: ' + path.name)
        for field in ('player_ids', 'player_names'):
            if field in payload and (not isinstance(payload[field], list) or len(payload[field]) != len(scores)):
                raise ValueError('misaligned cached players: ' + path.name)
        if source == 'tracker' and headers.get('x-position-source') != 'responseOrder':
            continue
        if source == 'haneoka' and payload.get('unsafe') is not False:
            continue
        fetched = payload.get('fetched') if source == 'haneoka' else headers.get('x-fetched-at')
        server = payload.get('server') if source == 'haneoka' else headers.get('x-server-time')
        received = data.get('received')
        if type(received) not in (int, float) or received <= 0:
            raise ValueError('invalid cache receipt time')
        song = EventSong(challenge, mid, '歌曲 ID ' + mid)
        event = EventSnapshot(region, event_id, '活动 ' + event_id, 'historical', None, None, (song,), source=source)
        board = BoardSnapshot(song, tuple(scores), int(fetched), int(server), int(received * 1000),
                              player_ids=tuple(payload.get('player_ids', ())), player_names=tuple(payload.get('player_names', ())),
                              source=source)
        yield key, event, board


def run(cache_dir, history_file, source, *, apply=False, backup_dir=None):
    store = (HaneokaCutoffHistory if source == 'haneoka' else CutoffHistory)(history_file)
    captured = list(candidates(cache_dir, history_file, source))
    planned = [(key, event, board, store.backfill_players(event, board)) for key, event, board in captured]
    matched = [row for row in planned if row[3] == 'matched']
    backup = None
    if apply and backup_dir is None:
        raise ValueError('--apply requires --backup-dir')
    if apply and matched:
        backup_dir.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(backup_dir).free < history_file.stat().st_size + 512 * 1024 * 1024:
            raise OSError('insufficient backup disk')
        backup = backup_dir / f'{history_file.stem}-{time.time_ns()}.sqlite3'
        with closing(sqlite3.connect(history_file.resolve().as_uri() + '?mode=ro', uri=True)) as src:
            with closing(sqlite3.connect(backup)) as dst:
                src.backup(dst)
                if dst.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise ValueError('backup check failed')
    results = []
    for key, event, board, planned_status in planned:
        status = store.backfill_players(event, board, apply=True) if apply and planned_status == 'matched' else planned_status
        results.append({'key': key, 'source_ms': board.fetched_ms, 'status': status})
    return {'source': source, 'apply': apply, 'backup': str(backup) if backup else None,
            'counts': dict(Counter(r['status'] for r in results)), 'results': results}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cache-dir', type=Path, required=True)
    p.add_argument('--history-file', type=Path, required=True)
    p.add_argument('--source', choices=('tracker', 'haneoka'), required=True)
    p.add_argument('--apply', action='store_true')
    p.add_argument('--backup-dir', type=Path)
    args = p.parse_args()
    print(json.dumps(run(args.cache_dir, args.history_file, args.source, apply=args.apply, backup_dir=args.backup_dir), ensure_ascii=False))


if __name__ == '__main__':
    main()
