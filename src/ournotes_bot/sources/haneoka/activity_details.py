# L3
# Input: Same-release event details and an injected approved artwork resolver.
# Output: Immutable bonus, reward, mission and recruitment sections.
# Pos: Sources / Haneoka event projection, consumed by information catalog.
# Effects: Pure conversion; no inferred cross-event or cross-server links.
from ...domain.info_catalog import ActivityFact, ActivitySection
import json


def text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return next((value[i] for i in (3, 2, 0, 1, 4) if i < len(value) and value[i]), '')
    return ''


def rows(value):
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > 20000 or any(not isinstance(row, dict) for row in value):
        raise ValueError('Invalid event detail collection')
    return value


def project_details(event, artwork=lambda path: ''):
    result = []
    groups = {}
    for effect in rows(event.get('effects')):
        targets = effect.get('targets', {})
        if not isinstance(targets, dict):
            raise ValueError('Invalid bonus targets')
        names = [text(target.get('name')) for target in targets.values() if isinstance(target, dict)]
        kind = {0: '成员卡参数加成', 1: '留影卡参数加成', 2: '活动道具加成'}.get(effect.get('bonusType'), '加成效果未确认')
        levels = tuple(f"档位{r['rank']}：+{r['percent']}%" for r in rows(effect.get('perRank'))
                       if type(r.get('rank')) is int and type(r.get('percent')) in (int, float))
        constraint = {2: '成员卡', 3: '留影卡'}.get(effect.get('resourceTypeConstraint'), '')
        title = '／'.join(filter(None, (constraint, *names))) or '加成对象未确认'
        key = (effect.get('resourceTypeConstraint'), json.dumps(targets, sort_keys=True, ensure_ascii=False))
        if key not in groups:
            groups[key] = (title, [], [])
        groups[key][1].append(kind + '：' + (' · '.join(levels) if levels else '数值未获取'))
        groups[key][2].append((kind, *levels))
    effects = [ActivityFact(title, tuple(lines), table=tuple(table)) for title, lines, table in groups.values()]
    result.append(ActivitySection('加成', tuple(effects), isinstance(event.get('effects'), list)))
    rewards = []
    complete = isinstance(event.get('rewards'), list)
    for row in rows(event.get('rewards')):
        reward = row.get('reward', {})
        if not isinstance(reward, dict):
            raise ValueError('Invalid reward reference')
        if row.get('unresolvedRewardId'):
            complete = False
            rewards.append(ActivityFact('奖励未解析', (f"奖励 ID {row['unresolvedRewardId']}",)))
            continue
        raw = row.get('raw', {})
        conditions = tuple(f'{label}：{raw[key]}' for key, label in (
            ('_eventPoint', '所需活动积分'), ('_point', '所需积分'), ('_requiredPoint', '所需积分'),
            ('_rankStart', '起始名次'), ('_rankEnd', '结束名次'), ('_rank', '名次'), ('_ranking', '名次'),
            ('_loopStartEventPoint', '循环起始积分'), ('_loopEventPoint', '每轮积分'),
            ('_boxCount', '奖励箱次'), ('_limitCount', '次数上限'),
            ('_achievementCount', '达成次数')) if key in raw)
        if '_scoreRank' in raw:
            grade = {0: '未指定', 1: 'E', 2: 'D', 3: 'C', 4: 'B', 5: 'A', 6: 'S', 7: 'SS'}.get(raw['_scoreRank'], '未确认')
            conditions += ('成绩档位：' + grade,)
        probability = raw.get('_probability')
        if type(probability) in (int, float) and 0 <= probability <= 10000:
            conditions += (f'抽选概率：{probability / 100:g}%',)
        category = {'MasterChallengeLiveEventReward': '挑战LIVE结算抽选', 'MasterLiveEventReward': '普通LIVE结算抽选',
                    'MasterEventAchievementReward': '累计积分奖励', 'MasterEventAchievementLoopReward': '循环积分奖励',
                    'MasterEventRankingReward': '排名奖励', 'MasterEventBoxGachaReward': '活动奖励箱'}.get(row.get('sourceTable'), '活动奖励')
        name = text(reward.get('name')) or f"奖励 {row.get('resourceId', '未获取')}"
        count = row.get('resourceCount')
        rewards.append(ActivityFact(name, (category, f'数量：{count if count is not None else "未获取"}', *conditions), artwork(reward.get('image')),
                                    preview=row.get('resourceType') in {2, 3, 9, 17}))
    result.append(ActivitySection('奖励', tuple(rewards), complete))
    missions = []
    for mission in rows(event.get('missions')):
        granted = []
        for reward in rows(mission.get('rewards')):
            ref = reward.get('reward', {})
            granted.append(text(ref.get('name')) + ' ×' + str(reward.get('resourceCount', '未获取'))
                           if not reward.get('unresolvedRewardId') else f"奖励 ID {reward['unresolvedRewardId']} 未解析")
        missions.append(ActivityFact(text(mission.get('description')) or '任务条件未获取', tuple(granted)))
    result.append(ActivitySection('任务', tuple(missions), isinstance(event.get('missions'), list)))
    gachas = []
    for gacha in rows(event.get('recruitments')):
        gachas.append(ActivityFact(text(gacha.get('title')) or '关联卡池',
                                  (f"卡池 ID {gacha.get('id', '未获取')}",), artwork(gacha.get('image'))))
        for featured in rows(gacha.get('featured')):
            if featured.get('pickup') is True:
                ref = featured.get('reward') or featured.get('card') or featured
                gachas.append(ActivityFact('UP · ' + (text(ref.get('name')) or f"卡牌 {featured.get('resourceId', '未获取')}"),
                    ('成员卡' if featured.get('resourceType') == 2 else '留影卡',), artwork(ref.get('image'))))
    result.append(ActivitySection('卡池', tuple(gachas), isinstance(event.get('recruitments'), list)))
    return tuple(result)
