from decimal import Decimal, InvalidOperation
from html import escape
from itertools import groupby
import re
from unicodedata import normalize

def signed_text(v):
    text=escape(v)
    text=text.replace('。达到 ', '。<br>达到 ')
    units=(r'LUCKY RUSH|(?:获得 )?LUCK pt|激奏 COMBO|LIFE [≥≤] [0-9]+|LIFE 门槛|PERFECT|GOOD|MyGO!!!!!'
           r'|(?:改为|提升|获得量) [+-]?[0-9]+(?:%| ?pt)?|得分提升|提升至|得分加成|[0-9]+(?: ?秒|%| ?pt)')
    return re.sub(units,r'<span class="keep">\g<0></span>',text)

NOTE_COPY = {
    "zh": {
        "leader": "每编入 1 名非{color}属性成员，全体成员{stat}值额外提升 {step}%。",
        "count": "最多 {n} 名", "cap": "上限 {n}%", "sep": "，", "end": "。",
        "colors": ("红赤", "绀碧", "翡翠", "琉金", "紫苑"), "stats": ("技巧", "形象", "表演"),
        "normal": "通常", "life": "{s} 秒内得分提升", "threshold": "发动时",
        "perfect": "{s} 秒内，PERFECT 及以上得分提升", "until": ("出现 GOOD", "及以下判定前"),
    },
    "ja": {
        "leader": "{color}以外のメンバーを1人編成するごとに、全員の{stat}が{step}%アップ。",
        "count": "最大{n}人", "cap": "上限{n}%", "sep": "、", "end": "。",
        "colors": ("紅", "紺碧", "翡翠", "山吹", "紫苑"), "stats": ("テクニック", "ビジュアル", "パフォーマンス"),
        "normal": "通常", "life": "{s}秒間スコアアップ", "threshold": "発動時",
        "perfect": "{s}秒間、PERFECT以上のスコアアップ", "until": ("GOOD以下を", "出すまで"),
    },
    "en": {
        "leader": "Each non-{color} member increases the whole band's {stat} by {step}%.",
        "count": "Up to {n} members", "cap": "{n}% cap", "sep": ", ", "end": ".",
        "colors": ("red", "blue", "green", "yellow", "purple"), "stats": ("technique", "visual", "performance"),
        "normal": "Normal", "life": "Score boost for {s}s", "threshold": "At activation",
        "perfect": "Score boost on PERFECT or better for {s}s", "until": ("Until GOOD", "or lower"),
    },
}


def percent(bp):
    whole, fraction = divmod(bp, 100)
    return f"{whole}.{fraction:02}".rstrip("0") if fraction else str(whole)


def clause(x, marked_condition=None):
    values = x.get("values")
    seq = ''.join(f'<span class="level">{escape(str(v))}</span>' for v in values) if isinstance(values, list) else ''
    nums = f'<span class="sequence">{seq}</span>' if seq else ''
    label = f'<span class="duration-label">{escape(x["label"])}</span>' if x.get("label") else ''
    condition = signed_text(x.get("condition") or "")
    if x.get("breakBefore"):
        condition = condition.replace(escape(x["breakBefore"]), '<br>'+escape(x["breakBefore"]), 1)
    if marked_condition is not None:
        condition = marked_condition
    return f'<p class="effect"><span class="condition">{condition}</span>{label}{nums}</p>'


def values_match(clause, values):
    # Only compare the five numeric columns; localized descriptions never select the layout.
    displayed = clause.get("values")
    if not isinstance(values, list) or not isinstance(displayed, list) or len(displayed) != 5 or len(values) != 5:
        return False
    for text, number in zip(displayed, values):
        value = normalize("NFKC", str(text)).strip()
        if not value.endswith("%") or type(number) is not int or number < 0:
            return False
        try:
            if Decimal(value[:-1].strip()) != Decimal(number).scaleb(-2):
                return False
        except InvalidOperation:
            return False
    return True


