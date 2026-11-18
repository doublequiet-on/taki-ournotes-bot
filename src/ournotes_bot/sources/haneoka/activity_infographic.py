# L3: Fixed public JP assets, event details, exchange and isolated Haneoka scoring.
from dataclasses import dataclass
import gzip
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import TemporaryFile
import time

from ...domain.activity_infographic import exact_id, exchange_summary, nonnegative, reward_summary, source_date
from ...domain.query_cancellation import thread_cancel_event
from ..activity_infographic_store import write_atomic
from ..errors import ChartDataError
from ..json_validation import decode_json
from .catalog_assets import asset_url, attribute_url
from .chart_paths import chart_asset_url
from .identity import check_headers, release_identity
from .transport import ORIGIN, public_get

JP = '/api/v1/servers/jp/'
ENGINE_PATH = '/_astro/team-engine.YEHdIvBE.wasm'
ENGINE_SHA256 = 'e5938d2e0b73dc370926215aade0edf0f11c2e08c1de0e3d0c602eed135550cb'
ENGINE_LIMIT = 16_000_000


def check_cancelled():
    flag = thread_cancel_event.get()
    if flag is not None and flag.is_set():
        raise ChartDataError('activity infographic interrupted')


@dataclass(frozen=True)
class InfographicData:
    event: dict
    exchange: dict | None
    scores: tuple
    master: dict | None
    identity: dict
    marks: dict
    inventory: tuple[int, int] = (0, 0)

    def image(self, path):
        return asset_url(path, self.identity['releaseId']) if path else ''

    def attribute(self, value):
        return attribute_url(self.marks, value, self.identity['releaseId'])


def _engine_file(directory, fetch):
    path = directory / (ENGINE_SHA256 + '.wasm')
    if path.is_file() and path.stat().st_size <= ENGINE_LIMIT and sha256(path.read_bytes()).hexdigest() == ENGINE_SHA256:
        return path
    raw, _ = fetch(ORIGIN + ENGINE_PATH, ENGINE_LIMIT, 25)
    if sha256(raw).hexdigest() != ENGINE_SHA256:
        raise ChartDataError('activity scoring engine checksum mismatch')
    write_atomic(path, raw)
    return path


