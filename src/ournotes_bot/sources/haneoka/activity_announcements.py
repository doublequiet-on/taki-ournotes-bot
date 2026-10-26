# L3: Bounded verified game notices -> explicitly published upcoming event time.
# Effects: One notice index and at most three details within a shared eight-second deadline.
import re
import time
from datetime import datetime, timedelta, timezone

from ...domain.info_catalog import AnnouncedActivity
from ...domain.query_policy import CURRENT_CONTENT_DEADLINE
from ..errors import ChartDataError
from .notices import Notices


JP_TITLE = re.compile(r'イベント\s*[「『](.+?)[」』]')
EN_TITLE = re.compile(r'Event\s+["「](.+?)["」]', re.I)
CHANGED = re.compile(r'延期|中止|変更|取消|延后|延後|postpon|cancel|resched', re.I)
BODY_CHANGED = re.compile(r'延期(?:とな|いた|しま)|中止(?:とな|いた|しま)|開催(?:を中止|延期)|'
                          r'(?:開始日時|開催日時|開催期間)を変更|has been (?:postponed|cancelled|canceled|rescheduled)', re.I)
DATE = r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})(?:[（(][^()（）\n]{1,12}[)）])?\s+(\d{1,2}):(\d{2})(?::(\d{2}))?'


def announced_time(row, server):
    """Only event-period body text is evidence; notice publication time is never used."""
    match = (JP_TITLE if server == 'jp' else EN_TITLE).search(row['title'])
    if not match or CHANGED.search(row['title']) or row.get('text_truncated'):
        return None
    text = row.get('text', '')
    if BODY_CHANGED.search(text):
        return None
    heading = r'(?:【イベント開催期間】|【開催期間】)' if server == 'jp' else r'(?:Event Period|Event Duration)\s*[:：]?'
    periods = list(re.finditer(heading + r'\s*' + DATE, text, re.I))
    if len(periods) != 1:
        return None
    period = periods[0]
    # Japanese source uses Japan time. International English notices must state
    # their offset explicitly; language or announcement startAt is not a timezone.
    offset = 9
    if server == 'intl':
        section = text[period.start():period.end()+180].split('\n\n')[0]
        offsets = set(re.findall(r'\b(?:UTC|GMT)(?:\s*([+-]\d{1,2})(?::(\d{2}))?)?\b', section, re.I))
        if len(offsets) != 1:
            return None
        hours, minutes = next(iter(offsets))
        if minutes not in ('', '00') or hours and not -12 <= int(hours) <= 14:
            return None
        offset = int(hours or 0)
    try:
        values = [int(value or 0) for value in period.groups()]
        start = datetime(*values, tzinfo=timezone(timedelta(hours=offset))).timestamp()
    except (ValueError, OverflowError):
        return None
    return match[1], start


class ActivityAnnouncements:
    def __init__(self, notices=None):
        self.notices = notices or Notices()

    def next(self, server, now):
        deadline = min(time.monotonic()+8, CURRENT_CONTENT_DEADLINE.get() or float('inf'))
        locale = 'ja' if server == 'jp' else 'en'
        index_url = f'https://haneoka.org/{server}/{locale}/announcements/'
        try:
            snapshot = self.notices.read(server, deadline)
        except ChartDataError:
            return None, '下一期公告暂时读取失败，不能据此判断是否已发布。'
        pattern = JP_TITLE if server == 'jp' else EN_TITLE
        candidates = [row for row in snapshot['rows'] if pattern.search(row['title'])
                      and (row['startAt'] is None or row['startAt'] <= now)]
        candidates.sort(key=lambda r: (r['updatedAt'] or r['startAt'] or 0, r['id']), reverse=True)
        changed = {pattern.search(row['title'])[1] for row in candidates if CHANGED.search(row['title'])}
        found = []
        incomplete = len(candidates) > 3
        for row in candidates[:3]:
            try:
                detail = self.notices.read(server, deadline, row['id'])
                if detail['observed_at'] != snapshot['observed_at']:
                    raise ChartDataError('Notice snapshot changed')
                full = detail['rows'][0]
                if any(full[key] != row[key] for key in ('id','title','startAt','endAt','updatedAt')):
                    raise ChartDataError('Notice identity changed')
                if BODY_CHANGED.search(full.get('text', '')):
                    changed.add(pattern.search(full['title'])[1])
                value = announced_time(full, server)
            except ChartDataError:
                incomplete = True
                continue
            if value and value[1] > now:
                found.append(AnnouncedActivity(value[0], value[1], index_url+str(row['id'])+'/', snapshot['observed_at'],
                    datetime.fromisoformat(snapshot['observed_at'].replace('Z','+00:00')).timestamp()+86400))
            elif value is None:
                incomplete = True
        if any(row.title in changed for row in found):
            return None, f'这期预告另有延期或调整公告，开始时间需再核对。\n[查看活动公告]({index_url})'
        if found:
            times = {(r.title, r.start_at) for r in found}
            # Multiple future announcements are not enough to establish which is next.
            if len(times) == 1:
                return found[0], '公告列表未完全核对；以上时间仅对应这条已读取的活动预告。' if incomplete else ''
            return None, '读到了多条不同的未来活动时间，暂不能唯一确定下一期。'
        note = '公告正文缺失、时间不明确或未能完整核对，暂不能确认下一期时间。' if incomplete else '已读取的公告中尚未找到明确的下一期开启时间。'
        return None, note + f'\n[查看活动公告]({index_url})'
