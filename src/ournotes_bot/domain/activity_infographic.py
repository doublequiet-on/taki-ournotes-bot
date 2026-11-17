# L3: Pure JP activity reward and exchange summaries, independent of sources and rendering.
from collections import Counter
import re

FIXED_ITEMS = {1: '星石', 23: '奇迹水晶', 22: '幸运水晶',
               24: '演出技能强化券', 25: '激奏技能强化券', 27: 'SP 技能强化券'}
ITEM_LABELS = {**FIXED_ITEMS, 3: '金币', 5: '成员 EXP', 6: '留影 EXP', 26: '技能强化券',
               19: '紫苑碎片（小）', 20: '紫苑碎片（中）', 21: '紫苑碎片（大）',
               29: 'Ave Mujica 棱镜', 33: '希望棱镜', 36: '乐曲券', 2000002: 'Our Notes 招募券'}


def source_date(value):
    return value[0] if isinstance(value, list) and value else None


def source_name(value):
    if isinstance(value, list):
        value = value[0] if value else ''
    return value.strip() if isinstance(value, str) else ''


def exact_id(value):
    result = str(value)
    if not re.fullmatch(r'[1-9][0-9]{0,18}', result):
        raise ValueError('invalid activity resource ID')
    return result


def nonnegative(value):
    if type(value) is not int or not 0 <= value <= 2**31-1:
        raise ValueError('invalid activity resource count')
    return value


def reward_summary(event):
    totals, thresholds = Counter(), {}
    specials = {}
    rows = event.get('rewards')
    if not isinstance(rows, list) or not rows:
        raise ValueError('activity rewards pending')
    for row in rows:
        if row.get('sourceTable') != 'MasterEventAchievementReward':
            continue
        reward = row.get('reward')
        if not isinstance(reward, dict):
            raise ValueError('unresolved activity reward')
        item = int(exact_id(reward.get('resourceId')))
        amount = nonnegative(reward.get('count'))
        threshold = nonnegative(row['raw']['_eventPoint'])
        if reward.get('kind') == 'Item' and item in FIXED_ITEMS:
            totals[item] += amount
            thresholds[item] = max(thresholds.get(item, 0), threshold)
        elif reward.get('kind') in {'MemberCard', 'SupportCard', 'Stamp', 'Degree'} or item == 57:
            _, tiers = specials.setdefault((reward.get('kind'), item), (reward, []))
            tiers.append((threshold, amount))
    return tuple((name, totals[item], item) for item, name in FIXED_ITEMS.items() if item in totals), thresholds, tuple(specials.values())


def exchange_summary(exchange):
    summaries = {}
    for product in exchange['products'] if exchange else ():
        limit = nonnegative(product['limit'])
        if not limit:
            continue
        reward = product['reward']
        key = (nonnegative(reward['resourceType']), int(exact_id(reward['resourceId'])))
        if product.get('paymentSteps') or product.get('resetType') != 1:
            raise ValueError('unsupported activity exchange pricing')
        quantity = nonnegative(reward['count']) * limit
        cost = nonnegative(product['cost']) * limit
        old = summaries.get(key)
        summaries[key] = (reward, quantity + (old[1] if old else 0), cost + (old[2] if old else 0))
    return tuple(summaries.values())
