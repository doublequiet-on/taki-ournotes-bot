# L3
# Input: Independent cache path, TTL and anonymous release-pinned Catalog responses.
# Output: Validated JP snapshot, verified intl text, persistence facts and same-release Meta inputs.
# Pos: Sources / Haneoka main Catalog orchestration; see ../L2-2.md.
# Effects/Dependencies: Scoped bounded GET, atomic independent cache; never retries another source.

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlencode

from ...data import DataError
from .catalog_records import convert, integer
from .info_catalog import convert_info
from ..json_validation import decode_json
from .transport import ORIGIN, public_get, public_http_scope
from .identity import resource_identity as identity, check_headers

SOURCE = ORIGIN + "/api/v1/servers/jp/"
ENTITIES = {"songs": "musicId", "cards": "cardId", "support-cards": "supportCardId",
            "characters": "characterId", "bands": "bandId"}
MAX_BYTES = 24_000_000
# Download accounting also includes discovery indexes and batch envelopes which
# are not retained in the snapshot. Keep the existing cache/rollback limit.
MAX_DOWNLOAD_BYTES = 32_000_000
ERRORS = (OSError, ValueError, TypeError, KeyError, IndexError, RuntimeError, OverflowError, AttributeError)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def validate(saved, now, *, stale=False):
    if not isinstance(saved, dict) or saved.get("schema") != 1 or saved.get("source") != SOURCE:
        raise ValueError("foreign Catalog cache")
    fetched = saved["fetched_at"]
    if type(fetched) not in (int, float) or not math.isfinite(fetched) or not 0 < fetched <= now + 300:
        raise ValueError("invalid Catalog timestamp")
    for server in ("jp", "intl"):
        ident = identity(saved["identities"][server], server)
        catalog = saved["documents"][server]["catalog"]
        if catalog.get("server") != server or catalog.get("sourceId") != ident["sourceId"]:
            raise ValueError("Catalog identity mismatch")
        for resource, key in ENTITIES.items():
            rows = saved["documents"][server][resource]
            count = integer(catalog["resources"][resource]["count"], 1, 5000)
            if not isinstance(rows, dict) or len(rows) != count:
                raise ValueError("incomplete Catalog index")
            for k, row in rows.items():
                if k != str(integer(row[key], 1)):
                    raise ValueError("Catalog row identity mismatch")
    stamp = datetime.fromtimestamp(fetched, timezone.utc).isoformat()
    records = convert(saved["documents"], saved["identities"]["jp"], stamp, stale=stale)
    info = convert_info(saved["documents"], saved["identities"]["jp"], stamp, saved["identities"]["intl"])
    return records, {"source": SOURCE, "data_source": "haneoka", "data_version": saved["identities"]["jp"]["releaseId"],
                     "text_version": saved["identities"]["intl"]["releaseId"], "cached_at": stamp,
                     "song_count": len(records[0]), "card_count": len(records[1]), "support_card_count": len(records[2]),
                     "member_skill_index_complete": bool(records[1]) and all(c.catalog["skill_index_complete"] for c in records[1]),
                     "card_catalog_version": 1,
                     "info_catalog_available": {key: key in info.available
                                                for key in ("characters", "gachas", "activities")},
                     "change_summary": saved.get("change_summary")}


CHANGE_RESOURCES = ("songs", "cards", "support-cards", "characters", "gacha", "events")


def added_since(previous, candidate):
    """Keep the latest observed additions across refreshes with unchanged IDs."""
    if previous is None:
        return None
    before, after = previous["documents"]["jp"], candidate["documents"]["jp"]
    additions = {name: sorted(int(key) for key in after[name].keys() - before[name].keys())
                 if name in before and name in after else []
                 for name in CHANGE_RESOURCES}
    if any(additions.values()):
        return {"at": candidate["fetched_at"], "ids": additions}
    return previous.get("change_summary") or {"at": candidate["fetched_at"], "ids": additions}


