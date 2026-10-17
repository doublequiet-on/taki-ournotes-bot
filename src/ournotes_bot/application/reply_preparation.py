# L3
# Input: Existing Song/Chart records, source selection, locale and injectable source readers.
# Output: Captured drawing inputs and exact fallback text; no platform identity or cursor.
# Pos: Application reply preparation; see ../../L2-Application.md.
# Effects: Reads chart source once; expected source failures retain existing text semantics.
from dataclasses import dataclass
from ..data import Song, Chart, localized_text, note_text
from ..sources.chart_data import ChartDataError, load_chart_score, load_chart_data


@dataclass(frozen=True)
class ChartReply:
    song: Song
    charts: tuple[Chart, ...]
    score: dict | None
    preview: str | None
    locale: str
    notice: str
    text: str


@dataclass(frozen=True)
class SongReply:
    song: Song
    score: dict | None
    locale: str
    band_logos: tuple[str, ...]
    text: str


def prepare_chart_reply(song,charts,source,locale,*,load_score=load_chart_score,load_data=load_chart_data):
    preview=next((c for c in charts if c.difficulty=='EXPERT'),charts[-1] if charts else None)
    score=None;notice=''
    if preview:
        try:
            if source=='moenotes':score=load_score(song,preview)
            else:
                captured=load_data(song,preview,source=source);score=captured.score;notice=captured.notice(locale)
        except ChartDataError:
            notice='谱面暂不可用'
    text=f'{song.title} · ID {song.id} · '+notice+'\n'+' / '.join(f'{c.difficulty} {c.display_level:g} · {note_text(c.notes)}' for c in charts)
    return ChartReply(song,tuple(charts),score,preview.difficulty if preview else None,locale,notice,text)


def prepare_song_reply(song,source,locale,*,load_score=load_chart_score,load_data=load_chart_data):
    score=None;selected=next((c for c in song.charts if c.difficulty=='EXPERT'),None)
    if selected:
        try:
            score=load_score(song,selected) if source=='moenotes' else load_data(song,selected,source=source).score
        except ChartDataError:pass
    logos=song.catalog.get('band_logos',[])
    text=f'{localized_text(song,"title",locale)} · ID {song.id} · '+localized_text(song,'band',locale)
    text+='\n'+' / '.join(f'{ch.difficulty} {ch.display_level:g} · {ch.notes if ch.notes is not None else "?"} Note' for ch in song.charts)
    if song.traits:text+='\n激奏：'+' → '.join(song.traits.missions or ())
    text+='\n乐曲时长（毫秒）：'+str(song.catalog.get('duration_ms') or '未获取')
    if score:
        from ..rendering.song_detail_layout import seconds_at
        events=score.get('events',{});bpms=events.get('bpm',[])
        text+='\nBPM：'+', '.join(str(v['bpm']) for v in bpms)
        text+='\n技能点位（秒）：'+', '.join(str(seconds_at(t,bpms)) for t in sorted(events.get('skill',[])))
    for label,rows in [('得分奖励',song.catalog.get('score_rewards',[])),('EXPERT 连击奖励',song.catalog.get('combo_rewards',{}).get('expert',[]))]:
        text+='\n'+label+'：'+' / '.join(str(row['tier'])+' '+(row['label'].get(locale) or row['label'].get('ja') or '道具')+' ×'+str(row['count']) for row in rows)
    return SongReply(song,score,locale,tuple(logos),text)
