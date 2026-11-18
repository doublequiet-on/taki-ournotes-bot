"""Run the frozen panel with the second, behavioral measurement of two case families."""
import argparse
from collections import Counter
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import sys
import unittest

# -I omits the script directory. Import only this evaluator's shared driver.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from run import BASE, FIXTURES, fingerprint, forbid_network, git, measure


def trial(case, method, *, link=False):
    from behavior import BehaviorProbe
    probe = BehaviorProbe(method)
    probe.case, probe.link, probe.observed = case, link, {}
    result = unittest.TestResult()
    probe.run(result)
    row = dict(status='pass', observed=probe.observed)
    if result.errors or result.failures:
        error = (result.errors or result.failures)[0][1]
        print(error, file=sys.stderr)
        row.update(status='undetermined' if result.errors else 'fail',
                   reason=next((line for line in error.splitlines() if line.startswith('AssertionError')),
                               error.strip().splitlines()[-1]))
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--cases', default=HERE / 'cases.json', type=Path)
    parser.add_argument('--official-sample', type=Path)
    parser.add_argument('--compare', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    output = args.output.resolve()
    if args.official_sample:
        args.official_sample = args.official_sample.resolve()
    if Path(git(repo, 'rev-parse', '--show-toplevel')).resolve() != repo:
        parser.error('expected a checkout root')
    source = git(repo, 'rev-parse', 'HEAD')
    if git(repo, 'status', '--porcelain', '--untracked-files=all', '--', 'src'):
        parser.error('dirty source')
    if git(FIXTURES, 'diff', BASE, '--name-only', '--', 'tests', 'deploy'):
        parser.error('fixture checkout differs from base')
    cases = json.loads(args.cases.read_text(encoding='utf-8'))
    ids = [case['id'] for case in cases]
    if not ids or len(set(ids)) != len(ids):
        parser.error('empty or duplicate case IDs')
    conditions = dict(fixture_base=BASE, panel=fingerprint(args.cases), python=platform.python_version(),
                      official_sample=bool(args.official_sample),
                      scripts={name: fingerprint(HERE / name) for name in
                               ('run.py', 'probes.py', 'run_behavior.py', 'behavior.py')},
                      dependencies={name: version(name) for name in ('httpx', 'aiohttp', 'Pillow', 'qq-botpy')})
    comparison = json.loads(args.compare.read_text(encoding='utf-8')) if args.compare else None
    if comparison and (comparison['conditions'] != conditions or [r['id'] for r in comparison['cases']] != ids):
        parser.error('different cases, driver, dependencies or source settings')
    sys.path[:0] = [str(repo / 'src'), str(FIXTURES / 'tests'), str(FIXTURES)]
    os.chdir(FIXTURES)
    sys.addaudithook(forbid_network)
    from ournotes_bot.domain.chat import TurnBudget
    budget = TurnBudget()
    if (budget.model_calls, budget.tool_calls, budget.execution_seconds, budget.final_seconds) != (6, 8, 60, 5):
        parser.error('changed budget')
    rows = []
    for case in cases:
        if case['kind'] == 'coverage':
            row = dict(id=case['id'], **trial(case, 'coverage', link=True))
            row['without_binding'] = trial(case, 'coverage')
        elif case['kind'] == 'chapters':
            row = dict(id=case['id'], **trial(case, 'chapters'))
        else:
            row = measure(case, args)
        rows.append(row)
    if git(repo, 'rev-parse', 'HEAD') != source or git(repo, 'status', '--porcelain', '--untracked-files=all', '--', 'src'):
        parser.error('source changed during the run')
    totals = dict(Counter(row['status'] for row in rows))
    report = dict(source=source, conditions=conditions, totals=totals, cases=rows)
    if comparison:
        report.update(compared_to=comparison['source'],
                      changed_ids=[r['id'] for p, r in zip(comparison['cases'], rows) if p != r])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(source, totals)


if __name__ == '__main__':
    main()
