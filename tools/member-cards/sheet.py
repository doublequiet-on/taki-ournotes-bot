"""Build a detail sheet from a reviewed card receipt; stdout is the renderer protocol."""
from html import escape
from pathlib import Path
from urllib.parse import quote
import json
import re
import sys

from seats import ruler_row
from skills import original_effects, relation_name, sections, skill_effects, skill_name, title_text

GEAR = {1: "mic", 2: "ultratone", 3: "potbelly", 4: "bass", 5: "snare",
        11: "arale", 12: "nonoka", 13: "ritsu", 14: "miyako", 15: "yuno"}


def media(name):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.(?:webp|png|jpe?g)", name):
        raise ValueError(f"Expected a media file name, got {name!r}")
    return "/media/" + quote(name)


def img(cls, name, alt=""):
    return f'<img class="{cls}" src="{media(name)}" alt="{escape(alt)}">'


def colour(value):
    if not re.fullmatch(r"#[a-fA-F0-9]{6}", value):
        raise ValueError(f"Invalid colour {value!r}")
    return value


def related(card):
    groups = []
    for kind, label in (("events", "关联活动"), ("gachas", "关联卡池")):
        items = card["relations"][kind]
        if not items:
            continue
        event = kind == "events"
        group_bonus = event and all(r.get("role") == "加成" for r in items)
        if group_bonus:
            label = "加成活动"
        entries = []
        for rec in items:
            serial = f'<span class="relation-id">ID {int(rec["id"])}</span>'
            title = f'<span class="relation-name">{relation_name(rec["name"], not event)}</span>'
            mew = card["bandId"] == 3
            caption = '<div class="relation-caption">' + (title + serial if mew else serial + title) + '</div>'
            banner = img("relation-banner", rec["banner"])
            if event:
                banner = f'<span class="clipping">{banner}</span>'
            pieces = [caption, banner] if mew else [banner, caption]
            roles = []
            if rec.get("isReward"):
                roles.append(rec.get("rewardLabel", "活动奖励"))
            if rec.get("role") and not group_bonus and rec["role"] not in roles:
                roles.append(rec["role"])
            if roles:
                pieces.append(f'<p class="event-role">{escape("、".join(roles))}</p>')
            targets = []
            for key, target_label in (("attributes","属性"), ("bands","乐队")):
                icons = ''.join(img("", item["image"], item["name"]) for item in rec.get("targets", {}).get(key, []))
                if icons:
                    targets.append(f'<span class="target-group"><span>{target_label}</span>{icons}</span>')
            if targets:
                pieces.append('<div class="event-targets">' + ''.join(targets) + '</div>')
            bonus = rec.get("cardBonuses")
            if bonus:
                scope = '<wbr>'.join(f'<span class="bonus-scope-part">{escape(part)}{sep}</span>'
                                     for part, sep in re.findall(r"([^、]+)(、?)", bonus["scope"]))
                rows = []
                for row in bonus["rows"]:
                    if len(row["values"]) != 5:
                        raise ValueError("Expected five awakening bonuses")
                    css = ' class="included"' if row["name"] == "其中专属" else ''
                    cells = ''.join(f'<td>+{escape(str(v))}%</td>' for v in row["values"])
                    rows.append(f'<tr{css}><th scope="row">{escape(row["name"])}</th>{cells}</tr>')
                ranks = ''.join(f'<th scope="col">{n}</th>' for n in range(5))
                pieces.append(f'<table class="bonus-table"><caption>{scope}</caption><thead><tr><th scope="col">觉醒次数</th>{ranks}</tr></thead><tbody>{"".join(rows)}</tbody></table>')
            entries.append('<dd>' + ''.join(pieces) + '</dd>')
        groups.append(f'<div class="{kind}"><dt>{label}</dt>{"".join(entries)}</div>')
    return ''.join(groups)