def run_engine(input_data, directory, fetch=public_get):
    node = os.environ.get('OURNOTES_INFOGRAPHIC_NODE')
    release = Path(os.environ.get('OURNOTES_RELEASE_SOURCE', '')).parent.name
    if not node and os.name != 'nt' and re.fullmatch('[a-f0-9]{40}', release):
        managed = Path('/opt/taki-combinations') / release / 'bin/node'
        if managed.is_file():
            node = str(managed)
    node = node or shutil.which('node')
    if not node:
        raise ChartDataError('Node is required for activity scoring')
    binary = _engine_file(directory, fetch)
    script = Path(__file__).with_name('activity_infographic_engine.cjs')
    deadline = time.monotonic() + 180
    environment = {'SystemRoot': os.environ['SystemRoot']} if os.name == 'nt' and 'SystemRoot' in os.environ else {}
    with TemporaryFile() as stdin, TemporaryFile() as stdout:
        stdin.write(json.dumps(input_data, ensure_ascii=False).encode())
        stdin.seek(0)
        with subprocess.Popen([node, str(script), str(binary)], stdin=stdin, stdout=stdout,
                              stderr=subprocess.DEVNULL, env=environment, **(
                                  {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {})) as child:
            try:
                while child.poll() is None:
                    check_cancelled()
                    if time.monotonic() >= deadline:
                        raise ChartDataError('activity scoring timed out')
                    time.sleep(0.1)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait()
            if child.returncode != 0:
                raise ChartDataError('activity scoring failed')
        stdout.seek(0)
        raw = stdout.read(96_001)
    if len(raw) > 96_000:
        raise ChartDataError('activity scoring response too large')
    return decode_json(raw)


class InfographicSource:
    def __init__(self, directory, *, fetch=public_get, solve=run_engine):
        self.directory, self.fetch, self.solve = Path(directory), fetch, solve

    def _get(self, url, limit, identity=None):
        check_cancelled()
        raw, headers = self.fetch(ORIGIN + url, limit, 12)
        if identity:
            check_headers(headers, identity)
        return decode_json(raw), headers

    def _resource(self, resource, identity, limit=2_000_000):
        return self._get(JP + resource + '?release=' + identity['releaseId'], limit, identity)[0]

    def _shop(self, event, period, identity):
        currency = event.get('eventItem')
        if currency is None:
            return None
        if not isinstance(currency, dict):
            raise ChartDataError('invalid event badge currency')
        key = (currency.get('resourceType'), currency.get('resourceId'))
        listings = self._resource('exchange', identity)['entries']
        if len(listings) > 1000:
            raise ChartDataError('activity exchange collection too large')
        shops = []
        for row in listings.values():
            category = row.get('category') or []
            if (source_date(row.get('startAt')) != period.start
                    or 'Event Exchange' not in category and 'イベント交換所' not in category):
                continue
            ident = exact_id(row['id'])
            shop = self._resource('exchange/' + ident, identity)
            candidate = shop.get('currency') or {}
            if (str(shop.get('id')) == ident and source_date(shop.get('startAt')) == period.start
                    and (candidate.get('resourceType'), candidate.get('resourceId')) == key):
                shops.append(shop)
        if len(shops) != 1 or not shops[0].get('products') or len(shops[0]['products']) > 120:
            raise ChartDataError('activity exchange pending or ambiguous')
        exchange_summary(shops[0])
        return shops[0]

    def _chart(self, master, song_id, identity):
        song = master['songs'][str(song_id)]
        difficulty = next((row for row in song['difficulty'] if row.get('difficulty') == 3), None)
        if difficulty is None:
            raise ChartDataError('challenge EXPERT chart pending')
        url = chart_asset_url(difficulty['file'], identity['releaseId'])
        check_cancelled()
        try:
            raw, headers = self.fetch(url, 2_000_000, 12)
        except ChartDataError:
            raw, headers = self.fetch(url.split('?', 1)[0], 2_000_000, 12)
        check_headers(headers, identity)
        if raw.startswith(b'\x1f\x8b'):
            with gzip.GzipFile(fileobj=BytesIO(raw)) as file:
                raw = file.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ChartDataError('challenge chart size limit')
        return {'song': {'songId': int(song_id), 'difficulty': 3}, 'chart': decode_json(raw)}

    def capture(self, period, song_ids):
        version, headers = self._get(JP + 'release?projection=identity', 8192)
        identity = release_identity(version, 'jp')
        check_headers(headers, identity)
        event = self._resource('events/' + exact_id(period.event_id), identity)
        if (str(event.get('id')) != period.event_id or source_date(event.get('startAt')) != period.start
                or source_date(event.get('endAt')) != period.end or not event.get('image')):
            raise ChartDataError('activity detail identity not ready')
        reward_summary(event)
        exchange = self._shop(event, period, identity)
        if not song_ids:
            if event.get('musicRankingDisabled') is not True:
                raise ChartDataError('challenge songs pending')
            return InfographicData(event, exchange, (), None, identity, {})
        marks = self._resource('ui-marks', identity)
        master, _ = self._get('/api/v1/team-builder/jp?release=' + identity['releaseId'] + '&v=4',
                              20_000_000, identity)
        if master.get('schema') != 'haneoka-team-builder-data-v1' or any(
                master.get('identity', {}).get(key) != identity[key] for key in ('server','releaseId','sourceId')):
            raise ChartDataError('unverified activity scoring data')
        chart_song_ids = {str(row['liveMusicId']) for row in master['challengeMusicTable']['rows']
                          if str(row.get('eventId')) == period.event_id}
        if set(song_ids) != chart_song_ids:
            raise ChartDataError('challenge association mismatch')
        charts = [self._chart(master, value, identity) for value in song_ids]
        characters = [row for row in master['masterCharacterRoster']['rows'] if row.get('isNonPlayable') is False]
        instruments = list(master['bandItems'].values())
        if len(characters) != 25 or not all(any(r['level'] == 15 for r in row['levels']) for row in instruments):
            raise ChartDataError('activity account baseline no longer matches the roster')
        player = {'characterRanks': {str(row['id']): 30 for row in characters}, 'characterTotalRank': 750,
                  'bandItems': {str(row['bandItemId']): 15 for row in instruments}, 'vipRank': 16,
                  'musicMemory': {}, 'characterMemory': {}}

        def cards(key, prefix):
            result = []
            for card in master[key].values():
                released = source_date(card.get('catalogMetadata', {}).get('releasedAt'))
                if type(released) is not int:
                    raise ChartDataError('card release time pending')
                if released <= period.start:
                    ident = int(exact_id(card['id']))
                    result.append({'key': prefix + str(ident), 'cardId': ident})
            return result

        payload = {'master': master, 'charts': charts, 'eventId': int(period.event_id), 'player': player,
                   'members': cards('members', 'm'), 'snaps': cards('snapshots', 's')}
        scores = self.solve(payload, self.directory, self.fetch)
        if not isinstance(scores, list) or [str(row.get('songId')) for row in scores] != list(song_ids):
            raise ChartDataError('mismatched challenge score results')
        member_ids = {row['key'] for row in payload['members']}
        snap_ids = {row['key'] for row in payload['snaps']}
        for row in scores:
            members, snaps, order = row['members'], row['snaps'], row['order']
            if (type(row['proven']) is not bool or not 0 < nonnegative(row['score'])
                    or len(members) != 5 or len(set(members)) != 5 or not set(members) <= member_ids
                    or len(snaps) != 5 or len(set(snaps)) != 5 or not set(snaps) <= snap_ids
                    or sorted(order) != sorted(members)):
                raise ChartDataError('invalid challenge score result')
        return InfographicData(event, exchange, tuple(scores), master, identity, marks,
                               (len(member_ids), len(snap_ids)))
