# L3
# Input: A captured validated Catalog envelope and requested ranking game server.
# Output: Source-scoped card artwork/progression records; unknowns remain absent.
# Pos: Sources / Haneoka; optional ranking visual projection.
# Effects: Pure conversion, no network or cross-server fallback.
from .catalog_assets import asset_url,attribute_url
from .catalog_text import language


def cards_for(saved,server):
    source='jp' if server=='jp' else 'intl'
    if not saved:return {},{}
    doc=saved['documents'][source];release=saved['identities'][source]['releaseId'];progress=doc.get('progression',{})
    result=[]
    for support,resource in ((False,'cards'),(True,'support-cards')):
        records={}
        for key,row in doc[resource].items():
            group=row.get('supportCardLevelGroup' if support else 'memberCardLevelGroup')
            catalog={'attribute_icon':attribute_url(doc.get('ui-marks',{}),row['cardType'],release,source),
              'level_curve':[{'level':v['level'],'exp':v['exp']} for v in progress.get('supportCardLevels' if support else 'memberCardLevels',[]) if v.get('group')==group],
              'level_limits':[v['raw'] for v in progress.get('supportCardRanks' if support else 'memberCardLevelLimits',[]) if
                (v['raw'].get('_group')==row.get('supportCardRankGroup') if support else v['raw'].get('_rarity')==row['rarity'])],
              'player_levels':progress.get('playerRanks',[])}
            values=dict(id=int(key),title=language(row.get('prefix',[])),character='',rarity=row['rarity'],card_type=row['cardType'],
              performance=0,technic=0,visual=0,start_at='',full_url=asset_url(row['images']['full'],release),thumbnail_url=asset_url(row['images']['thumbnail'],release),catalog=catalog)
            records[int(key)]={**values,**({'characters':()} if support else {'asset_id':row['assetId'],'band':'','skill_name':''})}
        result.append(records)
    return tuple(result)



def event_marks(saved, event):
    """Only decorate the exact source event/period, never another server's event."""
    if not saved or event.source != 'haneoka':
        return ()
    source = 'jp' if event.server == 'jp' else 'intl'
    doc = saved['documents'][source]
    row = doc.get('events', {}).get(str(event.event_id), {})
    # Regional dates use TIME_REGIONS (jp, en, tw, cn, kr), not text locale indices.
    index = {'jp': 0, 'en': 1, 'tw': 2, 'kr': 4}[event.server]
    start, end = row.get('startAt', []), row.get('endAt', [])
    if len(start) <= index or len(end) <= index or (start[index], end[index]) != (event.start_ms, event.end_ms):
        return ()
    release = saved['identities'][source]['releaseId']
    effects = [v.get('raw', {}) for v in row.get('effects', [])]
    bands = sorted({v['_bandId'] for v in effects if type(v.get('_bandId')) is int and v['_bandId'] > 0})
    colors = sorted({v['_cardType'] for v in effects if type(v.get('_cardType')) is int and 1 <= v['_cardType'] <= 5})
    marks = [('加成属性', attribute_url(doc.get('ui-marks', {}), color, release, source)) for color in colors]
    marks += [('加成乐队', asset_url(doc['bands'][str(b)]['logo'], release)) for b in bands if doc['bands'].get(str(b), {}).get('logo')]
    return tuple((label, url) for label, url in marks if url)


def bands_for(saved,server):
    """Band labels from the same captured regional catalogue as the ranking art."""
    if not saved:return {}
    doc=saved['documents']['jp' if server=='jp' else 'intl']
    return {str(key):language(doc.get('bands',{}).get(str(row.get('bandId')),{}).get('bandName',[]))
            for key,row in doc.get('songs',{}).items()}
