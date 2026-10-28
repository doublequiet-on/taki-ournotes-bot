"""Check actual per-request outcomes and native usage, not just rendered fixture text."""
import argparse,json
from collections import Counter
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('input',type=Path);p.add_argument('--partial',action='store_true');p.add_argument('--output',type=Path);a=p.parse_args()
runs=json.loads(a.input.read_text());errors=[];native=attempts=0
if not a.partial and len(runs)!=20:errors.append('Expected twenty ABBA process runs')
for run in runs:
 for row in run['rows']:
  label=f"{run['round']}/{run['turn']}/{run['label']}/{row['scenario']}/c{row['concurrency']}"
  count=sum(sample['command']=='/问 工程规则是什么意思' for sample in row['samples'])
  usage=row['backend']['usage'];native+=count;attempts+=len(usage)
  if len(usage)!=count*2 or any(x['state']!='settled' or x['charged']!='140' for x in usage):errors.append(label+': usage mismatch')
  if any(n!=2 for n in Counter(x['request_id'] for x in usage).values()):errors.append(label+': duplicate or missing native attempt')
  if any(row['backend']['model'][k] for k in ('running','waiting')) or row['backend']['pending_workers']:errors.append(label+': outstanding backend work')
  activity=row['activity'];material=activity['materials']
  if activity['pending_total'] or activity['accepted_inflight'] or activity['admission_inflight']:errors.append(label+': outstanding receiver work')
  if any(material[k] for k in ('blocked','completion_unknown','deletion_unknown','pending','pending_bytes','queued','running','state_queued','state_active')):errors.append(label+': outstanding material work')
  if material['schema']!=1 or activity['capacity']!=8:errors.append(label+': compatibility or capacity changed')
result=dict(complete=len(runs)==20,runs=len(runs),rows=sum(len(r['rows']) for r in runs),native_requests=native,settled_attempts=attempts,errors=errors)
if a.output:a.output.write_text(json.dumps(result,indent=2))
print(json.dumps(result))
raise SystemExit(bool(errors))
