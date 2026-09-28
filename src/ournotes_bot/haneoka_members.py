"""Public JP Haneoka skill data, isolated to member-list summaries.

No upstream code is copied. Verified effect contracts fail closed when their
semantics change; only effectValue is variable within a contract. Source Lv.5
is selected explicitly, never a player's current skill level.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, replace
from functools import lru_cache
import hashlib
import json
import math
import os
import re
import tempfile
import threading
import time
from urllib.request import Request, urlopen
from http.client import HTTPException

from .config import runtime_data_dir
from .song_meta import unique_object

BASE = "https://haneoka.org/api/v1/servers/jp/"
SOURCE = "https://haneoka.org/jp/zh-CN/member-cards/"
RESOURCES = ("cards", "skills", "gekisou-skills", "skill-reference", "leader-skills")
UNKNOWN = ("摘要未确认", "请按ID查看完整技能")


def fetch_json(path):
    if not re.fullmatch(r"release\?projection=identity|(?:cards|skills|gekisou-skills|skill-reference|leader-skills)\?release=r-[\w-]+", path):
        raise ValueError("unexpected member resource")
    with urlopen(Request(BASE + path, headers={"Accept": "application/json", "User-Agent": "Taki-member-list/1"}), timeout=5) as response:
        if "application/json" not in response.headers.get("Content-Type", ""):
            raise ValueError("non-JSON member resource")
        raw = response.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("oversized member resource")
    return json.loads(raw, object_pairs_hook=unique_object)


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("invalid skill number")
    return value


def fmt(value):
    return f"{number(value):g}"


def reference_index(reference):
    result = {}
    for name, key in (("conditions", "_id"), ("cumulativeConditions", "_id"), ("conditionSets", "_id")):
        rows = reference[name]
        if not isinstance(rows, list) or not rows:
            raise ValueError("missing skill references")
        result[name] = {}
        for item in rows:
            row = item["raw"]
            if row[key] in result[name]:
                raise ValueError("duplicate skill reference")
            result[name][row[key]] = row
    result["targets"] = {}
    result["target_names"] = {}
    for item in reference.get("targets", []):
        row = item["raw"]
        if row["_id"] in result["targets"]:
            raise ValueError("duplicate target")
        result["targets"][row["_id"]] = row
        result["target_names"][row["_id"]] = (item.get("name") or [""])[0]
    return result


def conditions(index, group):
    if not group:
        return []
    sets = [r for r in index["conditionSets"].values() if r["_group"] == group]
    if not sets:
        raise ValueError("missing condition group")
    return [[index["conditions"][i] for i in r["_conditionIds"]] for r in sets]


def contract(skill, index):
    rows = [e for e in skill["effects"] if e["level"] == 5]
    if not rows or len({e["effectId"] for e in rows}) != len(rows):
        raise ValueError("missing/duplicate level 5 effects")
    payload = []
    for e in rows:
        number(e["effectValue"])
        if e.get("raw", {}).get("_effectValue", e["effectValue"]) != e["effectValue"]:
            raise ValueError("conflicting effect magnitude")
        shape = {k: v for k, v in e.items() if k not in {"effectValue", "effectId", "raw", "sourceTable"}}
        # Include all raw mechanics too, except the identity and varying magnitude.
        shape["raw"] = {k: v for k, v in e.get("raw", {}).items()
                        if k not in {"_id", "_effectValue", "_liveSkillID", "_gekisouSkillID", "_leaderSkillID"}}
        if "_leaderSkillID" in e.get("raw", {}):
            shape["resolved_targets"] = [index["targets"][i] for i in e["targetIds"]]
        for field in ("conditionGroup", "triggerConditionGroup", "releaseConditionGroup", "executeLimitResetConditionGroup"):
            shape[field] = conditions(index, e.get(field))
        shape["cumulative"] = index["cumulativeConditions"].get(e.get("cumulativeConditionId"))
        if e.get("cumulativeConditionId") and not shape["cumulative"]:
            raise ValueError("missing cumulative condition")
        payload.append(shape)
    signature = hashlib.sha256(json.dumps([skill["description"][0], payload], sort_keys=True,
                                         ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    return signature, rows


def summarize_skill(skill, index):
    from .haneoka_member_contracts import PROFILES
    signature, rows = contract(skill, index)
    mode = PROFILES.get(signature)
    if mode is None:
        return UNKNOWN
    values = [number(row["effectValue"]) for row in rows]
    v = [fmt(n) for n in values]
    seconds = fmt(rows[0]["activationTimeSecond"])
    if mode == "live_plain":
        return f"得分 +{fmt(values[0]/100)}%", f"{seconds}秒 · 无额外条件"
    if mode == "live_perfect":
        return f"得分 +{fmt(values[0]/100)}%", f"{seconds}秒 · PERFECT以上"
    if mode == "live_life":
        return f"得分 +{fmt(values[0]/100)}→{fmt(values[1]/100)}%", f"{seconds}秒 · LIFE≥700取后值"
    if mode in {"just", "combo", "gauge"}:
        name = {"just": "JUST", "combo": "COMBO", "gauge": "LUCK"}[mode]
        text = f"LUCK条增量 +{fmt(values[0]/100)}%" if mode == "gauge" else f"{name} +{v[0]}"
        return text, f"{name}激奏期间" if seconds == "0" else f"{name}激奏开始后{seconds}秒"
    if mode == "points":
        return f"LUCK点数 +{v[0]}pt", "触发LUCKY RUSH时"
    if mode == "stack":
        cumulative = index["cumulativeConditions"][rows[0]["cumulativeConditionId"]]
        return f"JUST 每{fmt(cumulative['_conditionValues'][0])}次 +{v[0]}", f"JUST激奏 · 最多{fmt(cumulative['_maxCumulativeCount'])}次"
    if mode == "combo_threshold":
        threshold = conditions(index, rows[0]["triggerConditionGroup"])[0][0]["_conditionValues"][0]
        return f"COMBO +{v[0]}", f"保持激奏COMBO≥{fmt(threshold)}"
    if mode == "just_life":
        return f"JUST +{v[0]}→{v[1]}", "第1次起 · LIFE≥700取后值"
    if mode == "combo_life":
        return f"COMBO +{v[0]}→{v[1]}", "不断连 · LIFE≥700取后值"
    if mode == "probability":
        if values[0] != values[1]:
            return UNKNOWN
        probabilities = [conditions(index, e["conditionGroup"])[0][1]["_conditionValues"][0] for e in rows]
        return f"LUCK起始条{fmt(values[0]/100)}%", f"概率{fmt(probabilities[0])}→{fmt(probabilities[1])}% · LIFE≥700"
    return UNKNOWN


def summarize_leader(skill, index):
    from .haneoka_member_contracts import LEADER_PROFILES
    signature, rows = contract(skill, index)
    if signature not in LEADER_PROFILES:
        return UNKNOWN
    attribute = {1000: "全属性", 1001: "技巧值", 1002: "表现值", 1003: "表演值"}[rows[0]["effectType"]]
    band = index["target_names"][rows[0]["targetIds"][0]]
    if not band:
        return UNKNOWN
    primary = f"{attribute} +{fmt(rows[0]['effectValue']/100)}%"
    qualifier = f"{band}成员"
    if len(rows) == 2:
        target = index["targets"][rows[1]["targetIds"][0]]
        category = {1: "简单", 2: "生命值", 3: "判定"}[target["_liveSkillCategories"][0]]
        qualifier += f"\n演出【{category}】成员另+{fmt(rows[1]['effectValue']/100)}%"
    return primary, qualifier


@dataclass(frozen=True)
class Snapshot:
    cards: dict
    summaries: dict
    release: str
    fetched_at: float
    stale: bool = False
    leader_ready: bool = True

    def for_card(self, card):
        row = self.cards.get(str(card.id))
        evidence = {e["kind"]: e["id"] for e in card.catalog.get("skill_evidence", [])}
        if (not row or (row["cardId"], row["assetId"], row["rarity"], row["cardType"]) !=
                (card.id, card.asset_id, card.rarity, card.card_type) or
                card.catalog.get("character_ids") != [row["characterId"]] or
                evidence.get("liveSkill") != row["liveSkillId"] or
                evidence.get("gekisouSkill") != row["gekisouSkillId"]):
            return (("映射未确认", "未合并其他来源数值"),) * 3
        return self.summaries[str(card.id)]


def parse(saved):
    identity = saved["identity"]
    if (saved.get("schema") != 1 or saved.get("source") != SOURCE or identity.get("server") != "jp" or
            identity.get("schema") != "haneoka-resource-release-identity-v1" or not identity.get("sourceId") or
            not re.fullmatch(r"r-[\w-]+", identity.get("releaseId", ""))):
        raise ValueError("member cache/source identity")
    docs = saved["documents"]
    if not all(isinstance(docs.get(key), dict) and docs[key] for key in RESOURCES if key != "leader-skills"):
        raise ValueError("incomplete member data")
    leader_ready = "leader-skills" in docs
    if leader_ready and (not isinstance(docs["leader-skills"], dict) or not docs["leader-skills"]):
        raise ValueError("incomplete leader data")
    index = reference_index(docs["skill-reference"])
    summaries = {}
    for key, card in docs["cards"].items():
        if key != str(card["cardId"]) or any(type(card.get(k)) is not int for k in
                ("cardId", "assetId", "characterId", "rarity", "cardType", "liveSkillId", "gekisouSkillId")):
            raise ValueError("member identity")
        pair = [UNKNOWN]
        if leader_ready:
            if type(card.get("leaderSkillId")) is not int:
                raise ValueError("leader identity")
            leader = docs["leader-skills"][str(card["leaderSkillId"])]
            if leader["id"] != card["leaderSkillId"]:
                raise ValueError("leader identity")
            pair[0] = summarize_leader(leader, index)
        for resource, field in (("skills", "liveSkillId"), ("gekisou-skills", "gekisouSkillId")):
            skill = docs[resource][str(card[field])]
            if skill["id"] != card[field]:
                raise ValueError("skill identity")
            pair.append(summarize_skill(skill, index))
        summaries[key] = tuple(pair)
    return Snapshot(deepcopy(docs["cards"]), summaries, identity["releaseId"], number(saved["fetched_at"]), leader_ready=leader_ready)


class MemberRepository:
    def __init__(self, path, fetch=fetch_json, clock=time.time):
        self.path, self.fetch, self.clock = path, fetch, clock
        self.lock, self.snapshot, self.loaded, self.retry = threading.Lock(), None, False, 0

    def get(self):
        with self.lock:
            if not self.loaded:
                self.loaded = True
                try:
                    self.snapshot = parse(json.loads(self.path.read_text(encoding="utf-8"), object_pairs_hook=unique_object))
                except (OSError, ValueError, TypeError, KeyError, IndexError):
                    pass
            now = self.clock()
            if self.snapshot and self.snapshot.leader_ready and not self.snapshot.stale and 0 <= now - self.snapshot.fetched_at < 86400:
                return self.snapshot
            if now < self.retry:
                return self.snapshot
            self.retry = now + 300
            try:
                identity = self.fetch("release?projection=identity")
                release = identity["releaseId"]
                if not isinstance(release, str) or not re.fullmatch(r"r-[\w-]+", release):
                    raise ValueError("member release")
                with ThreadPoolExecutor(max_workers=4) as pool:
                    jobs = {r: pool.submit(self.fetch, f"{r}?release={release}") for r in RESOURCES}
                    docs = {r: job.result() for r, job in jobs.items()}
                saved = dict(schema=1, source=SOURCE, identity=identity, documents=docs, fetched_at=now)
                snapshot = parse(saved)
                if self.snapshot and not self.snapshot.cards.keys() <= snapshot.cards.keys():
                    raise ValueError("incomplete refresh: existing cards missing")
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                                     prefix=self.path.name, suffix=".tmp", delete=False) as file:
                        temporary = file.name
                        json.dump(saved, file, ensure_ascii=False, allow_nan=False)
                    os.replace(temporary, self.path)
                finally:
                    if temporary and os.path.exists(temporary):
                        os.unlink(temporary)
                self.snapshot = snapshot
            except (OSError, HTTPException, ValueError, TypeError, KeyError, IndexError):
                if self.snapshot:
                    self.snapshot = replace(self.snapshot, stale=True)
            return self.snapshot


@lru_cache(maxsize=1)
def _repository():
    return MemberRepository(runtime_data_dir() / "haneoka-member-list-jp.json")


def get_snapshot():
    return _repository().get()
