# L3
# Input: Independent cache path, TTL and anonymous release-pinned Catalog responses.
# Output: Bounded validated JP/intl derivatives, captured persistence facts and same-release Meta inputs.
# Pos: Sources / Haneoka main Catalog orchestration; see ../L2-2.md.
# Effects/Dependencies: Scoped GET, atomic source cache and two-slot derived cache (64 MiB); no foreign source retry.

from __future__ import annotations

import hashlib
from copy import deepcopy
import json
import math
import os
import re
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from dataclasses import dataclass, fields, is_dataclass
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
DERIVED_CACHE_BYTES = 64 * 1024 * 1024
# Download accounting also includes discovery indexes and batch envelopes which
# are not retained in the snapshot. Keep the existing cache/rollback limit.
MAX_DOWNLOAD_BYTES = 32_000_000
ERRORS = (OSError, ValueError, TypeError, KeyError, IndexError, RuntimeError, OverflowError, AttributeError)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


_EVENT_BANNER = re.compile(r"/assets/jp/Assets/AddressableResources/Image/Event/[0-9]+/Banner/[A-Za-z0-9_-]+\.(?:png|webp)")


def persistent_snapshot(saved):
    """Keep the shared cache readable by versions predating event banner support."""
    snapshot = deepcopy(saved)
    for event in snapshot["documents"]["jp"].get("events", {}).values():
        image = event.get("image")
        if isinstance(image, str) and _EVENT_BANNER.fullmatch(image):
            event["image"] = ""
    return snapshot


class CatalogRecords(tuple):
    """Records owned by one validated publication, with a content-derived digest."""
    def __new__(cls, records):
        result = super().__new__(cls, records)
        digest = hashlib.sha256()
        for collection in records:
            digest.update(repr(collection).encode("utf-8"))
        result.fingerprint = digest.hexdigest()
        return result


class CatalogResult(tuple):
    """Keep the existing records/metadata unpacking contract and its matching info."""
    def __new__(cls, records, metadata, info):
        result = super().__new__(cls, (records, metadata))
        result.info = info
        return result


@dataclass
class CatalogPublication:
    snapshot: dict
    fingerprint: str
    variants: dict


def retained_size(*roots):
    """Estimate the retained Python object graph once per cache change, sharing once."""
    seen, pending, total = set(), list(roots), 0
    while pending:
        value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        total += sys.getsizeof(value)
        if isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, (list, tuple, set, frozenset)):
            pending.extend(value)
            if hasattr(value, "__dict__"):
                pending.append(vars(value))
        elif is_dataclass(value) and not isinstance(value, type):
            pending.extend(getattr(value, item.name) for item in fields(value))
            if hasattr(value, "__dict__"):
                pending.append(vars(value))
    return total


def validate_timestamp(saved, now):
    fetched = saved["fetched_at"]
    if type(fetched) not in (int, float) or not math.isfinite(fetched) or not 0 < fetched <= now + 300:
        raise ValueError("invalid Catalog timestamp")
    return fetched


