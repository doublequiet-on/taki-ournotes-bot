import json,statistics
from pathlib import Path
root=Path(__file__).resolve().parent
def summarize(file,keys):
    rows=json.loads((root/file).read_text(encoding='utf-8'));out=[]
    for key in sorted({tuple(r[k] for k in keys) for r in rows}):
        sample=[r for r in rows if tuple(r[k] for k in keys)==key]
        item=dict(zip(keys,key));item['runs']=len(sample)
        for metric in ('elapsed_ms','cpu_seconds','peak_rss_bytes'):
            values={label:[r[metric] for r in sample if r['label']==label] for label in 'AB'}
            item[metric]={label:dict(median=statistics.median(v),min=min(v),max=max(v),std=statistics.stdev(v)) for label,v in values.items()}
            item[metric]['change_pct']=(statistics.median(values['B'])/statistics.median(values['A'])-1)*100
            paired=[];noise=[];both_noise=[];deltas=[]
            for rnd in range(5):
                av=[r[metric] for r in sample if r['label']=='A' and r['round']==rnd]
                bv=[r[metric] for r in sample if r['label']=='B' and r['round']==rnd]
                paired.append((statistics.mean(bv)/statistics.mean(av)-1)*100)
                noise.append(abs(av[0]-av[1])/statistics.mean(av)*100)
                both_noise.append(max(abs(av[0]-av[1]),abs(bv[0]-bv[1])))
                deltas.append(statistics.mean(bv)-statistics.mean(av))
            item[metric]['paired_changes_pct']=paired;item[metric]['A_repeat_noise_pct']=noise
            item[metric]['paired_deltas']=deltas;item[metric]['within_round_AB_difference']=both_noise
            scale=statistics.median(both_noise)
            item[metric]['classification']='consistent_increase' if all(v>scale for v in deltas) else 'consistent_decrease' if all(v < -scale for v in deltas) else 'overlaps_repeat_variation'
        if 'counters' in rows[0]:
            item['counters']={label:[r['counters'] for r in sample if r['label']==label][0] for label in 'AB'}
        out.append(item)
    (root/(file.removesuffix('-runs.json')+'-summary.json')).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(file,len(rows))
    for r in out:
        print([r[k] for k in keys], 'ms',*(round(r['elapsed_ms'][label]['median'],2) for label in 'AB'),
              'RSS MiB',*(round(r['peak_rss_bytes'][label]['median']/1048576,2) for label in 'AB'),
              'time %',round(r['elapsed_ms']['change_pct'],2),'rss %',round(r['peak_rss_bytes']['change_pct'],2),
              'noise',round(statistics.median(r['elapsed_ms']['A_repeat_noise_pct']),2),round(statistics.median(r['peak_rss_bytes']['A_repeat_noise_pct']),2))
summarize('knowledge-runs.json',('documents','chunks','parts'))
summarize('detail-final-runs.json',('case','relations','locale','warm'))