def numbers(c):
    stats = c["stats"]
    for s in stats:
        colour(s["color"])
    mygo = c["bandId"] == 1
    peak = max(s["max"] for s in stats)
    if mygo:
        rows = ''.join(ruler_row(s, peak, c["maxLevel"]) for s in stats)
        heads = f'<th aria-label="Lv.1"></th><th aria-label="Lv.{c["maxLevel"]}"></th>'
        prefix = '<table class="stats ruled"><colgroup><col><col><col></colgroup>'
    else:
        rows = ''.join(f'<tr><th><i class="dot" style="--stat:{s["color"]}"></i>{escape(s["label"])}</th>'
                       f'<td>{s["initial"]:,}</td><td>{s["max"]:,}</td></tr>' for s in stats)
        heads = f'<th>Lv.1</th><th>Lv.{c["maxLevel"]}</th>'
        prefix = '<table class="stats">'
    totals = [sum(s[key] for s in stats) for key in ("initial", "max")]
    table = f'{prefix}<thead><tr><th>等级基础值</th>{heads}</tr></thead><tbody>{rows}</tbody><tfoot><tr><th>综合力</th><td>{totals[0]:,}</td><td class="total">{totals[1]:,}</td></tr></tfoot></table>'
    gains = ''
    if mygo:
        g = c["cultivation"]
        gains = (f'<div class="cultivation"><div class="growth-bonuses"><span>特训 {g["trainingStage"]-1} 次 <b>+{g["trainingBonus"]:,}</b></span>'
                 f'<span>觉醒 {g["awakeningStage"]-1} 次 <b>+{g["awakeningBonus"]:,}</b></span></div><div class="growth-total"><span>满培养综合力</span><strong>{g["total"]:,}</strong></div></div>')
    return '<section class="numbers">' + table + '</section>' + gains


