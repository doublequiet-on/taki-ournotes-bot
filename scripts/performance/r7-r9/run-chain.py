from __future__ import annotations
import argparse,hashlib,json,os,subprocess,sys,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--base',type=Path,required=True);p.add_argument('--new',type=Path,required=True);p.add_argument('--node',required=True);p.add_argument('--assets',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--rounds',type=int,default=5);p.add_argument('--scenarios',default='light,cold,ask-mixed');p.add_argument('--requests',type=int,default=8);a=p.parse_args()
a.output.mkdir(parents=True,exist_ok=False)
cg=Path('/sys/fs/cgroup')/next(line.split(':',2)[2].lstrip('/') for line in Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0:'))
limits={name:(cg/name).read_text().strip() for name in ['cpu.max','memory.max','memory.swap.max']}
assert limits['memory.max']=='4294967296' and limits['memory.swap.max']=='0',limits
q,period=limits['cpu.max'].split();assert int(q)/int(period)==4,limits
(a.output/'limits.json').write_text(json.dumps(limits,indent=2))
manifest=[]
for rnd in range(a.rounds):
 for turn,label in enumerate('ABBA'):
  repo=a.base if label=='A' else a.new
  output=a.output/f'r{rnd}-{turn}-{label}'
  cmd=[sys.executable,'-B',str(repo/'scripts/benchmark_queries.py'),'--output',str(output),'--assets',str(a.assets),'--node',a.node,'--runs','1','--requests',str(a.requests),'--concurrency','1,2,4','--scenarios',a.scenarios]
  env=dict(os.environ,PYTHONPATH=str(repo/'src'),QQ_APP_ID='',QQ_APP_SECRET='',AI_API_KEY='')
  print(f'RUN {rnd}/{turn}/{label}',flush=True)
  with (a.output/f'r{rnd}-{turn}-{label}.log').open('w') as log:subprocess.run(cmd,cwd=repo,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
  result=json.loads((output/'results.json').read_text())
  # The historical harness does not know about its supervising cgroup; bind the measured limits explicitly.
  result['limits']['host_resource_limit']={'supervisor_cgroup':limits,'scope':'whole benchmark process tree; isolated local Linux, not production'}
  (output/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
  manifest.append({'round':rnd,'turn':turn,'label':label,'head':result['head'],'rows':result['rows']})
  (a.output/'all-runs.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
print('COMPLETE',len(manifest),flush=True)
