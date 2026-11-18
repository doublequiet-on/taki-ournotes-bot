# L3: One static Taki activity image from captured JP event facts and challenge results.
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from ..domain.activity_infographic import (
    ITEM_LABELS, exchange_summary, reward_summary, source_date, source_name,
)
from .haneoka_theme import ACCENT, INK, LINE, MUTED, SOFT, Canvas, frame, lines
from .image_output import encode_image, reclaim_after_render

LEFT, RIGHT = 54, 1026
WIDTH = RIGHT - LEFT
ZONE = timezone(timedelta(hours=8))


def date(milliseconds):
    return datetime.fromtimestamp(milliseconds / 1000, ZONE).strftime('%Y-%m-%d %H:%M')


class InfographicCanvas(Canvas):
    def __init__(self, data, load):
        super().__init__(1080, 8192)
        self.data, self.load = data, load
        self.y = 0

    def section(self, title, note=''):
        self.line(LEFT, self.y, RIGHT, '#d9d4df')
        self.text(LEFT, self.y + 32, title, 31, INK, True)
        if note:
            self.text(RIGHT, self.y + 32, note, 22, MUTED, align='right')
        self.y += 60

    def art(self, path, x, y, w, h):
        image = self.load(self.data.image(path), (w, h)) if path else None
        if path and image is None:
            raise ValueError('activity artwork temporarily unavailable')
        try:
            self.paste(image, x, y, w, h, True, radius=8)
        finally:
            if image is not None:
                image.close()

    def paragraph_row(self, title, value):
        self.text(LEFT, self.y + 16, title, 24, INK, True)
        self.y = self.paragraph(LEFT + 176, self.y, value, WIDTH - 176, 23, 32, MUTED) + 7

    def overview(self, period):
        event = self.data.event
        self.box((926, 54, 1026, 94), SOFT, 20)
        self.text(976, 74, 'Taki', 22, '#3d417b', True, 'center')
        self.y = self.paragraph(LEFT, 52, period.title, 820, 44, 56, INK, True)
        title = source_name(event.get('title'))
        subtitle = '日服 · 活动一图流' + (f' · {title}' if title and title != period.title else '')
        self.y = self.paragraph(LEFT, self.y, subtitle,
                                WIDTH, 24, 34, MUTED) + 20
        self.art(event['image'], LEFT + 26, self.y + 36, 420, 180)
        x = LEFT + 498
        self.text(x, self.y + 27, f'活动 ID {period.event_id} · 北京时间（UTC+8）', 22, MUTED)
        for offset, label, stamp in ((73, '开始', period.start), (147, '结束', period.end)):
            self.text(x, self.y + offset, label, 22, MUTED)
            self.text(x, self.y + offset + 33, date(stamp), 27, INK, True)
        if self.data.exchange and (deadline := source_date(self.data.exchange.get('endAt'))):
            self.text(LEFT + 26, self.y + 241, '徽章兑换截止 ' +
                      date(deadline), 22, ACCENT, True)
        self.y += 270

        groups, subjects = defaultdict(set), defaultdict(set)
        for effect in event.get('effects', ()):
            targets = effect.get('targets') or {}
            prefix = ('指定卡牌' if {'memberCard', 'supportCard'} & targets.keys()
                      else '指定乐队' if 'band' in targets else '指定属性' if 'attribute' in targets else '其他加成')
            by_rank = effect.get('perRank') or []
            if by_rank:
                groups[prefix].add(by_rank[-1]['percent'])
            if prefix != '指定卡牌':
                subjects[prefix].update(name for target in targets.values()
                                        if (name := source_name(target.get('name')).removesuffix('タイプ')))
        if groups:
            self.section('活动加成', '满档 · 参数与活动道具')
            for label, values in groups.items():
                targets = '／'.join(sorted(subjects[label]))
                self.paragraph_row(label, '　'.join(filter(None, (targets, '／'.join(f'+{v:g}%' for v in sorted(values))))))
            self.y += 10

    def item_grid(self, rows):
        for start in range(0, len(rows), 3):
            chunk = rows[start:start+3]
            headings = [lines(row[0], 248, 22, True) for row in chunk]
            h = max(map(len, headings)) * 30
            for index, ((_, path, amount, price), heading) in enumerate(zip(chunk, headings)):
                x = LEFT + index*324
                self.art(path, x, self.y + 11, 44, 44)
                cy = self.y + 17
                for label in heading:
                    self.text(x+53, cy, label, 22, INK, True)
                    cy += 30
                self.text(x+53, self.y+h+16, f'×{amount:,}', 22, MUTED)
                if price is not None:
                    self.text(x+305, self.y+h+16, f'{price:,} 徽章', 22, ACCENT, align='right')
            self.y += h + 35
            self.line(LEFT, self.y, RIGHT, '#e1dce7')

    def rewards(self):
        fixed, thresholds, specials = reward_summary(self.data.event)
        self.section('本期奖励汇总')
        if fixed:
            self.text(LEFT, self.y + 14, '累计 PT 固定奖励', 26, ACCENT, True)
            self.text(RIGHT, self.y + 14, f'{max(thresholds.values()):,} PT 领齐所列道具', 22, MUTED, align='right')
            self.y += 41
            images = {row['reward'].get('resourceId'): row['reward'].get('image') for row in self.data.event['rewards']
                      if row.get('sourceTable') == 'MasterEventAchievementReward' and isinstance(row.get('reward'), dict)}
            self.item_grid([(name, images.get(item), amount, None) for name, amount, item in fixed])
        if specials:
            self.text(LEFT, self.y + 28, '限定奖励与门槛', 26, ACCENT, True)
            self.y += 52
        for reward, tiers in specials:
            self.art(reward.get('image'), LEFT, self.y + 5, 94, 68)
            title = source_name(reward.get('name')) or f"奖励 ID {reward['resourceId']}"
            end = self.paragraph(LEFT + 112, self.y + 3, title, 555, 24, 32, INK, True)
            first = min(threshold for threshold, _ in tiers)
            self.text(RIGHT, self.y + 25, f'{first:,} PT' + (' 首份' if len(tiers) > 1 else ''), 24, ACCENT, True, 'right')
            if len(tiers) > 1:
                end = self.paragraph(LEFT + 112, end + 4, f'{max(t for t,_ in tiers):,} PT 领齐 {sum(n for _,n in tiers):,} 份',
                                     555, 21, 30, MUTED)
            self.y = max(end + 8, self.y + 80)
            self.line(LEFT, self.y, RIGHT, '#e1dce7')
        for row in self.data.event['rewards']:
            if row.get('sourceTable') != 'MasterEventAchievementLoopReward':
                continue
            raw, reward = row['raw'], row['reward']
            name = ITEM_LABELS.get(reward['resourceId']) or source_name(reward.get('name'))
            self.paragraph_row('循环奖励', f"{raw['_loopStartEventPoint']:,} PT 后，每 {raw['_loopEventPoint']:,} PT 得 {name} ×{reward['count']:,}")
        self.y += 22

    def shop(self):
        trades = exchange_summary(self.data.exchange)
        if not trades:
            return
        self.section('徽章商店可换道具', '限购总量 · 换完所需徽章')
        rows = []
        for reward, quantity, cost in trades:
            name = (ITEM_LABELS.get(reward['resourceId']) if reward.get('kind') == 'Item' else None)
            rows.append((name or source_name(reward.get('name')) or f"道具 ID {reward['resourceId']}",
                         reward.get('image'), quantity, cost))
        self.item_grid(rows)
        self.y += 20

    def draw_card(self, card, x, y, width, height):
        border = frame(width, height, card['rarity'])
        self.image.paste(border, (x-4, y-4), border)
        self.art(card.get('image'), x, y, width, height)
        url = self.data.attribute(card.get('attribute'))
        if url:
            icon = self.load(url, (28, 28))
            if icon is not None:
                try: self.paste(icon, x+2, y+2, 28, 28, True)
                finally: icon.close()
        self.rarity_badge(card['rarity'], x+width-5, y+height-5, 40, 30)

    def challenge(self):
        data = self.data
        if not data.scores:
            return
        self.section('挑战曲冲榜', 'EXPERT · AP · 全卡满培养')
        self.paragraph(LEFT, self.y, '25 位角色 Rank 30 · 乐器 Lv.15 · TGW CARD Lv.16', WIDTH, 22, 32, MUTED)
        self.y += 40
        groups = {}
        for score in data.scores:
            key = (tuple(score['members']), tuple(score['snaps']))
            groups.setdefault(key, len(groups) + 1)
        for score in data.scores:
            song = data.master['songs'][str(score['songId'])]
            self.art(song.get('jacketThumbUrl') or song.get('jacketUrl'), LEFT, self.y+5, 66, 66)
            end = self.paragraph(LEFT+87, self.y+2, source_name(song['musicTitle']), 560, 25, 33, INK, True)
            order = ' → '.join(key[1:] for key in score['order'])
            group = groups[(tuple(score['members']), tuple(score['snaps']))]
            note = ('阵容 ' + str(group) + ' · ' if len(groups) > 1 else '')
            note += '技能顺序 ' + order
            end = self.paragraph(LEFT+87, end+3, note, 690, 21, 30, MUTED)
            self.text(RIGHT, self.y+21, f"{score['score']:,}", 31, ACCENT, True, 'right')
            self.text(RIGHT, self.y+55, '模拟最高分' if score['proven'] else '限时搜索结果', 20, MUTED, align='right')
            self.y = max(self.y+82,end+12)
            self.line(LEFT,self.y,RIGHT,'#e1dce7')
        for (members, snaps), group in groups.items():
            self.text(LEFT, self.y+25, '共用阵容' if len(groups)==1 else f'阵容 {group}', 25, INK, True)
            self.y += 54
            for index, (member, snap) in enumerate(zip(members, snaps)):
                x = LEFT+index*196
                card = data.master['members'][member[1:]]
                self.draw_card(card, x, self.y, 108, 144)
                character = data.master['characters'][str(card['characterId'])]
                name = source_name(character.get('name') or character.get('characterName')).split(' / ')[-1]
                self.text(x, self.y+164, name, 20, MUTED)
                self.text(x, self.y+190, f'成员卡 ID {member[1:]}' + (' · 队长' if index == 0 else ''), 19, MUTED)
                self.draw_card(data.master['snapshots'][snap[1:]], x, self.y+218, 144, 81)
                self.text(x, self.y+319, f'留影卡 ID {snap[1:]}', 19, MUTED)
            self.y += 347

    def recruitment(self):
        pools = self.data.event.get('recruitments') or []
        if not pools:
            return
        self.section('关联卡池')
        for row in pools:
            self.art(row.get('image'), LEFT, self.y, 300, 129)
            x = LEFT + 324
            end = self.paragraph(x, self.y+6, source_name(row.get('title')), RIGHT-x, 25, 36, INK, True)
            until = source_date(row.get('endAt'))
            detail = f"卡池 ID {row['id']}" + (f' · {date(until)} 截止' if until else '')
            end = self.paragraph(x, end+6, detail, RIGHT-x, 22, 32, MUTED)
            featured = defaultdict(list)
            for card in row.get('featured', ()):
                if card.get('pickup') is True:
                    featured[card.get('resourceType')].append(str(card.get('resourceId')))
            notes = [label+' '+ '、'.join(featured[key]) for key,label in ((2,'UP 成员'),(3,'UP 留影')) if featured[key]]
            if notes:
                end = self.paragraph(x, end+4, ' · '.join(notes), RIGHT-x, 21, 31, MUTED)
            self.y = max(self.y+150, end+16)


@reclaim_after_render
def render_infographic(period, data, *, load):
    canvas = InfographicCanvas(data, load)
    try:
        canvas.overview(period)
        canvas.rewards()
        canvas.shop()
        canvas.challenge()
        canvas.recruitment()
        canvas.line(LEFT, canvas.y, RIGHT, LINE)
        y = canvas.paragraph(LEFT, canvas.y+12, '资料来源 Haneoka 日服 · ' + data.identity['releaseId'],
                             WIDTH, 21, 31, MUTED)
        if data.scores:
            members, snaps = data.inventory
            y = canvas.paragraph(LEFT, y, f'开场卡库 {members} 张成员卡与 {snaps} 张留影卡 · 角色与乐曲记忆加成按 0',
                                 WIDTH, 21, 31, MUTED)
        canvas.text(LEFT,y+23,'Taki · Our Notes',20,MUTED)
        if y + 46 > canvas.height:
            raise ValueError('activity infographic too tall')
        return encode_image(canvas.image.crop((0, 0, 1080, y+46)), close_input=True, preserve_size=True)
    finally:
        canvas.image.close()