class CatalogRepository:
    def __init__(self, path, ttl_hours=6, *, fetch=public_get, clock=time.time):
        self.path, self.ttl = path, ttl_hours * 3600
        self.fetch, self.clock = fetch, clock
        self.saved = None
        self._query_snapshot = None
        self.lock = threading.RLock()
        self.state = "unknown"
        self._durable_initialized = False
        self._durable_summary = None
        self._durable_fetched_at = None

    def _publication(self, result):
        """Attach persistence facts independently of the latest refresh attempt."""
        metadata = result[1]
        metadata["change_summary_saved"] = metadata["change_summary"] == self._durable_summary
        metadata["last_successful_sync_at"] = (
            datetime.fromtimestamp(self._durable_fetched_at, timezone.utc).isoformat()
            if self._durable_fetched_at is not None else None)
        return result

    def _read(self):
        if self.path.stat().st_size > MAX_BYTES:
            raise ValueError("Catalog cache size limit")
        envelope = decode_json(self.path.read_bytes())
        saved = envelope["snapshot"]
        if hashlib.sha256(encoded(saved)).hexdigest() != envelope["sha256"]:
            raise ValueError("Catalog cache checksum mismatch")
        validate(saved, self.clock())
        return saved

    def _write(self, saved):
        body = encoded({"snapshot": saved, "sha256": hashlib.sha256(encoded(saved)).hexdigest()})
        if len(body) > MAX_BYTES:
            raise ValueError("Catalog cache size limit")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(body)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    @public_http_scope()
    def _collect(self):
        started, total = time.monotonic(), 0
        budget_lock = threading.Lock()
        identities, documents = {}, {}

        def get(server, resource, ident=None, ids=()):
            nonlocal total
            if time.monotonic() - started > 150:
                raise ValueError("Catalog refresh deadline")
            query = [("release", ident["releaseId"])] if ident else [("projection", "identity")]
            query += [("id", key) for key in ids]
            url = f"{ORIGIN}/api/v1/servers/{server}/{resource}?{urlencode(query)}"
            raw, headers = self.fetch(url, 8_000_000, 20)
            with budget_lock:
                total += len(raw)
                if len(raw) > 8_000_000 or total > MAX_DOWNLOAD_BYTES:
                    raise ValueError("Catalog download budget")
            value = decode_json(raw)
            check_headers(headers, ident or identity(value, server))
            return value

        for server in ("jp", "intl"):
            ident = identities[server] = identity(get(server, "release"), server)
            catalog = get(server, "catalog", ident)
            if catalog.get("server") != server or catalog.get("sourceId") != ident["sourceId"]:
                raise ValueError("Catalog identity mismatch")

            @public_http_scope()
            def collection(resource):
                count = integer(catalog["resources"][resource]["count"], 1, 5000)
                rows = get(server, resource, ident)
                if not isinstance(rows, dict) or len(rows) != count:
                    raise ValueError("incomplete Catalog index")
                for key, row in rows.items():
                    if key != str(integer(row[ENTITIES[resource]], 1)):
                        raise ValueError("Catalog index identity mismatch")
                if resource in {"songs", "cards", "support-cards"}:
                    full, keys = {}, sorted(rows)
                    for offset in range(0, len(keys), 100):
                        requested = keys[offset:offset + 100]
                        batch = get(server, resource, ident, requested)
                        if batch.get("missing") != [] or set(batch.get("items", {})) != set(requested):
                            raise ValueError("incomplete Catalog batch")
                        full.update(batch["items"])
                    rows = full
                return resource, rows

            with ThreadPoolExecutor(max_workers=3) as pool:
                docs = dict(pool.map(collection, ENTITIES))
            docs["catalog"] = catalog
            if server=="jp" and "song-meta" in catalog["resources"]:
                meta=get(server,"song-meta",ident)
                expected=integer(catalog["resources"]["song-meta"]["count"],1,5000)
                if not isinstance(meta,dict) or len(meta)!=expected or any(key not in docs["songs"] for key in meta):
                    raise ValueError("Incomplete song metadata")
                docs["song-meta"]=meta
            docs["skill-reference"] = get(server, "skill-reference", ident)
            docs["ui-marks"] = get(server, "ui-marks", ident)
            progression = get(server, "progression", ident)
            docs["progression"] = {k: progression[k] for k in ("memberCardLevels", "memberCardRanks", "supportCardLevels", "supportCardRanks")}
            docs["progression"].update({k: progression.get(k,[]) for k in ("memberCardLevelLimits","playerRanks")})
            events=get(server,'events',ident)
            event_ids=[str(integer(int(key),1)) for key,row in events['entries'].items() if row.get('kind')=='game-event']
            if len(event_ids)>5000:raise ValueError('Event collection size limit')
            docs['events']={key:get(server,'events/'+key,ident) for key in event_ids}
            docs['gacha']=get(server,'gacha',ident)['entries']
            documents[server] = docs
        return {"schema": 1, "source": SOURCE, "fetched_at": self.clock(), "identities": identities, "documents": documents}

    def load_with_info(self, refresh=False):
        """Capture converted facts from the same committed source snapshot as cards."""
        with self.lock:
            records, metadata = self.load(refresh)
            info = convert_info(self.saved["documents"], self.saved["identities"]["jp"], metadata["cached_at"], self.saved["identities"]["intl"])
            return records, metadata, info

    @public_http_scope()
    def probe_identities(self):
        with self.lock:
            current={}
            for server in ('jp','intl'):
                raw,headers=self.fetch(f'{ORIGIN}/api/v1/servers/{server}/release?projection=identity',512000,20)
                current[server]=identity(decode_json(raw),server)
                check_headers(headers,current[server])
            return current

    def metrics_documents(self, server, current):
        """Capture already-published source documents for an independently checked identity.

        Published snapshots are replaced, never mutated. Capture references without
        waiting for a background refresh; the Meta consumer revalidates its contract.
        """
        for snapshot in (self._query_snapshot, self.saved):
            if snapshot is not None and snapshot['identities'][server] == current:
                return snapshot['documents'][server]
        return None

    @public_http_scope()
    def current_with_info(self):
        """Request-local current data: successful identity check, never stale fallback."""
        with self.lock:
            current = self.probe_identities()
            candidate = next((s for s in (self.saved, self._query_snapshot)
                              if s is not None and s['identities'] == current), None)
            if candidate is None:
                candidate = self._collect()
            result = validate(candidate, self.clock())
            # A query must not consume the background refresh's additions or
            # change its persistence/fallback state. Reuse only after a fresh probe.
            self._query_snapshot = candidate
            records, metadata = self._publication(result)
            info = convert_info(candidate['documents'], candidate['identities']['jp'], metadata['cached_at'], candidate['identities']['intl'])
            return records, metadata, info

    def synchronize_with_info(self, force=False):
        """Explicit administration refresh; separates publication from durable success."""
        with self.lock:
            if self.saved is None:
                try:
                    self.saved = self._read()
                except ERRORS:
                    pass
            before = self.saved
            prior_ids = {name: set(before['documents']['jp'].get(name, {})) for name in CHANGE_RESOURCES} if before else None
            previous_version = {s: v['releaseId'] for s,v in before['identities'].items()} if before else None
            result_code = None
            stage = 'identity' if not force else 'catalog'
            try:
                if not force:
                    current = self.probe_identities()
                    same = before is not None and all(
                        all(before['identities'][s][k] == current[s][k] for k in ('releaseId','sourceId')) for s in ('jp','intl'))
                    if same:
                        validate(before, self.clock())
                        try:
                            disk_valid = self._read() == before
                        except ERRORS:
                            disk_valid = False
                        if not disk_valid:
                            stage = 'persistence'
                            self._write(before)
                        self._durable_fetched_at = before['fetched_at']
                        self._durable_summary = before.get('change_summary')
                        self.state, result_code = 'cached', 'upstream_unchanged'
                if result_code is None:
                    stage = 'catalog'
                    self.load(refresh=True)
                    result_code = {'fresh':'synchronized','unsaved':'updated_unsaved','stale':'refresh_failed_cached'}.get(self.state, 'no_available_data')
            except ERRORS:
                if stage == 'persistence' and self.saved is not None:
                    self.state, result_code = 'unsaved', 'updated_unsaved'
                else:
                    self.state, result_code = ('stale','refresh_failed_cached') if self.saved else ('unknown','no_available_data')
            summary = {'code':result_code,'stage':stage,'before_version':previous_version,
                       'after_version':{s:v['releaseId'] for s,v in self.saved['identities'].items()} if self.saved else None,
                       'persisted':self.saved is not None and self._durable_fetched_at == self.saved['fetched_at'],
                       'published':result_code in ('synchronized','updated_unsaved'),'data_available':self.saved is not None,
                       'added_counts':None,'checked_at':self.clock()}
            if self.saved is None:
                return None, None, None, summary
            records, metadata = self._publication(validate(self.saved, self.clock(), stale=self.state=='stale'))
            info = convert_info(self.saved['documents'], self.saved['identities']['jp'], metadata['cached_at'], self.saved['identities']['intl'])
            if prior_ids is not None:
                summary['added_counts'] = {name:len(set(self.saved['documents']['jp'].get(name, {}))-prior_ids[name]) for name in CHANGE_RESOURCES}
            return records, metadata, info, summary

    def load(self, refresh=False):
        with self.lock:
            if self.saved is None:
                try:
                    self.saved = self._read()
                except ERRORS:
                    pass
            if not self._durable_initialized:
                if self.saved is not None:
                    self._durable_summary = self.saved.get("change_summary")
                    self._durable_fetched_at = self.saved["fetched_at"]
                self._durable_initialized = True
            now = self.clock()
            if not refresh and self.saved and 0 <= now - self.saved["fetched_at"] < self.ttl:
                result = validate(self.saved, now, stale=self.state == "stale")
                if self.state not in {"unsaved", "stale"}:
                    self.state = "cached"
                return self._publication(result)
            try:
                candidate = self._collect()
                result = validate(candidate, self.clock())
                candidate["change_summary"] = added_since(self.saved, candidate)
                result[1]["change_summary"] = candidate["change_summary"]
                # Commit the entire JP/text snapshot only after conversion succeeds.
                self.saved = candidate
            except ERRORS as exc:
                if self.saved is None:
                    raise DataError("Haneoka 主资料同步失败，且无已验证缓存") from exc
                self.state = "stale"
                return self._publication(validate(self.saved, self.clock(), stale=True))
            self.state = "fresh"
            try:
                self._write(candidate)
            except ERRORS:
                self.state = "unsaved"
            else:
                self._durable_summary = candidate["change_summary"]
                self._durable_fetched_at = candidate["fetched_at"]
            return self._publication(result)
