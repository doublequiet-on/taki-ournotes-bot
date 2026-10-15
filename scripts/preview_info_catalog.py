#!/usr/bin/env python3
"""Render six isolated synthetic previews; no network, accounts or production cache."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from PIL import Image, ImageDraw

from ournotes_bot.query.contracts.info import InfoAnswer, InfoRow, InfoView, info_text
from ournotes_bot.rendering.haneoka_theme import font
from ournotes_bot.rendering.info_layout import render_info


def samples():
    note = ('布局预览 · 合成资料与合成素材，不代表游戏当前活动或卡池。',)
    activity = tuple(InfoRow(f'合成活动 {i} · 夏日的乐队故事',
        ('开始：2026-10-01 15:00（UTC+09:00）', '结束：2026-10-10 20:59（UTC+09:00）'),
        image_url=f'synthetic:banner:{i}', image_kind='wide', label='已结束', command=f'/查活动 {i}') for i in range(1, 4))
    gacha = tuple(InfoRow(f'合成卡池 {i} · 星光下的演奏',
        ('活动期间：2026-10-01 — 2026-10-10', '角色卡与留影卡关联以已核对资料为准。'),
        image_url=f'synthetic:banner:{i}', image_kind='wide', command=f'/查卡池 {i}') for i in range(1, 4))
    people = tuple(InfoRow(f'合成角色 {i}', ('合成乐队 · Guitar',),
        image_url=f'synthetic:portrait:{i}', command=f'/查角色 {i}') for i in range(1, 5))
    return {
        'activity-list': InfoView('活动列表', '日服 · 按 ID 或名称查详情', rows=activity, footer=note),
        'activity-detail': InfoView('活动详情', '合成活动 1 · 日服', 'synthetic:banner:1',
            (InfoRow('活动时间', ('开始：2026-10-01 15:00（UTC+09:00）', '结束：2026-10-10 20:59（UTC+09:00）'), label='已结束'),
             InfoRow('挑战歌曲', ('合成歌曲 A · 歌曲 ID 101',), image_url='synthetic:song:1', command='/查榜线 歌曲ID=101'),
             InfoRow('已核对的活动资料', ('无奖励或加成资料时保持未确认，不从图片推断。',))), note, 'detail'),
        'gacha-list': InfoView('卡池列表', '日服 · 按 ID 或名称查详情', rows=gacha, footer=note),
        'gacha-detail': InfoView('卡池详情', '合成卡池 1 · 日服', 'synthetic:banner:2',
            (InfoRow('开放时间', ('2026-10-01 15:00 — 2026-10-10 20:59（UTC+09:00）',)),
             InfoRow('关联角色卡 · 合成卡牌 A', ('角色：合成角色 1',), image_url='synthetic:card:1', rarity=4, command='/查卡 1'),
             InfoRow('卡池说明', ('资料未提供概率、保底规则时不推测。',))), note, 'detail'),
        'character-list': InfoView('角色目录', '合成乐队 · 4 位角色', rows=people, footer=note),
        'character-detail': InfoView('角色详情', '合成角色 1 · 合成乐队', 'synthetic:portrait:1',
            (InfoRow('基本资料', ('位置：Guitar', 'CV：合成配音演员', '生日：1 月 1 日')),
             InfoRow('角色介绍', ('与乐队成员一起努力，把想传达的心情写进旋律。', '这里的每个段落会完整保留。介绍较长时按段落接到后续图片，字体保持一致。')),
             InfoRow('相关查询', ('查看该角色的角色卡和留影卡。',), command='/查卡 合成角色1\n/查留影卡 合成角色1')), note, 'character'),
    }


def artwork(url, size):
    width, height = ((900, 300) if ':banner:' in url else (360, 640) if ':portrait:' in url else (300, 300))
    colors = ['#c7d6ed', '#ead2e6', '#c9e4df', '#e9dfc9']
    number = int(url.rsplit(':', 1)[-1])
    image = Image.new('RGB', (width, height), colors[(number - 1) % len(colors)])
    draw = ImageDraw.Draw(image)
    draw.ellipse((width * .18, height * .1, width * .9, height * .9), fill='#f4eff7')
    draw.text((24, 24), '合成预览素材', font=font(28, True), fill='#555994')
    return image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('runtime/info-catalog-previews'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = []
    for name, view in samples().items():
        answer = InfoAnswer(info_text((view,)), views=(view,))
        for index, page in enumerate(render_info(answer, load=artwork), 1):
            path = args.output / f'{name}-{index}.jpg'
            path.write_bytes(page.image)
            path.with_suffix('.txt').write_text(page.text, encoding='utf8')
            with Image.open(path) as image:
                report.append({'file': str(path), 'size': list(image.size), 'bytes': len(page.image)})
    (args.output / 'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
