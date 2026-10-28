"""Local real-Pillow ABBA, with synthetic fixed artwork and externally sampled RSS."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def worker(a):
    sys.path.insert(0,str(a.repo/'src'))
    from dataclasses import asdict
    from PIL import Image,ImageDraw
    from ournotes_bot.data import Card,SupportCard,Skill
    from ournotes_bot.rendering import card_detail_layout as layout,haneoka_theme as theme
    from ournotes_bot.rendering import readable_pages
    if a.reclaim:
        from ournotes_bot.rendering.image_output import reclaim_after_render
        layout.render=reclaim_after_render(layout.render)
    from unittest.mock import patch
    support=a.case=='snap'
    text='保留完整工程条件。'*(20 if a.case=='long' else 1)
    catalog=dict(source='haneoka',band_logos=['logo'],attribute_icon='attribute',skill_icons=['skill'],
        skill_levels=[[{'zh':f'{n}秒，{text}','ja':f'{n}秒、完全な条件。','en':f'{n}s; complete condition.'} for n in range(1,6)]],
        people=[dict(id=n,band=1,order=n,name={'zh':'工程人物'+str(n),'ja':'試験'+str(n),'en':'Fixture '+str(n)},face='face'+str(n)) for n in range(6)],
        relations=[dict(kind='相关活动',id=n,name={'zh':'工程关联'+str(n),'ja':'試験'+str(n),'en':'Fixture '+str(n)},
                        image='related'+str(n),start='2026-10-01T00:00:00Z',end='2026-10-02T00:00:00Z') for n in range(a.relations)])
    common=dict(id=1,title='工程详情 '+text,character='工程人物',rarity=4,card_type=1,performance=10000,technic=20000,visual=30000,
                start_at='',thumbnail_url='',full_url='full',skills=(Skill('supportSkill' if support else 'liveSkill','工程技能',text),),catalog=catalog)
    card=SupportCard(characters=('工程人物','工程人物二'),**common) if support else Card(asset_id=1,band='工程乐队',skill_name='',**common)
    cache={};counts=dict(source=0,miss=0,late=0,canvas=0);images=[]
    def raw_image(url,size):
        color=tuple(hashlib.sha256(url.encode()).digest()[:3])
        with Image.new('RGB',size,color) as im:
            draw=ImageDraw.Draw(im)
            for x in range(0,size[0],37):draw.line((x,0,size[0]-x,size[1]),fill=tuple(255-v for v in color),width=3)
            buf=io.BytesIO();im.save(buf,format='PNG');return buf.getvalue()
    def load(url,size):
        counts['source']+=1
        if counts['canvas']:counts['late']+=1
        key=url,size
        if key not in cache:
            counts['miss']+=1
            # Source latency is a declared local fixture, never network IO.
            time.sleep(.003)
            cache[key]=raw_image(url,(972,547 if support else 1296) if url=='full' else size)
        with Image.open(io.BytesIO(cache[key])) as original:return original.convert('RGBA')
    icon=raw_image('rarity',(80,40))
    original_canvas=layout.Canvas;original_encode=readable_pages.encode_image
    def canvas(*args,**kwargs):
        obj=original_canvas(*args,**kwargs);counts['canvas']+=1;return obj
    def encode(image,**kwargs):
        if a.preencode:
            from ournotes_bot.rendering.image_output import _release_image_memory
            _release_image_memory(image.width*image.height)
        try:return original_encode(image,**kwargs)
        finally:counts['canvas']-=1
    with patch.object(layout,'Canvas',canvas),patch.object(readable_pages,'encode_image',encode),theme.game_assets(lambda name:icon):
        if a.warm:layout.render(card,a.locale,load=load)
        counts={k:0 for k in counts}
        began=time.perf_counter();cpu=time.process_time();began_ns=time.monotonic_ns()
        parts=layout.render(card,a.locale,load=load)
        ended_ns=time.monotonic_ns();elapsed=time.perf_counter()-began;cpu=time.process_time()-cpu
    for index,part in enumerate(parts):
        with Image.open(io.BytesIO(part.image)) as image:
            images.append(dict(range=[part.start,part.stop],size=image.size,
                               bytes=len(part.image),sha256=hashlib.sha256(part.image).hexdigest(),
                               pixels_sha256=hashlib.sha256(image.tobytes()).hexdigest()))
        if a.images:
            a.images.mkdir(parents=True,exist_ok=True);(a.images/f'{index}.jpg').write_bytes(part.image)
    result=dict(render_interval_ns=[began_ns,ended_ns],case=a.case,relations=a.relations,locale=a.locale,warm=a.warm,elapsed_ms=elapsed*1000,cpu_seconds=cpu,
                images=images,counters=counts,fixture_sha256=hashlib.sha256(json.dumps(asdict(card),sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
                assets_sha256=hashlib.sha256(b''.join(cache[key] for key in sorted(cache))).hexdigest(),
                fonts={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(theme.ASSETS.glob('*.ttf'))},
                head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=a.repo,text=True).strip())
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2))


def run(a):
    from knowledge_benchmark import sample
    a.output.mkdir(parents=True,exist_ok=True)
    cg=Path('/sys/fs/cgroup')/next(line.split(':',2)[2].lstrip('/') for line in Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0:'))
    limits={name:(cg/name).read_text().strip() for name in ('cpu.max','memory.max','memory.swap.max')}
    q,p=limits['cpu.max'].split();assert int(q)/int(p)==4
    assert limits['memory.max']=='4294967296' and limits['memory.swap.max']=='0'
    (a.output/'limits.json').write_text(json.dumps(limits))
    rows=[];reference={}
    cases=(('member',0,'zh'),('snap',8,'zh'),('long',64,'zh'),('snap',8,'ja'),('member',8,'en'))
    if a.profile:cases=(('snap',8,'zh'),('long',64,'zh'))
    for case,relations,locale in cases:
        for warm in (False,True):
            for rnd in range(5):
                for turn,label in enumerate('ABBA'):
                    key=f'{case}-{relations}-{locale}-{warm}'
                    name=f'{key}-r{rnd}-{turn}-{label}';out=a.output/(name+'.json')
                    cmd=[sys.executable,'-B',__file__,'--worker','--repo',str(a.base if label=='A' else a.new),
                         '--case',case,'--relations',str(relations),'--locale',locale,'--output',str(out)]
                    if warm:cmd.append('--warm')
                    if a.reclaim and label=='B':cmd.append('--reclaim')
                    if a.preencode and label=='B':cmd.append('--preencode')
                    if rnd==0 and turn in (0,1):cmd+=['--images',str(a.output/'images'/(key+'-'+label))]
                    with (a.output/(name+'.log')).open('w') as log:
                        proc=subprocess.Popen(cmd,stdout=log,stderr=log);samples=[]
                        while proc.poll() is None:
                            samples.append((time.monotonic_ns(),sample(proc.pid)));time.sleep(.002)
                        assert proc.returncode==0,name
                    row=json.loads(out.read_text());begin,end=row['render_interval_ns']
                    measured=[v for t,v in samples if begin<=t<=end];assert measured,name
                    row.update(round=rnd,turn=turn,label=label,peak_rss_bytes=max(measured),process_peak_rss_bytes=max(v for t,v in samples),render_rss_samples=len(measured))
                    frozen={k:row[k] for k in ('images','fixture_sha256','assets_sha256','fonts')}
                    if key in reference:assert frozen==reference[key],('render drift',name)
                    else:reference[key]=frozen
                    if label=='B':assert row['counters']['late']==0,name
                    assert row['counters']['canvas']==0,name
                    rows.append(row);(a.output/'runs.json').write_text(json.dumps(rows,ensure_ascii=False))
            print('DONE',case,relations,locale,warm,flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--worker',action='store_true');p.add_argument('--warm',action='store_true')
    p.add_argument('--preencode',action='store_true');p.add_argument('--reclaim',action='store_true');p.add_argument('--profile',action='store_true')
    for name in ('repo','base','new','output','images'):p.add_argument('--'+name,type=Path)
    p.add_argument('--case',default='member');p.add_argument('--relations',type=int,default=0);p.add_argument('--locale',default='zh')
    args=p.parse_args();worker(args) if args.worker else run(args)