def make(c):
    band, member = c["bandId"], c["characterId"]
    if band not in (1, 2, 3) or member not in range((band-1)*5+1, band*5+1):
        raise ValueError("Unsupported band/member pair")
    if type(c["maxLevel"]) is not int or c["maxLevel"] < 1:
        raise ValueError("Invalid maximum level")
    if [row["label"] for row in c["stats"]] != ["表演", "技巧", "形象"]:
        raise ValueError("Expected performance, technique and visual in display order")
    if band in (2, 3):
        growth = c["cultivation"]
        if growth["maxLevel"] != c["maxLevel"] or [s["key"] for s in growth["stats"]] != ["performance","technique","visual"]:
            raise ValueError("Cultivation data and card header disagree")
        for stat, row in zip(c["stats"], growth["stats"]):
            if (row["initial"],row["base"]) != (stat["initial"],stat["max"]) or row["base"] + row["trainingBonus"] + row["awakeningBonus"] != row["full"]:
                raise ValueError("Base and fully trained stats disagree")
            if band == 3:
                levels = c["levelCurve"][row["key"]]
                if len(levels) != c["maxLevel"] or (levels[0],levels[-1]) != (row["initial"],row["base"]) or any(type(v) is not int for v in levels):
                    raise ValueError("Fader curve does not match the card's level range")
    mew = band == 3
    mygo = band == 1
    art = ''.join([img("portrait", c["portrait"], c["character"]), img("corner attribute", c["attributeIcon"], c["attributeName"]),
                   img("corner band-emblem", c["emblem"], c["band"]), img("corner rarity", c["rarityIcon"], c["rarityLabel"])])
    frame = media(c["frame"])
    logo = img("band", c["logo"], c["band"])
    traits = f'<span>ID {int(c["id"])}</span><span>{escape(c["category"])}</span>'
    debut = c.get("jpDebutDate")
    if debut and band != 2:
        traits += f'<time class="debut" datetime="{escape(debut)}">实装时间 <span>{escape(debut.replace("-", "."))}</span></time>'
    name = f'<strong>{escape(c["character"])}</strong>'
    header = f'<div class="member-line">{logo}{name}</div><div class="traits">{traits}</div>'
    pattern = '<div class="corner-print" aria-hidden="true"></div>'
    if mygo:
        pattern = f'<svg class="printed-edge" aria-hidden="true" viewBox="70 50 580 1300" preserveAspectRatio="xMidYMid slice"><image href="{media("member_background1.png")}" width="1440" height="1920"/></svg>' + pattern
    footer = '<div class="plate-illustration" aria-hidden="true"></div>'
    corner = '<div class="return-print" aria-hidden="true"></div>'
    if mew:
        pattern = ''
        header = (f'<div class="masthead"><div class="identity"><div class="onair-credit"><span class="tally" lang="en">ON AIR</span>'
                  f'<div class="member-line">{name}</div></div></div><img class="band-shelf" src="/mewtype/band-shelf.svg#{GEAR[member]}" alt="成员设备"></div>'
                  f'<div class="traits">{logo}{traits}</div>')
        footer = '<div class="snack-corner"><img src="/mewtype/left-yokan-still-giant.svg" alt="巨大羊羹"></div>'
        corner = '<div class="host-corner"><img class="maru" src="/mewtype/maru-edge.svg" alt="丸君"></div>'
    articles = []
    facts = []
    for s in c["skills"]:
        if band in (2, 3):
            texts = s["sourceTexts"]
            if len(texts) != 5 or not all(texts):
                raise ValueError("Missing five-level skill text")
            groups = sections(texts)
            facts.append(dict(name=s["name"], groups=groups, texts=texts))
            effects = original_effects(dict(groups=groups))
        else:
            effects = skill_effects(s, s.get("annotation"), band_id=band, language="zh")
        icon = img("skill-icon", s["icon"])
        role = escape(s["role"])
        articles.append(f'<article data-skill-role="{role}">{icon}<div class="skillhead"><span class="kind">{role}</span><h3>{skill_name(s,c["band"])}</h3></div>{effects}</article>')
    links = related(c)
    right = f'<section class="relation-page"><dl class="relations">{links}</dl>{corner}</section>' if links else ''
    header += f'<h1>{title_text(c["title"])}</h1><p class="jp" lang="ja">{escape(c["jpTitle"])}</p>'
    artpage = f'<section class="art-page"><figure><div class="portrait-frame">{art}</div></figure>{numbers(c)}{footer}</section>'
    middle = f'<section class="lyric-page">{pattern}<header>{header}</header><h2 class="section">技能 <span class="levels-label">Lv.1 → 5</span></h2><section class="skills">{"".join(articles)}</section>{corner if mew and not right else ""}</section>'
    css = f'--band-color:{colour(c["bandColor"])};--frame:url({frame})'
    if mygo:
        css += f';--corner-art:url(/base/graphics/mygo-ensemble.svg#{GEAR[member]})'
    layout = ' class="foldsheet study-compact"' if mew else ''
    body = f'<main{layout} data-band-id="{band}" style="{css}">{artpage}{middle}{right}</main>'
    if mew:
        body = f'<div id="print"><div class="sheet-shadow">{body}</div></div>'
        styles = ["mewtype/sheet.css","mewtype/print.css", *["mewtype/paper/"+n+".css" for n in ("mewtype","feed","folds","creases")]]
    else:
        styles = ["base/sheet.css","base/paper/mygo-ave.css","base/print.css"]
        if mygo:
            if right:
                styles.append("base/paper/mygo-foldout.css")
            styles.append("base/paper/mygo-score.css")
            if right:
                styles.append("base/paper/mygo-clippings.css")
    styles.insert(0, "fonts.css")
    stylesheets = ''.join(f'<link rel="stylesheet" href="/{file}">' for file in styles)
    script = "mewtype" if mew else "base"
    html = f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">{stylesheets}</head><body>{body}<script src="/{script}/fit-folds.js"></script></body></html>'
    return dict(html=html, articles=facts)


if __name__ == "__main__":
    print(json.dumps(make(json.loads(Path(sys.argv[1]).read_bytes()))))
