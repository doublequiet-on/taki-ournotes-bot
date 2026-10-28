"""Summarize completed real-chain ABBA evidence. Never runs a benchmark or contacts services."""
from __future__ import annotations
import argparse, hashlib, json, math, re, statistics as stats, sys
from collections import Counter, defaultdict
from pathlib import Path

HOT = {'hot', 'mixed', 'ask-mixed'}
BAD_TEXT = re.compile('暂时失败|稍后重试|没有找到|未找到')
EXPECTED_HARNESS = '5ccf621170d63cdbde54c0c28cbb11b091d748c461e945675ca0ed9c8c84f413'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def quantile(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered)*fraction)-1)] if ordered else None


def describe(values):
    values = [v for v in values if isinstance(v, (int, float)) and math.isfinite(v)]
    if not values:
        return {'n': 0, 'median': None, 'min': None, 'max': None, 'mad': None}
    center = stats.median(values)
    return {'n': len(values), 'median': center, 'min': min(values), 'max': max(values),
            'mad': stats.median(abs(v-center) for v in values)}


def outcomes(row):
    groups = {key: [] for key in ('full_success', 'degraded', 'rejected', 'unclassified')}
    for sample in row['samples']:
        parts = sample['parts']
        success = (sample['ledger_status'] == 'finished' and bool(parts)
                   and not any(BAD_TEXT.search(part['text']) for part in parts)
                   and (sample['command'] == '/帮助' or sample['command'] == '/问 工程规则是什么意思' and bool(sample.get('business')) and all(p['business_status']=='success' for p in sample['business']) or any(part['image'] for part in parts)
                        and bool(sample.get('business'))
                        and all(part['business_status'] == 'success' for part in sample['business'])))
        degraded = sample['command'] not in ('/帮助','/问 工程规则是什么意思') and bool(parts) and not any(p['image'] for p in parts)
        rejected = not parts
        if sum(map(bool, (success, degraded, rejected))) > 1:
            raise ValueError('outcome classifier is not mutually exclusive')
        group = 'full_success' if success else 'degraded' if degraded else 'rejected' if rejected else 'unclassified'
        groups[group].append(sample)
    return groups


def metrics(row, groups):
    values = {key: len(samples) for key, samples in groups.items()}
    values.update({key+'_rate': len(samples)/row['count'] for key, samples in groups.items()})
    values.update(duration_ms=row['duration_ms'], successful_throughput=row['successful_throughput'],
                  p50_ms=row['p50_ms'] if groups['full_success'] else None,
                  p95_ms=row['p95_ms'] if groups['full_success'] else None,
                  loop_p95_ms=row['loop_p95_ms'], peak_reported_pending=row['peak_reported_pending'],
                  completed_images=row['completed_images'], uploads=row['uploads'],
                  telemetry_dropped=row.get('telemetry', {}).get('dropped_events'))
    for label in ('python', 'node'):
        sample = row.get('process_samples', {}).get(label, {})
        for field in ('cpu_seconds', 'peak_rss_bytes', 'count'):
            values[f'{label}_process.{field}'] = sample.get(field)
    for name, observations in row['timings'].items():
        prefix = 'stages_including_warmup.'+name
        values.update({prefix+'.count': len(observations), prefix+'.total_ms': sum(observations),
                       prefix+'.p50_ms': quantile(observations, .5), prefix+'.p95_ms': quantile(observations, .95),
                       prefix+'.mean_ms': stats.mean(observations) if observations else None})
    for name, count in row['backend']['counters'].items():
        values['backend_counters_including_setup_warmup.'+name] = count
    for name, total in row['backend']['inclusive_stage_ms'].items():
        prefix = 'backend_stages_including_setup_warmup.'+name
        count = row['backend']['counters'].get(name, 0)
        values[prefix+'.total_ms'] = total
        values[prefix+'.mean_ms'] = total/count if count else None
    for name, count in row['activity'].get('admission_attempts', {}).items():
        values['admission_counts_including_warmup.'+name] = count
    return values


