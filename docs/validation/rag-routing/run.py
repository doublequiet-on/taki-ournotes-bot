"""Run the fixed Taki routing panel against the src tree of one Git checkout."""
import argparse
from collections import Counter
from hashlib import sha256
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import unittest

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parents[2]
BASE = 'e72eb9cbe1557966e8121034c6aca870d1f5cf2d'


def git(repo, *args):
    return subprocess.check_output(['git', *args], cwd=repo, text=True).strip()


def fingerprint(path):
    return sha256(path.read_text(encoding='utf-8').encode('utf-8')).hexdigest()


def forbid_network(event, args):
    # Windows implements asyncio's internal socketpair through loopback TCP.
    if event == 'socket.connect' and sys._getframe(1).f_globals.get('__name__') == 'socket':
        if sys._getframe(1).f_code.co_name == 'socketpair' and args[1][0] in ('127.0.0.1', '::1'):
            return
    if event in ('socket.connect', 'socket.getaddrinfo', 'socket.sendto'):
        raise RuntimeError('network disabled in offline evaluation')


def measure(case, args):
    from probes import ChatProbe, OfficialSummaryProbe, RuleProbe
    row = {'id': case['id']}
    if case['kind'] == 'unscored':
        return dict(row, status='undetermined', reason=case['reason'])
    if case['kind'] == 'contract':
        suite = unittest.defaultTestLoader.loadTestsFromName(case['test'])
        if suite.countTestCases() != 1:
            raise ValueError('expected one frozen fixture case')
    else:
        cls = {'summary': OfficialSummaryProbe, 'rule': RuleProbe, 'context': ChatProbe,
               'chapters': ChatProbe, 'coverage': ChatProbe}[case['kind']]
        suite = cls('measure')
        suite.case = case
        suite.sample = args.official_sample
        suite.observed = row['observed'] = {}
    result = unittest.TestResult()
    suite.run(result)
    if result.errors or result.failures:
        error = (result.errors or result.failures)[0][1]
        print(error, file=sys.stderr)
        return dict(row, status='undetermined' if result.errors else 'fail',
                    reason=next((line for line in error.splitlines() if line.startswith('AssertionError')),
                                error.strip().splitlines()[-1]))
    if result.skipped:
        return dict(row, status='undetermined', reason=result.skipped[0][1])
    return dict(row, status='pass')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True, type=Path)
    parser.add_argument('--cases', type=Path, default=HERE / 'cases.json')
    parser.add_argument('--official-sample', type=Path)
    parser.add_argument('--compare', type=Path, help='previous report for the identical panel')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    if Path(git(repo, 'rev-parse', '--show-toplevel')).resolve() != repo:
        parser.error('--repo must be a Taki checkout root')
    source = git(repo, 'rev-parse', 'HEAD')
    if git(repo, 'status', '--porcelain', '--untracked-files=all', '--', 'src'):
        parser.error('commit changes under the measured src tree before running')
    if git(FIXTURES, 'diff', BASE, '--name-only', '--', 'tests', 'deploy'):
        parser.error('use an evaluator checkout with the frozen tests and deploy helpers')
    cases = json.loads(args.cases.read_text(encoding='utf-8'))
    ids = [case['id'] for case in cases]
    if not ids or len(set(ids)) != len(ids):
        parser.error('empty or duplicate case IDs')
    conditions = dict(fixture_base=BASE, panel=fingerprint(args.cases),
                      driver=fingerprint(HERE / 'run.py'), probes=fingerprint(HERE / 'probes.py'),
                      python=platform.python_version(), official_sample=bool(args.official_sample),
                      dependencies={name: version(name) for name in ('httpx', 'aiohttp', 'Pillow', 'qq-botpy')})
    previous = None
    if args.compare:
        previous = json.loads(args.compare.read_text(encoding='utf-8'))
        if previous['conditions'] != conditions or [r['id'] for r in previous['cases']] != ids:
            parser.error('comparison requires identical fixtures, driver, inputs, Python and sample settings')

    sys.path[:0] = [str(repo / 'src'), str(FIXTURES / 'tests'), str(FIXTURES), str(HERE)]
    sys.addaudithook(forbid_network)
    # Some frozen fixtures open auxiliary repository files using relative paths.
    # Resolve caller-supplied paths before entering their checkout.
    output = args.output.resolve()
    if args.official_sample:
        args.official_sample = args.official_sample.resolve()
    os.chdir(FIXTURES)
    from ournotes_bot.domain.chat import TurnBudget
    budget = TurnBudget()
    if (budget.model_calls, budget.tool_calls, budget.execution_seconds, budget.final_seconds) != (6, 8, 60, 5):
        parser.error('candidate default turn budget differs from the frozen baseline')
    rows = [measure(case, args) for case in cases]
    if source != git(repo, 'rev-parse', 'HEAD') or git(repo, 'status', '--porcelain', '--untracked-files=all', '--', 'src'):
        parser.error('measured source changed during evaluation')
    report = dict(source=source, conditions=conditions,
                  totals=dict(Counter(row['status'] for row in rows)), cases=rows)
    if previous is not None:
        report['compared_to'] = previous['source']
        report['changes'] = [dict(id=r['id'], before=p, after=r)
                             for p, r in zip(previous['cases'], rows) if p != r]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(report['source'], report['totals'])


if __name__ == '__main__':
    main()
