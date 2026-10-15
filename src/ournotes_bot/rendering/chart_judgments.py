# L3
# Input: Validated SS notes and the source binary32 chart clock.
# Output: Judgment times including guide/automatic nodes and merged slide ends.
# Pos: Rendering / Chart statistics; shared by song and chart layouts.
# Effects: Pure conversion, no network, cache, scoring predictions or platform calls.
"""SS judgment conversion, checked against captured runtime-chart reference vectors.

Guide starts absorb a coincident single note. Only trace-to-trace guides carry
eighth-beat judgments; hidden endpoints remain structural, never judgments.
"""
import struct

UNJUDGED = {0, 80, 82, 100, 103, 121, 122, 123}
BEGINS = {20, 41, 61, 80, 104}
ENDS = {22, 42, 62, 82, 105}
MERGEABLE = {20, 22, 41, 42, 61, 62, 80, 82, 100, 101, 102, 103, 104, 105}


def f(value):
    return struct.unpack('f', struct.pack('f', value))[0]


def judgments(notes, position, ms_position, bases, eighths):
    def read(raw):
        node = dict(type='tap', t=0, pos=0, size=6, visible=True,
                    crit=False, dir='up', ease='linear')
        node.update(raw)
        node['auto'] = node['pos'] == 'auto'
        node['pos'] = 0 if node['auto'] else f(node['pos'])
        node['size'] = f(node['size'])
        ease = node['ease']
        node['ease'] = tuple(ease) if isinstance(ease, list) else (ease, ease)
        if 'node' in raw: node['node'] = [read(v) for v in raw['node']]
        return node

    notes = [read(n) for n in notes]
    def overlap(n): return n['t'], round(n['pos']), round(n['size'])
    starts = {overlap(n['node'][0]) for n in notes if n['type'] == 'guide'
              and n.get('node') and not n['node'][0]['auto']}
    guide_map, absorbed = {}, set()
    for i, n in enumerate(notes):
        key = overlap(n)
        if n['type'] in ('tap', 'flick', 'trace') and key in starts and key not in guide_map:
            guide_map[key] = n
            absorbed.add(i)
    infos = []
    def info(n, op, lane, width, index=None, direction=None):
        infos.append(dict(pos=position(n['t']), tick=n['t'], lane=round(f(lane)),
                          width=max(1, round(width)), op=op, crit=n['crit'],
                          direction=(direction or n)['dir'] if (direction or n)['type'] == 'flick' else 'up',
                          ease=n['ease'] if index is not None else ('linear', 'linear'),
                          lines=[] if index is None else [index]))
    for i, n in enumerate(notes):
        if n['type'] in ('tap', 'flick', 'trace') and i not in absorbed:
            info(n, {'tap':1, 'flick':40, 'trace':60}[n['type']], n['pos'], n['size'])
    in_use = []
    for kind in ('long', 'guide'):
        chains = sorted((n for n in notes if n['type'] == kind and len(n.get('node', [])) > 1),
                        key=lambda n:n['node'][0]['t'])
        for chain in chains:
            nodes = chain['node']
            in_use = [(i,t) for i,t in in_use if t >= nodes[0]['t']]
            used = {i for i,_ in in_use}
            index = next((i for i in range(20) if i not in used), None)
            if index is None: raise ValueError('SS chart exceeds 20 simultaneous lines')
            for i, n in enumerate(nodes):
                end = i == len(nodes)-1
                single = guide_map.get(overlap(n)) if kind == 'guide' and i == 0 and not n['auto'] else None
                if kind == 'long':
                    if i == 0: op = 80 if not n['visible'] else {'flick':41,'trace':61}.get(n['type'],20)
                    elif end: op = 82 if not n['visible'] else {'flick':42,'trace':62}.get(n['type'],22)
                    else: op = 21 if n['auto'] else 122 if not n['visible'] else 63 if n['type']=='trace' else 21
                else:
                    if i == 0: op = {'tap':101,'flick':102,'trace':104}[single['type']] if single else 100
                    elif end: op = 105 if n['visible'] and n['type']=='trace' else 103
                    else: op = 63 if n['auto'] or n['visible'] else 122
                lane, width = n['pos'], n['size']
                if n['auto']:
                    if i == 0 or end: lane, width = nodes[0]['pos'], nodes[0]['size']
                    else:
                        j=i-1
                        while j>0 and nodes[j]['auto']: j-=1
                        k=i+1
                        while k<len(nodes)-1 and nodes[k]['auto']: k+=1
                        a,b=nodes[j],nodes[k]
                        span=b['t']-a['t']; progress=f(f(n['t']-a['t'])/f(span)) if span>0 else 0
                        def ease(value, mode):
                            return f(value*value) if mode=='in' else f(f(2-value)*value) if mode=='out' else value
                        lane=f(a['pos']+f(ease(progress,a['ease'][0])*f(b['pos']-a['pos'])))
                        right=f(f(a['pos']+a['size'])+f(ease(progress,a['ease'][1])*f(f(b['pos']+b['size'])-f(a['pos']+a['size']))))
                        width=f(right-lane)
                info(n,op,lane,width,index,single)
            in_use.append((index,nodes[-1]['t']))
    dictionary = {}
    for item in infos:
        key=f(item['pos'][1]+f(item['pos'][0]))
        lane=dictionary.setdefault(key,{}).setdefault(item['lane'],[])
        match=next((v for v in lane if all(v[k]==item[k] for k in ('op','width','crit','direction','ease'))),None)
        if item['op'] in MERGEABLE and match is not None: match['lines'].extend(item['lines'])
        else: lane.append(item)
    active, long_lines, guide_lines = {}, {}, {}
    result=[]; current=0; current_guide=10000
    def end_line(note):
        collected=[]; valid=True
        for line in note['ids']:
            begin=active.pop(line,None)
            if begin is None: valid=False; break
            middle=sorted((v for v in begin['middle'] if line in v['ids']),key=lambda v:v['pos'])
            mids=[v for v in middle if v['op'] not in UNJUDGED]
            bounds=[begin,*mids,note]
            beats=[t for a,b in zip(bounds,bounds[1:]) for t in eighths(a['tick'],b['tick'])]
            combos=[]
            for k, now in enumerate(beats):
                if any(v['ms']==now for v in [*mids,note]): continue
                bpm=next((v[2] for v in reversed(bases) if v[1]<=now),160.0)
                guard=15000/bpm
                if any(v['ms']-guard<=now<v['ms'] for v in mids): continue
                if k==0 and guard>now-begin['ms']: continue
                if k==len(beats)-1 and guard>note['ms']-now: continue
                if begin['op'] in BEGINS: combos.append(now)
            if begin['op'] in BEGINS and note['op'] in ENDS: collected.extend(combos)
        if valid: result.extend(collected)
    for key in sorted(dictionary):
        batch=[v for lane in dictionary[key].values() for v in lane]
        batch.sort(key=lambda v:8 if v['op'] in {20,41,61,80,100,101,102,104} else 9 if v['op']==122 else 10)
        for item in batch:
            op=item['op']; item['ms']=ms_position(*item['pos']); item['ids']=[]; item['middle']=[]
            if op in {20,41,61,80,100,101,102,104}:
                for slot in item['lines']:
                    if op<100: current+=1; identity=current; long_lines[slot]=identity
                    else: current_guide+=1; identity=current_guide; guide_lines[slot]=identity
                    item['ids'].append(identity); active[identity]=item
            elif op in {22,42,62,82,103,105}:
                slots=long_lines if op<100 else guide_lines
                item['ids']=[slots.pop(i,-1) for i in item['lines']]
                end_line(item)
            elif op in {21,63,122}:
                slot=item['lines'][0]
                identity=(long_lines if op==21 else guide_lines).get(slot,-1)
                if op==122: identity=long_lines.get(slot,guide_lines.get(slot,-1))
                item['ids']=[identity]
                if identity in active: active[identity]['middle'].append(item)
            if op not in UNJUDGED: result.append(item['ms'])
    return [v/1000 for v in sorted(result)]