def paired(entries, name, rounds):
    getter = lambda e: e['metrics'].get(name, 0 if name.startswith(('backend_counters_', 'admission_counts_')) else None)
    by_label = {label: describe([getter(e) for e in entries if e['label'] == label]) for label in 'AB'}
    pairs = []
    for rnd in range(rounds):
        a = [getter(e) for e in entries if e['round'] == rnd and e['label'] == 'A']
        b = [getter(e) for e in entries if e['round'] == rnd and e['label'] == 'B']
        if len(a) != 2 or len(b) != 2 or any(v is None for v in a+b):
            continue
        ac, bc = stats.mean(a), stats.mean(b)
        pairs.append({'round': rnd, 'A': ac, 'B': bc, 'delta_B_minus_A': bc-ac,
                      'change_percent': 100*(bc/ac-1) if ac else None,
                      'within_round_difference': max(abs(a[0]-a[1]), abs(b[0]-b[1]))})
    delta = describe([p['delta_B_minus_A'] for p in pairs])['median']
    noise = describe([p['within_round_difference'] for p in pairs])['median']
    classification = 'incomplete_or_not_comparable'
    if len(pairs) == rounds:
        ds = [p['delta_B_minus_A'] for p in pairs]
        classification = ('equal' if all(d == 0 for d in ds) else
                          'consistent_higher_beyond_round_variability' if all(d > 0 for d in ds) and delta > noise else
                          'consistent_lower_beyond_round_variability' if all(d < 0 for d in ds) and -delta > noise else
                          'not_consistent_beyond_round_variability')
    return {**by_label, 'paired_rounds': pairs, 'median_paired_delta': delta,
            'median_paired_change_percent': describe([p['change_percent'] for p in pairs])['median'],
            'median_within_round_difference': noise, 'classification': classification}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='all-runs.json or its containing folder')
    parser.add_argument('--metadata-root', type=Path, help='Contains r<round>-<turn>-<label>/results.json; default input parent')
    parser.add_argument('--output', type=Path, help='Output prefix; default input parent/chain-summary')
    parser.add_argument('--rounds', type=int, default=5)
    parser.add_argument('--base-head')
    parser.add_argument('--candidate-head')
    args = parser.parse_args()
    path = args.input/'all-runs.json' if args.input.is_dir() else args.input
    root = args.metadata_root or path.parent
    prefix = args.output or path.parent/'chain-summary'
    errors, warnings, manifests, run_ids = [], [], [], set()
    runs = read(path)
    baseline_metadata = None
    label_sources, label_heads = {}, {}
    groups, flattened, all_counts = defaultdict(list), [], {label: Counter() for label in 'AB'}
    expected_order = [(rnd, turn, label) for rnd in range(args.rounds) for turn, label in enumerate('ABBA')]
    actual_order = [(r['round'], r['turn'], r['label']) for r in runs]
    if actual_order != expected_order:
        errors.append(f'Expected {len(expected_order)} ordered ABBA runs, found {actual_order!r}')
    for run in runs:
        rnd, turn, label = run['round'], run['turn'], run['label']
        rid = f'r{rnd}-{turn}-{label}'
        if rid in run_ids:
            errors.append('Duplicate run '+rid)
            continue
        run_ids.add(rid)
        meta_path = root/rid/'results.json'
        if not meta_path.is_file():
            errors.append('Missing manifest '+str(meta_path))
            metadata = None
        else:
            metadata = read(meta_path)
            manifests.append({'run': rid, 'path': str(meta_path), 'sha256': hashlib.sha256(meta_path.read_bytes()).hexdigest()})
            if metadata['head'] != run['head'] or metadata['rows'] != run['rows']:
                errors.append(rid+': manifest head/rows differ from all-runs.json')
            shared = {k: metadata[k] for k in ('schema','host','logical_cpus','python','python_dependencies','node','limits','inputs','evidence_scope')}
            shared['arguments'] = {k: v for k,v in metadata['arguments'].items() if k != 'output'}
            if baseline_metadata is None:
                baseline_metadata = shared
            elif shared != baseline_metadata:
                changed = [k for k in shared if shared[k] != baseline_metadata[k]]
                errors.append(rid+': shared inputs/dependencies/caps differ: '+','.join(changed))
            source = metadata['source_and_build']
            if label in label_sources and source != label_sources[label]:
                errors.append(rid+': source/build fingerprint changed within '+label)
            label_sources.setdefault(label, source)
            if metadata['inputs'].get('koishi/scripts/benchmark-queries.cjs') != EXPECTED_HARNESS:
                errors.append(rid+': unexpected common native knowledge/card fixture harness hash')
            if any(not re.fullmatch('[a-f0-9]{64}', value) for value in source.values()):
                errors.append(rid+': invalid source/build hash')
            limits = metadata['limits']
            expected_caps = {'gateway_pending':8,'query_workers':2,'query_queue':4,'retained_reply_bytes':128000000}
            if any(limits.get(k) != v for k,v in expected_caps.items()):
                errors.append(rid+': application input caps changed')
            try:
                cg = limits['host_resource_limit']['supervisor_cgroup']
                quota, period = map(int,cg['cpu.max'].split())
                cap_ok = quota/period == 4 and cg['memory.max']=='4294967296' and cg['memory.swap.max']=='0'
            except (KeyError, TypeError, ValueError):
                cap_ok = False
            if not cap_ok:
                errors.append(rid+': 4 CPU/4 GiB/no-swap cgroup evidence missing or wrong')
        if label in label_heads and label_heads[label] != run['head']:
            errors.append(rid+': HEAD changed within '+label)
        label_heads.setdefault(label, run['head'])
        expected_head = args.base_head if label=='A' else args.candidate_head
        if expected_head and run['head'] != expected_head:
            errors.append(rid+': unexpected HEAD '+run['head'])
        keys = set()
        for row in run['rows']:
            key = (row['scenario'], row['concurrency'])
            if key in keys or row.get('run') != 0:
                errors.append(rid+': duplicate scenario/concurrency or unexpected inner run')
            keys.add(key)
            counts = outcomes(row)
            if len(row['samples']) != row['count'] or sum(map(len,counts.values())) != row['count']:
                errors.append(rid+str(key)+': request count mismatch')
            if (len(counts['full_success']),len(counts['degraded']),len(counts['rejected'])) != (row['success'],row['degraded'],row['rejected']):
                errors.append(rid+str(key)+': stored outcome counts differ from sample reclassification')
            latencies = [s['duration_ms'] for s in counts['full_success']]
            for name,q in [('p50_ms',.5),('p95_ms',.95)]:
                if not math.isclose(row[name],quantile(latencies,q) or 0,rel_tol=1e-9,abs_tol=1e-6):
                    errors.append(rid+str(key)+': stored '+name+' differs from samples')
            if not math.isclose(row['successful_throughput'],row['success']*1000/row['duration_ms'],rel_tol=1e-9):
                errors.append(rid+str(key)+': throughput differs from successful count/duration')
            if metadata and row['count'] != int(metadata['arguments']['requests']):
                errors.append(rid+str(key)+': input request count differs from arguments')
            if any(row['process_samples'].get(p,{}).get('count',0)==0 for p in ('python','node')):
                warnings.append(rid+str(key)+': missing external resource samples')
            item = {'round':rnd,'turn':turn,'label':label,'run':rid,'head':run['head'],
                    'scenario':key[0],'concurrency':key[1],'metrics':metrics(row,counts),
                    'outcomes':{name:{'count':len(samples),'latencies_ms':[s['duration_ms'] for s in samples],
                        'ledger_statuses':dict(Counter(s['ledger_status'] for s in samples))} for name,samples in counts.items()}}
            groups[key].append(item);flattened.append(item)
            all_counts[label].update({name:len(samples) for name,samples in counts.items()})
        if metadata:
            expected_keys = {(scenario,int(concurrency)) for scenario in metadata['arguments']['scenarios'].split(',') for concurrency in metadata['arguments']['concurrency'].split(',')}
            if keys != expected_keys:
                errors.append(rid+': scenario/concurrency matrix differs from arguments')
    source_diff = []
    if all(label in label_sources for label in 'AB'):
        for name in sorted(label_sources['A'].keys() | label_sources['B'].keys()):
            a,b = label_sources['A'].get(name),label_sources['B'].get(name)
            if a != b:
                source_diff.append({'path':name,'A':a,'B':b})
    comparisons=[]
    for (scenario,concurrency),entries in sorted(groups.items()):
        names = sorted({name for item in entries for name in item['metrics']})
        outcome_totals = {label:{name:sum(e['outcomes'][name]['count'] for e in entries if e['label']==label) for name in ('full_success','degraded','rejected','unclassified')} for label in 'AB'}
        pooled={label:{name:{'count':sum(e['outcomes'][name]['count'] for e in entries if e['label']==label),
            'p50_ms':quantile([v for e in entries if e['label']==label for v in e['outcomes'][name]['latencies_ms']],.5),
            'p95_ms':quantile([v for e in entries if e['label']==label for v in e['outcomes'][name]['latencies_ms']],.95)} for name in ('full_success','degraded','rejected','unclassified')} for label in 'AB'}
        comparisons.append({'scenario':scenario,'concurrency':concurrency,'warmup_requests_per_run':int(scenario in HOT),
                            'run_count':len(entries),'outcome_totals':outcome_totals,'pooled_outcome_latencies':pooled,
                            'metrics':{name:paired(entries,name,args.rounds) for name in names}})
    report={'schema':1,'input':str(path),'input_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'comparison_valid':not errors,'validation_errors':errors,'warnings':warnings,'heads':label_heads,
        'run_count':len(runs),'expected_rounds':args.rounds,'shared_metadata':baseline_metadata,
        'source_stable_within_version':not any('source/build fingerprint changed' in e for e in errors),
        'source_changes_between_versions':source_diff,'manifest_fingerprints':manifests,
        'outcome_totals':{label:dict(counts) for label,counts in all_counts.items()},
        'method':'Each scenario/concurrency compares two A and two B process runs per round. Center is their mean; the paired effect is B minus A. Report median of five paired effects and changes. Variability is max(abs(A0-A1),abs(B0-B1)) per round. Consistent beyond variability requires five same-sign effects whose median exceeds median variability. This descriptive rule is not a confidence interval; values from incomplete comparisons are not evidence of a change.',
        'scope_notes':[
            'Full success includes intended plain-text knowledge explanations with successful business outcomes; degraded excludes help and these knowledge explanations. Rejected means no delivered parts, including admitted failures, not necessarily capacity rejection. Unclassified is every remaining sample; categories are exhaustive and disjoint.',
            'Request counts, full-success p50/p95, request throughput, completed_images and uploads exclude warmup. p50/p95 are undefined (null) if there are no full successes; stored zero is not a latency measurement.',
            'Hot, mixed and ask-mixed each execute one warmup through the full delivery/ACK path. Frontend timings, admission counters, backend counters and inclusive stages include that warmup. Backend conversion counters also include repo.load startup; worker instrumentation starts after repo.load.',
            'Stage times are inclusive and may overlap/nest. Do not sum stages as total latency. Admission timing count is accessor observations, not an asserted one-to-one request count; worker wait mean is total/count, no worker-wait percentile was recorded.',
            'External process CPU is sampled lifetime cumulative CPU, not CPU restricted to timed requests; RSS is maximum observed current RSS at 25ms intervals on Linux. Python includes startup/load and both processes include warmup and cleanup/report windows. Short spikes can be missed.',
            'Across versions source/build changes are expected and listed; within each label fingerprints must remain identical. Shared inputs/fonts/harness/lockfile, declared dependency versions, workload args and cgroup caps must be identical. Python metadata records Pillow/aiohttp/qq-botpy, not a complete environment freeze; Node is version plus package-lock hash, not an installed dependency tree audit.',
            'All results use fixed local artwork/chart/source/provider/platform fixtures. Knowledge cases run the actual ModelRuntime, scheduler, permissions and usage ledger with a synthetic 20ms provider, 50 approved engineering documents and per-request fixture users. No real QQ/provider/production throughput or overall speedup claim follows from these cases.'],
        'comparisons':comparisons,'runs':flattened}
    prefix.parent.mkdir(parents=True,exist_ok=True)
    prefix.with_suffix('.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    lines=['真实链路 ABBA 汇总',f'comparison_valid={not errors}; runs={len(runs)}; expected={args.rounds*4}',
           'heads='+json.dumps(label_heads,ensure_ascii=False),'totals='+json.dumps(report['outcome_totals'],ensure_ascii=False),
           '校验错误: '+json.dumps(errors,ensure_ascii=False),'警告: '+json.dumps(warnings,ensure_ascii=False),'']
    for c in comparisons:
        lines.append(f"{c['scenario']} c{c['concurrency']} — warmup每次{c['warmup_requests_per_run']}；outcomes="+json.dumps(c['outcome_totals'],ensure_ascii=False))
        wanted=['p50_ms','p95_ms','successful_throughput','python_process.cpu_seconds','node_process.cpu_seconds',
                'python_process.peak_rss_bytes','node_process.peak_rss_bytes','stages_including_warmup.admission_wait.mean_ms',
                'stages_including_warmup.admission_wait.p95_ms','backend_stages_including_setup_warmup.worker_wait.mean_ms',
                'backend_stages_including_setup_warmup.worker_wait.total_ms']
        wanted += [k for k in c['metrics'] if k.startswith('backend_counters_')]
        for name in wanted:
            if name not in c['metrics']:continue
            m=c['metrics'][name]
            lines.append(f"  {name}: A={m['A']['median']} B={m['B']['median']}; paired={m['median_paired_change_percent']}%; delta={m['median_paired_delta']}; variability={m['median_within_round_difference']}; {m['classification']}")
        lines.append('')
    lines+=['口径与限制:']+['- '+note for note in report['scope_notes']]
    prefix.with_suffix('.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps({'comparison_valid':not errors,'errors':len(errors),'run_count':len(runs),'output':str(prefix)},ensure_ascii=False))
    return 0 if not errors else 2


if __name__ == '__main__':
    sys.exit(main())