def validate(saved, now, *, stale=False):
    if not isinstance(saved, dict) or saved.get("schema") != 1 or saved.get("source") != SOURCE:
        raise ValueError("foreign Catalog cache")
    fetched = validate_timestamp(saved, now)
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
    records = CatalogRecords(convert(saved["documents"], saved["identities"]["jp"], stamp, stale=stale))
    info = convert_info(saved["documents"], saved["identities"]["jp"], stamp, saved["identities"]["intl"])
    return CatalogResult(records, {"source": SOURCE, "data_source": "haneoka", "data_version": saved["identities"]["jp"]["releaseId"],
                     "text_version": saved["identities"]["intl"]["releaseId"], "cached_at": stamp,
                     "song_count": len(records[0]), "card_count": len(records[1]), "support_card_count": len(records[2]),
                     "member_skill_index_complete": bool(records[1]) and all(c.catalog["skill_index_complete"] for c in records[1]),
                     "card_catalog_version": 1,
                     "info_catalog_available": {key: key in info.available
                                                for key in ("characters", "gachas", "activities")},
                     "change_summary": saved.get("change_summary")}, info)


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
    def __init__(self, path, ttl_hours=6, *, fetch=public_get, clock=time.time,
                 derived_cache_bytes=DERIVED_CACHE_BYTES):
        self.path, self.ttl = path, ttl_hours * 3600
        self.fetch, self.clock = fetch, clock
        self.saved = None
        self._query_snapshot = None
        self._saved_publication = None
        self._query_publication = None
        if type(derived_cache_bytes) is not int or derived_cache_bytes < 0:
            raise ValueError("invalid Catalog derived cache budget")
        self.derived_cache_bytes = derived_cache_bytes
        self._derived_hits = self._derived_builds = self._derived_bypasses = 0
        self._derived_bytes = 0
        self.lock = threading.RLock()
        self.state = "unknown"
        self._durable_initialized = False
        self._durable_summary = None
        self._durable_fetched_at = None

    def _publication(self, result):
        """Attach persistence facts independently of the latest refresh attempt."""
        metadata = deepcopy(result[1])
        metadata["change_summary_saved"] = metadata["change_summary"] == self._durable_summary
        metadata["last_successful_sync_at"] = (
            datetime.fromtimestamp(self._durable_fetched_at, timezone.utc).isoformat()
            if self._durable_fetched_at is not None else None)
        return CatalogResult(result[0], metadata, result.info)

    def _publications(self):
        publications = []
        for publication in (self._saved_publication, self._query_publication):
            if publication is not None and all(publication is not old for old in publications):
                publications.append(publication)
        return publications

    def _limit_derived(self, preferred):
        publications = self._publications()
        self._derived_bytes = retained_size(*(p.variants for p in publications if p.variants))
        # Keep the just-requested publication if it fits; old query snapshots may
        # still exist, but their reconstructible derivatives need not be retained.
        for publication in sorted(publications, key=lambda p: p is preferred):
            if self._derived_bytes <= self.derived_cache_bytes:
                break
            publication.variants.clear()
            self._derived_bypasses += 1
            self._derived_bytes = retained_size(*(p.variants for p in publications if p.variants))

    def _new_publication(self, snapshot, *, stale=False):
        result = validate(snapshot, self.clock(), stale=stale)
        self._derived_builds += 1
        return CatalogPublication(snapshot, hashlib.sha256(encoded(snapshot)).hexdigest(), {stale: result}), result

    def _derived(self, publication, *, stale=False):
        # Currentness still requires the caller's identity probe; the timestamp
        # check also remains live when a previously validated snapshot is reused.
        validate_timestamp(publication.snapshot, self.clock())
        if stale in publication.variants:
            self._derived_hits += 1
            return publication.variants[stale]
        result = validate(publication.snapshot, self.clock(), stale=stale)
        self._derived_builds += 1
        publication.variants[stale] = result
        self._limit_derived(publication)
        return result

    def _saved_result(self, *, stale=False):
        publication = self._saved_publication
        if publication is None or publication.snapshot is not self.saved:
            publication, result = self._new_publication(self.saved, stale=stale)
            self._saved_publication = publication
            self._limit_derived(publication)
            return result
        return self._derived(publication, stale=stale)

    def cache_metrics(self):
        with self.lock:
            publications = self._publications()
            return {"publications": len(publications),
                    "variants": sum(len(p.variants) for p in publications),
                    "retained_bytes": self._derived_bytes, "byte_limit": self.derived_cache_bytes,
                    "hits": self._derived_hits, "builds": self._derived_builds,
                    "bypasses": self._derived_bypasses}

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
        saved = persistent_snapshot(saved)
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
            result = self.load(refresh)
            return result[0], result[1], result.info

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
            if self.saved is not None and self.saved['identities'] == current:
                result = self._saved_result()
                publication = self._saved_publication
            elif self._query_snapshot is not None and self._query_snapshot['identities'] == current:
                publication = self._query_publication
                if publication is None or publication.snapshot is not self._query_snapshot:
                    publication, result = self._new_publication(self._query_snapshot)
                else:
                    result = self._derived(publication)
            else:
                publication, result = self._new_publication(self._collect())
            # The request and background slots share a publication only when they
            # actually capture the same validated snapshot, not merely a label.
            self._query_snapshot = publication.snapshot
            if self._query_publication is not publication:
                self._query_publication = publication
                self._limit_derived(publication)
            published = self._publication(result)
            return published[0], published[1], published.info

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
                        self._saved_result()
                        try:
                            disk_valid = self._read() == persistent_snapshot(before)
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
            published = self._publication(self._saved_result(stale=self.state=='stale'))
            records, metadata = published
            info = published.info
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
                result = self._saved_result(stale=self.state == "stale")
                if self.state not in {"unsaved", "stale"}:
                    self.state = "cached"
                return self._publication(result)
            try:
                candidate = self._collect()
                candidate["change_summary"] = added_since(self.saved, candidate)
                publication, result = self._new_publication(candidate)
                # Commit source and derivatives together only after validation.
                self.saved, self._saved_publication = candidate, publication
                self._limit_derived(publication)
            except ERRORS as exc:
                if self.saved is None:
                    raise DataError("Haneoka 主资料同步失败，且无已验证缓存") from exc
                self.state = "stale"
                return self._publication(self._saved_result(stale=True))
            self.state = "fresh"
            try:
                self._write(candidate)
            except ERRORS:
                self.state = "unsaved"
            else:
                self._durable_summary = candidate["change_summary"]
                self._durable_fetched_at = candidate["fetched_at"]
            return self._publication(result)
