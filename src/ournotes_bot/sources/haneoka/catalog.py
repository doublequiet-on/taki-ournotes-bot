# L3
# Input: Independent cache path, TTL and anonymous release-pinned Catalog responses.
# Output: Validated complete JP domain snapshot with separately verified intl text.
# Pos: Sources / Haneoka main Catalog orchestration; see ../L2-2.md.
# Effects/Dependencies: Bounded GET, atomic independent cache; never retries another source.

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
from .transport import ORIGIN, public_get
from .identity import resource_identity as identity, check_headers

SOURCE = ORIGIN + "/api/v1/servers/jp/"
ENTITIES = {"songs": "musicId", "cards": "cardId", "support-cards": "supportCardId",
            "characters": "characterId", "bands": "bandId"}
MAX_BYTES = 24_000_000
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
    info = convert_info(saved["documents"], saved["identities"]["jp"], stamp)
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
        self.lock = threading.RLock()
        self.state = "unknown"

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
                if len(raw) > 8_000_000 or total > MAX_BYTES:
                    raise ValueError("Catalog download budget")
            value = decode_json(raw)
            check_headers(headers, ident or identity(value, server))
            return value

        for server in ("jp", "intl"):
            ident = identities[server] = identity(get(server, "release"), server)
            catalog = get(server, "catalog", ident)
            if catalog.get("server") != server or catalog.get("sourceId") != ident["sourceId"]:
                raise ValueError("Catalog identity mismatch")

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
            info = convert_info(self.saved["documents"], self.saved["identities"]["jp"], metadata["cached_at"])
            return records, metadata, info

    def load(self, refresh=False):
        with self.lock:
            if self.saved is None:
                try:
                    self.saved = self._read()
                except ERRORS:
                    pass
            now = self.clock()
            if not refresh and self.saved and 0 <= now - self.saved["fetched_at"] < self.ttl:
                result = validate(self.saved, now, stale=self.state == "stale")
                if self.state not in {"unsaved", "stale"}:
                    self.state = "cached"
                return result
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
                return validate(self.saved, self.clock(), stale=True)
            self.state = "fresh"
            try:
                self._write(candidate)
            except ERRORS:
                self.state = "unsaved"
            return result
