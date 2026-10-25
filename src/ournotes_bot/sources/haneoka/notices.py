# L3: Bounded anonymous notice snapshots and catalog identity probes.
# Effects: Fixed Haneoka endpoints only; no HTML execution, media or persistence.
from datetime import datetime
from html.parser import HTMLParser
import time
from urllib.parse import urlencode

from ..errors import ChartDataError
from ..json_validation import decode_json
from .identity import release_identity, check_headers
from .transport import ORIGIN, public_get, public_http_scope


def need(condition):
    if not condition: raise ChartDataError('Invalid Haneoka notice snapshot')


def get(path, deadline, fetch):
    remaining=deadline-time.monotonic()
    if remaining<=0: raise ChartDataError('Haneoka lookup deadline')
    raw,headers=fetch(ORIGIN+path,1_000_000,min(8,remaining))
    need(len(raw)<=1_000_000)
    return decode_json(raw),headers


def verify_catalog(metadata, deadline, fetch=public_get):
    with public_http_scope():
        for server,field in (('jp','data_version'),('intl','text_version')):
            value,headers=get(f'/api/v1/servers/{server}/release?projection=identity',deadline,fetch)
            ident=release_identity(value,server);check_headers(headers,ident)
            need(ident['releaseId']==metadata.get(field))


class PlainNotice(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden=[];self.parts=[];self.images=False
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','head','iframe','object'):
            self.hidden.append(tag)
        if tag=='img': self.images=True
        if tag in ('p','div','br','li','tr','h1','h2','h3'):self.parts.append('\n')
    def handle_endtag(self,tag):
        if self.hidden and tag==self.hidden[-1]:self.hidden.pop()
    def handle_data(self,data):
        if not self.hidden:self.parts.append(data)


class Notices:
    def __init__(self, *, fetch=public_get, clock=time.time):
        self.fetch,self.clock=fetch,clock

    def read(self,server,deadline,notice_id=None):
        need(server in ('jp','intl'))
        need(notice_id is None or type(notice_id) is int and 0<notice_id<2**53)
        locale='ja' if server=='jp' else 'en'
        route='/api/v1/announcements'+('/'+str(notice_id) if notice_id is not None else '')
        params=dict(server=server,locale=locale)
        if notice_id is None:params['limit']=100
        value,headers=get(route+'?'+urlencode(params),deadline,self.fetch)
        need(isinstance(value,dict) and value.get('server')==server and value.get('available') is True)
        stamp=value.get('fetchedAt')
        need(isinstance(stamp,str))
        try:
            observed=datetime.fromisoformat(stamp.replace('Z','+00:00'))
            need(observed.tzinfo is not None)
            age=self.clock()-observed.timestamp()
        except (ValueError,OverflowError):raise ChartDataError('Invalid notice time') from None
        need(-300<=age<=86400 and headers.get('x-haneoka-announcement-fetched-at')==stamp)
        language=value.get('actualSourceLocale')
        need(language==locale)  # Never silently mix another international game locale.
        rows=value.get('announcements') if notice_id is None else [value]
        need(isinstance(rows,list) and len(rows)<=100)
        seen=set();clean=[]
        for row in rows:
            need(isinstance(row,dict))
            rid=row.get('id');title=row.get('title')
            need(type(rid) is int and 0<rid<2**53 and rid not in seen)
            need(isinstance(title,str) and 0<len(title)<=500)
            need(row.get('actualSourceLocale',language)==language)
            seen.add(rid)
            item=dict(id=rid,title=title)
            for key in ('startAt','endAt','updatedAt'):
                number=row.get(key)
                need(type(number) is int and 0<=number<2**40)
                item[key]=number or None
            need(not item['startAt'] or not item['endAt'] or item['startAt']<item['endAt'])
            if notice_id is not None:
                need(rid==notice_id and isinstance(row.get('html'),str))
                parser=PlainNotice();parser.feed(row['html']);parser.close()
                text='\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())
                item.update(text=text[:2400],text_truncated=len(text)>2400,
                            images_not_read=parser.images or bool(row.get('bodyImage')))
            clean.append(item)
        return dict(source='Haneoka 游戏公告镜像',server=server,language=language,
                    observed_at=stamp,rows=clean,listing_limit=100)
