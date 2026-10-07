# L3
# Input: 筛选文本、SongRepository、成员／SNAP 模式、卡牌 ID 与页码。
# Output: CardRequest 与 CardAnswer；cards 保存完整选择集合，visible 提供当前页，text 输出列表／详情／原卡面说明或错误。
# Pos: Query / Deterministic 的成员卡与 SNAP 确定性筛选及展示模式选择；见 L2-2.md。
# Effects/Dependencies: 实体解析可读别名 JSON；ID／详情路径可经仓库补全详情并联网，不调用模型或绘图。

"""Platform independent card catalog: validated filters, complete sets and output modes."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from ..data import Card, SupportCard, localized_text, normalize, character_identity
from .entity_lexicon import resolve_exact_alias

RARITIES = {"R": 2, "SR": 3, "SSR": 4, "EX": 10, "BD": 20}
# Public /images/CardType{attribute}.webp, inspected against the five actual icons.
# These are Chinese colour aliases, not invented official attribute names.
TYPES = {1: "红色（日轮）", 2: "蓝色（星球）", 3: "绿色（流星）", 4: "黄色（闪光）", 5: "紫色（月亮）"}
LIVE = {"score": "分数提升", "life": "LIFE回复", "judgement": "判定强化", "duration": "技能延长"}
PAGE_SIZE = 16


def rarity_name(card):
    names = {2: "R", 3: "SR", 4: "SSR"}
    if isinstance(card, SupportCard):
        names[10] = "EX"
    else:
        names[20] = "BD"
    return names.get(card.rarity, f"未知稀有度({card.rarity})")


def labels(card):
    cats = card.catalog.get("categories", {})
    def label(key):
        values = cats.get(key) or [None]
        return "/".join("不适用" if v == "not_applicable" else
                        (LIVE.get(v, "分类未确认") if key == "live" else v or "分类未确认") for v in values)
    return label("live"), label("gekisou")


@dataclass(frozen=True)
class CardRequest:
    support: bool = False
    mode: str = "list"
    card_id: int | None = None
    page: int = 1
    query: str = ""
    filters: dict[str, tuple] = field(default_factory=dict)

    @property
    def command(self):
        if self.mode == "art":
            return "/查支援卡面" if self.support else "/查卡面"
        return "/查支援卡" if self.support else "/查卡"


@dataclass(frozen=True)
class CardAnswer:
    request: CardRequest
    cards: tuple = ()
    error: str = ""
    status: str = "success"

    def __post_init__(self):
        if self.status == "success" and not self.cards:
            object.__setattr__(self, "status", "invalid_arguments" if self.error else "empty")

    @property
    def visible(self):
        start = (self.request.page - 1) * PAGE_SIZE
        return self.cards[start:start + PAGE_SIZE] if self.request.page > 0 else ()

    @property
    def footer(self):
        req, total = self.request, len(self.cards)
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        if not 1 <= req.page <= pages:
            return f"页码超出范围，共 {pages} 页。发送 {req.command} {req.query} 页1"
        start = (req.page - 1) * PAGE_SIZE + 1 if total else 0
        text = f"共 {total} 张 · 第 {req.page}/{pages} 页 · 本页 {start}–{min(req.page * PAGE_SIZE, total)}"
        if req.page < pages:
            text += f"\n下一页：{req.command} {req.query} 页{req.page + 1}"
        if any(c.catalog.get("detail_stale") for c in self.cards):
            text += "\n部分详情使用上次有效缓存。"
        return text

    def text(self, locale="zh"):
        if self.error:
            return self.error
        req = self.request
        title = "SNAP（支援卡）" if req.support else "角色卡"
        if not self.cards:
            return f"{title}：未找到符合条件的卡牌。条件：{req.query or '不限'}（未放宽筛选）"
        if req.mode == "detail":
            return detail_text(self.cards[0], locale)
        if req.mode == "art":
            return f"{title} ID {self.cards[0].id} · 卡面（公开 full 版本）"
        lines = [f"{title}列表 · 条件：{req.query or '不限'}"]
        for index, card in enumerate(self.visible, 1):
            live, gek = labels(card)
            lines.append(f"{index:02d}. ID {card.id} · {rarity_name(card)} · {TYPES.get(card.card_type, '未知类型')} · "
                         f"{localized_text(card, 'character', locale)} · {localized_text(card, 'title', locale)}\n  LIVE {live} / 击奏 {gek}")
        lines.append(self.footer)
        if any(not c.catalog.get("detail_loaded") for c in self.cards):
            lines.append("部分技能资料未获取，未知分类不会当作无技能。")
        lines.append("按稀有度降序、ID升序；查询详情请指定ID。")
        return "\n".join(lines)


def _entity(value, dimension, repository):
    found = set()
    for c in repository.cards:
        for kind, field_name in (("character", "character"), ("band", "band")):
            names = [getattr(c, field_name), *c.localized.get(field_name, {}).values()]
            if (dimension is None or kind == dimension) and normalize(value) in {normalize(n) for n in names}:
                found.add((kind, getattr(c, field_name)))
    for c in repository.support_cards:
        if dimension in {None, "character"}:
            found.update(("character", n) for n in c.characters if normalize(n) == normalize(value))
        if dimension in {None, "band"}:
            found.update(("band", n) for n in c.catalog.get("bands", []) if normalize(n) == normalize(value))
    if len(found) == 1:
        return next(iter(found))
    if len(found) > 1:
        raise ValueError(f"名称“{value}”有歧义，请显式指定角色=或乐队=。")
    match = resolve_exact_alias("card", value, repository)
    if match.ambiguous:
        raise ValueError(f"名称“{value}”有歧义，请使用完整角色名或乐队名。")
    if match.entity and match.entity.kind in {"character", "band"}:
        if dimension and match.entity.kind != dimension:
            raise ValueError(f"“{value}”不是{dimension}条件。")
        return match.entity.kind, str(match.entity.value)
    # Built-in civil/stage-name equivalence is also used by old commands.
    from ..data import resolve_character_alias
    alias = resolve_character_alias(value)
    if alias and dimension != "band":
        return "character", alias.display
    raise ValueError(f"无法识别筛选值“{value}”；请使用已收录的角色/乐队简称或完整名称。")


def parse_card_request(query, repository, *, support=False, art=False):
    from ..commands import _split_page, split_card_rarity
    query, page = _split_page(unicodedata.normalize("NFKC", query).strip())
    if len(query) > 512:
        raise ValueError("筛选条件过长，请精简到512字以内。")
    if page < 1:
        raise ValueError("页码必须从1开始。")
    ident = re.fullmatch(r"(?:ID\s*[:=]?\s*)?(\d+)", query, re.I)
    if ident:
        return CardRequest(support, "art" if art else "detail", int(ident[1]), page, query)
    if art:
        raise ValueError("只输出卡面需要明确卡牌ID；请先用 /查卡 或 /查支援卡 按条件查列表。")
    filters = {}
    explicit_id = None
    def add(key, value):
        filters.setdefault(key, []).append(value)
    keys = {"稀有度": "rarity", "星级": "rarity", "rarity": "rarity", "颜色": "type", "类型": "type", "type": "type",
            "乐队": "band", "band": "band", "角色": "character", "character": "character",
            "live": "live", "击奏": "gekisou", "激奏": "gekisou", "gekisou": "gekisou",
            "得意": "tags", "得意乐曲": "tags", "tags": "tags"}
    fields = list(re.finditer(r"([^\s=：:]+)\s*[=：:]\s*", query))
    bare = query[:fields[0].start()].strip() if fields else query
    # Preserve catalog names containing spaces when combined with bare rarity aliases.
    names = {n for c in (*repository.cards, *repository.support_cards)
             for n in (c.title, c.character, getattr(c, "band", ""),
                       *(v for group in c.localized.values() for v in group.values())) if " " in n}
    chunks, remaining = [], bare
    while remaining:
        matched = next((n for n in sorted(names, key=len, reverse=True)
                        if re.match(re.escape(n) + r"(?=\s|$)", remaining, re.I)), None)
        if matched:
            chunks.append((None, remaining[:len(matched)]))
            remaining = remaining[len(matched):].strip()
        else:
            parts = remaining.split(None, 1)
            chunks.append((None, parts[0]))
            remaining = parts[1] if len(parts) > 1 else ""
    if bare:
        try:
            _entity(bare, None, repository)
            chunks = [(None, bare)]
        except ValueError:
            source = repository.support_cards if support else repository.cards
            if any(normalize(bare) in {normalize(c.title), *(normalize(n) for n in c.localized.get("title", {}).values())} for c in source):
                chunks = [(None, bare)]
    for i, match in enumerate(fields):
        key = keys.get(match[1].lower())
        if not key:
            raise ValueError(f"未知筛选维度“{match[1]}”；可用：稀有度、颜色、乐队、角色、LIVE、击奏、得意。")
        value = query[match.end():fields[i + 1].start() if i + 1 < len(fields) else len(query)].strip()
        if not value:
            raise ValueError(f"{match[1]}缺少筛选值。")
        chunks.append((key, value))
    for key, values in chunks:
        for value in re.split(r"[,，、|/]|或", values):
            value = value.strip()
            if value in {"全部", "不限", "すべて"}:
                continue
            upper = value.upper()
            if key == "rarity" or (key is None and (upper in {*RARITIES, "BD"} or re.search(r"星|[★☆]|-?stars?$", value, re.I))):
                if key == "rarity" and value in {"2", "3", "4"}:
                    rarity = int(value)
                elif upper in RARITIES:
                    rarity = RARITIES[upper]
                else:
                    try:
                        rest, rarity = split_card_rarity(value)
                    except ValueError:
                        raise ValueError("请指定 SSR/SR/R（兼容四/三/二星旧别名）；不支持稀有度范围。") from None
                    if rest or rarity is None:
                        raise ValueError("未知稀有度；角色卡可用 BD/SSR/SR/R，SNAP 可用 EX/SSR/SR/R。")
                if rarity == 10 and not support:
                    raise ValueError("EX 仅在 SNAP 数据中已核实，角色卡不支持 EX 筛选。")
                if rarity == 20 and support:
                    raise ValueError("BD 仅在角色卡数据中已核实，SNAP 不支持 BD 筛选。")
                add("rarity", rarity)
            elif key == "type" or (key is None and (re.fullmatch(r"[红蓝绿黄紫]色?", value) or (value.endswith("色") and not value.endswith("角色")))):
                color = {"红": 1, "蓝": 2, "绿": 3, "黄": 4, "紫": 5}.get(value.removesuffix("色"))
                if color is None:
                    raise ValueError("未知颜色；可用红色、蓝色、绿色、黄色、紫色。正式属性名称尚未核实。")
                add("type", color)
            elif key == "live":
                category = {"分数提升": "score", "分数up": "score", "スコアアップ": "score", "スコアup": "score",
                            "life回复": "life", "life回復": "life", "life恢复": "life", "回复": "life",
                            "判定强化": "judgement", "判定強化": "judgement", "技能延长": "duration"}.get(value.lower())
                if not category:
                    raise ValueError("未知LIVE分类；可用分数提升、LIFE回复、判定强化、技能延长（SNAP）。")
                if category == "duration" and not support:
                    raise ValueError("技能延长目前仅在 SNAP 的 LIVE 支援中核实。")
                add(key, category)
            elif key == "gekisou" or (key is None and upper in {"JUST", "COMBO", "LUCK"}):
                if upper not in {"JUST", "COMBO", "LUCK"}:
                    raise ValueError("未知击奏分类；可用 JUST、COMBO、LUCK。")
                add("gekisou", upper)
            elif key == "tags":
                if support:
                    raise ValueError("SNAP 数据没有已核实的得意乐曲字段。")
                available = {k: names for c in repository.cards for k, names in c.catalog.get("tags", {}).items()}
                found = [k for k, names in available.items() if normalize(value) in {normalize(n) for n in names.values()}]
                if not found:
                    _, band = _entity(value, "band", repository)
                    found = [k for k, names in available.items() if normalize(band) in {normalize(n) for n in names.values()}]
                if len(found) != 1:
                    raise ValueError("得意乐曲标签不存在或映射不完整；未扩大筛选范围。")
                add(key, found[0])
            else:
                if key is None and value.isdecimal():
                    if explicit_id is not None:
                        raise ValueError("详情查询只能指定一个卡牌ID。")
                    explicit_id = int(value)
                    add("title_ids", int(value))
                    continue
                try:
                    dimension, resolved = _entity(value, key, repository)
                    add(dimension, resolved)
                except ValueError:
                    # A complete exact card title is a list condition, never an implicit ID.
                    source = repository.support_cards if support else repository.cards
                    exact = [c.id for c in source if normalize(value) in {normalize(c.title), *(normalize(n) for n in c.localized.get("title", {}).values())}]
                    alias = resolve_exact_alias("support_card" if support else "card", value, repository)
                    if not alias.ambiguous and alias.entity and alias.entity.kind == ("support_card" if support else "card"):
                        exact = [c.id for c in source if c.id == alias.entity.value]
                    if key is not None or not exact:
                        raise
                    for card_id in exact:
                        add("title_ids", card_id)
    return CardRequest(support, mode="detail" if explicit_id is not None else "list", page=page,
                       query=query, filters={k: tuple(dict.fromkeys(v)) for k, v in filters.items()})


def execute_card_request(req, repository):
    cards = repository.support_cards if req.support else repository.cards
    if req.card_id is not None:
        matches = [c for c in cards if c.id == req.card_id]
        if len(matches) > 1:
            return CardAnswer(req, error="卡牌 ID 映射冲突，请等待数据核对。", status="ambiguous")
        if not matches:
            return CardAnswer(req, error=f"未找到{'SNAP' if req.support else '角色卡'} ID {req.card_id}，没有改查其他对象。", status="unknown_entity")
        if req.mode == "art":
            return CardAnswer(req, (matches[0],))
        detail = repository.support_card_with_detail if req.support else repository.card_with_detail
        return CardAnswer(req, (detail(matches[0]),))
    selected = []
    missing = set()
    for card in {c.id: c for c in cards}.values():
        keep = True
        card_missing = set()
        for key, values in req.filters.items():
            unknown = False
            if key == "rarity":
                match = card.rarity in values
            elif key == "type":
                match = card.card_type in values
            elif key == "title_ids":
                match = card.id in values
            elif key == "character":
                if card.catalog.get("character_links_complete") is False:
                    card_missing.add("角色关联")
                    unknown = True
                names = card.characters if isinstance(card, SupportCard) else (card.character,)
                match = bool({normalize(character_identity(n)) for n in names} & {normalize(character_identity(n)) for n in values})
            elif key == "band":
                bands = card.catalog.get("bands", []) if isinstance(card, SupportCard) else [card.band]
                if not bands or "未知乐队" in bands or card.catalog.get("character_links_complete") is False:
                    card_missing.add("乐队关联")
                    unknown = True
                match = bool({normalize(n) for n in bands} & {normalize(n) for n in values})
            elif key == "tags":
                if "tags" not in card.catalog or card.catalog.get("tags_complete") is False:
                    card_missing.add("得意乐曲")
                    unknown = True
                match = set(values) <= set(card.catalog.get("tags", {}))
            else:
                actual = card.catalog.get("categories", {}).get(key, [])
                if not actual or None in actual:
                    card_missing.add("LIVE/击奏分类")
                    unknown = True
                match = bool(set(actual) & set(values))
            # Unknown is not a proven mismatch; evaluate every dimension before
            # deciding whether this card could still belong to the result.
            if not match and not unknown:
                keep = False
        if keep:
            if card_missing:
                missing.update(card_missing)
            else:
                selected.append(card)
    if missing:
        return CardAnswer(req, error="卡牌索引不完整（" + "、".join(sorted(missing)) + "），不能保证完整筛选；请等待数据同步后重试，未将未知分类当作无技能。", status="data_unavailable")
    if req.mode == "detail" and selected:
        detail = repository.support_card_with_detail if req.support else repository.card_with_detail
        selected = [detail(selected[0])]
    return CardAnswer(req, tuple(sorted(selected, key=lambda c: (-c.rarity, c.id))))


def query_cards(query, repository, *, support=False, art=False):
    try:
        req = parse_card_request(query, repository, support=support, art=art)
        return execute_card_request(req, repository)
    except ValueError as exc:
        return CardAnswer(CardRequest(support, query=query), error=str(exc))


def detail_text(card, locale="zh"):
    support = isinstance(card, SupportCard)
    live, gek = labels(card)
    bands = card.catalog.get("bands", []) if support else [localized_text(card, "band", locale)]
    lines = [f"{'SNAP 支援卡详情' if support else '成员卡详情'} · ID {card.id}",
             localized_text(card, "title", locale), localized_text(card, "character", locale),
             "乐队：" + (" / ".join(bands) or "关联未获取"),
             f"{rarity_name(card)} · {TYPES.get(card.card_type, f'未知类型({card.card_type})')}",
             f"LIVE{'支援' if support else '技能'}：{live} · 击奏{'支援' if support else '技能'}：{gek}"]
    stats = card.catalog.get("stats_level1")
    state = "Lv.1 / Rank 1" + ("" if support else " / 觉醒0次")
    if stats is not None:
        unit = "%" if support else ""
        lines.append(f"资料展示状态：{state}（非玩家培养状态）")
        lines.append(" / ".join(f"{name} {value:g}{unit}" for name, value in zip(("演出", "技巧", "表现"), stats)))
    else:
        lines.append("数值状态未核实，暂不展示培养数值。")
    if not support:
        tags = card.catalog.get("tags")
        lines.append("得意乐曲：" + ("映射未确认" if card.catalog.get("tags_complete") is False else
                                    " / ".join(n.get(locale) or n.get("ja") or k for k, n in tags.items()) if tags else "标签未获取"))
    names = {"leaderSkill": "队长技能", "liveSkill": "LIVE技能", "gekisouSkill": "击奏技能",
             "supportSkill": "LIVE支援", "gekisouSupportSkill": "击奏支援"}
    for skill in card.skills:
        lang = card.catalog.get("skill_languages", {}).get(skill.kind, "未知")
        lines += [f"{names.get(skill.kind, skill.kind)} · {localized_text(skill, 'name', locale)}",
                  "效果（Lv.5；来源语言 " + lang + "）：" + (localized_text(skill, "description", locale) or "详情未获取")]
    if not card.skills:
        lines.append("技能详情暂不可用；不代表无技能。")
    source = "Haneoka JP" if card.catalog.get("source") == "haneoka" else "Project Yume"
    lines.append(source + " 公开资料 · 卡面为公开 full 版本")
    if card.catalog.get("detail_stale"):
        lines.append("详情使用上次有效缓存。")
    return "\n".join(lines)