def skill_effects(skill, note=None, *, band_id=None, language="zh"):
    clauses = skill.get("clauses") or []
    plain = '<div class="clauses">' + ''.join(clause(c) for c in clauses) + '</div>'
    copy = NOTE_COPY.get(language)
    if not copy or not isinstance(note, dict) or len(clauses) != 2 or any(c.get("label") for c in clauses):
        return plain
    kind = note.get("type")
    values = note.get("values")
    if not isinstance(values, list):
        return plain
    if kind == "memberStack" and band_id == 1 and note.get("band") == band_id:
        base = [c for c in clauses if c.get("values")]
        rest = [c for c in clauses if c.get("values") == [] and c.get("condition")]
        if (len(base) != 1 or len(rest) != 1 or len(values) != 1 or not values_match(base[0], values[0])
                or type(note.get("excluded")) is not int or not 1 <= note["excluded"] <= 5
                or type(note.get("stat")) is not int or note["stat"] not in (1001, 1002, 1003)
                or any(type(note.get(k)) is not int or note[k] <= 0 for k in ("count", "step"))):
            return plain
        count, step = note["count"], note["step"]
        body = copy["leader"].format(color=copy["colors"][note["excluded"]-1],
                                   stat=copy["stats"][note["stat"]-1001], step=percent(step))
        cap = copy["cap"].format(n=percent(count * step))
        marked = (signed_text(body) + '<br><span class="limit-note"><span class="member-limit">'
                  + escape(copy["count"].format(n=count)) + '</span>' + escape(copy["sep"])
                  + '<span class="bonus-cap">' + signed_text(cap) + '</span></span>' + copy["end"])
        return '<div class="clauses">' + ''.join(clause(c, marked if c is rest[0] else None)
                                                 for c in clauses) + '</div>'
    if kind not in ("lifeScore", "perfectScore") or type(note.get("seconds")) is not int or note["seconds"] <= 0:
        return plain
    if len(values) != 2:
        return plain
    for ordered in (clauses, clauses[::-1]):
        if all(values_match(c, row) for c, row in zip(ordered, values)):
            break
    else:
        return plain
    labels = [escape(copy["normal"])]
    if kind == "lifeScore":
        threshold = note.get("threshold")
        if type(threshold) is not int or threshold < 0:
            return plain
        labels.append(f'{escape(copy["threshold"])}<br><span class="keep">LIFE ≥ {threshold}</span>')
        rows = []
        for label, c in zip(labels, ordered):
            levels = ''.join(f'<span class="level">{escape(str(v))}</span>' for v in c["values"])
            rows.append(f'<div class="limits"><span class="threshold">{label}</span><span class="sequence">{levels}</span></div>')
        caption = escape(copy["life"].format(s=note["seconds"]))
        return (f'<div class="ladder" lang="{language}"><p class="condition shared-duration">{caption}</p>'
                + '<div class="ladder-rows">' + ''.join(rows) + '</div></div>')
    labels.append('<br>'.join(map(escape, copy["until"])))
    rows = []
    for label, c in zip(labels, ordered):
        cells = ''.join(f'<td><span class="level">{escape(str(v))}</span></td>' for v in c["values"])
        rows.append(f'<tr><th scope="row">{label}</th>{cells}</tr>')
    caption = escape(copy["perfect"].format(s=note["seconds"]))
    return (f'<table class="score-pair" lang="{language}"><caption class="condition">{caption}</caption>'
            '<colgroup><col><col span="5"></colgroup><tbody>' + ''.join(rows) + '</tbody></table>')

def title_text(title):
    # Let short phrases wrap at a comma before breaking a Chinese word.
    phrases = re.findall(r'[^，、]+[，、]?', title)
    if len(phrases) < 2 or ''.join(phrases) != title:
        return escape(title)
    return '<wbr>'.join(f'<span class="title-phrase">{escape(phrase)}</span>' for phrase in phrases)

def skill_name(skill, band):
    # Keep actual names intact. Mark only the natural break points inside them.
    units = re.compile(
        re.escape(band) + r'|&?(?:全部能力|表演|技巧|形象)值UP|&?激奏技能|激奏COMBO数'
        r'|抽选条|累积量UP|获得量UP|&?(?:翡翠|琉金)以外|【[^】]+】|[（(][^）)]+[）)]'
    )
    value = skill['name']
    pos = 0
    parts = []
    for match in units.finditer(value):
        parts.append(escape(value[pos:match.start()]))
        parts.append(f'<wbr><span class="keep">{escape(match.group())}</span>')
        pos = match.end()
    parts.append(escape(value[pos:]))
    return ''.join(parts)
def relation_name(title, group_ticket=False):
    if group_ticket:
        suffix = re.search(r'(?:期间限定\s*)?(?:SSR|SR|R)\s*必得券招募$', title)
        if suffix:
            return (relation_name(title[:suffix.start()]) + '<wbr>'
                    + f'<span class="pool-qualifier">{escape(suffix.group())}</span>')
    text = escape(title)
    phrases = r'#[A-Za-z0-9]+[～〜]?|(?:必得券|限定)?招募'
    return re.sub(phrases, r'<span class="keep">\g<0></span>', text)

NUMBER = re.compile(r"(\d+(?:\.\d+)?(?:%|秒|次|pt)?)")


def structure(text):
    return NUMBER.sub("\0", text)


def sections(descriptions):
    groups = []
    for _, indices in groupby(range(5), key=lambda i: structure(descriptions[i])):
        levels = list(indices)
        sample = levels[-1]
        tokens = [NUMBER.split(descriptions[i]) for i in levels]
        varying = [i for i in range(1, len(tokens[0]), 2) if len({t[i] for t in tokens}) > 1]
        # Keep each changing reading with its sentence, including its preceding condition.
        parts = []
        start = 0
        for pos, field in enumerate(varying):
            end = len(tokens[-1])
            if pos + 1 < len(varying):
                separator = tokens[-1][field + 1]
                cut = separator.find("\n")
                if cut < 0:
                    raise ValueError("Multiple changing quantities on one line need separate readout labels")
                prefix = separator[:cut]
                tail = separator[cut + 1:]
                after = prefix
                end = field + 1
            else:
                after = "".join(tokens[-1][field + 1:])
            before = "".join(tokens[-1][start:field])
            values = [""] * 5
            for level, token in zip(levels, tokens):
                values[level] = token[field]
            parts.append(dict(before=before, reading=tokens[-1][field], after=after, values=values))
            if pos + 1 < len(varying):
                tokens[-1][end] = tail
                start = end
        if not varying:
            parts.append(dict(before=descriptions[sample], reading="", after="", values=[]))
        for level in levels:
            reconstructed = "\n".join(x["before"] + (x["values"][level] if x["values"] else "") + x["after"] for x in parts)
            if reconstructed != descriptions[level]:
                raise ValueError(f"Cannot reproduce source at level {level+1}")
        groups.append(dict(sample=sample + 1, parts=parts))
    return groups


def original_effects(source):
    groups = []
    for group in source["groups"]:
        effects = []
        for part in group["parts"]:
            reading = f'<mark class="source-reading">{escape(part["reading"])}</mark>' if part["reading"] else ''
            text = escape(part["before"]) + reading + escape(part["after"])
            cells = ''.join(f'<span class="level">{escape(v)}</span>' for v in part["values"])
            row = f'<span class="sequence">{cells}</span>' if cells else ''
            effects.append(f'<p class="effect"><span class="condition">{text}</span>{row}</p>')
        sample = group["sample"]
        groups.append(f'<div class="source-group" data-sample="{sample}"><span class="source-level">Lv.{sample} 原文</span>'
                      + ''.join(effects) + '</div>')
    return '<div class="clauses">' + ''.join(groups) + '</div>'
