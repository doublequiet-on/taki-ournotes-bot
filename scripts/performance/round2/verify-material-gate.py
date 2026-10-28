"""Offline proof of the existing trusted gate against actual material source bytes."""
import argparse, hashlib, json, subprocess, sys, tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

parser=argparse.ArgumentParser()
parser.add_argument('--repo', required=True)
parser.add_argument('--controller', required=True)
parser.add_argument('--old', required=True)
parser.add_argument('--a', required=True)
parser.add_argument('--b', required=True)
args=parser.parse_args()
sys.path.insert(0,args.controller)
from deploy.linux_backend import LinuxBackend, fingerprints
from deploy.linux_updater import Rejected
paths=['koishi/src/materials.ts']
revisions={name:subprocess.check_output(['git','rev-parse',value],cwd=args.repo,text=True).strip()
           for name,value in [('old',args.old),('a',args.a),('b',args.b)]}
with tempfile.TemporaryDirectory(prefix='taki-material-gate-') as temp:
    root=Path(temp).resolve(); releases={}
    for name,sha in revisions.items():
        code=root/name; file=code/paths[0]; file.parent.mkdir(parents=True)
        file.write_bytes(subprocess.check_output(['git','show',sha+':'+paths[0]],cwd=args.repo))
        releases[name]=SimpleNamespace(code_dir=str(code))
    backend=LinuxBackend.__new__(LinuxBackend)
    policy=root/'policy.json'
    backend.config={'compatibility_paths':paths,'compatibility_policy':str(policy)}
    for field in ('compatibility_gate','compatibility_fixture'):
        file=root/field; file.write_text('frozen offline fixture',encoding='utf-8')
        backend.config[field]=str(file)
        backend.config[field+'_sha256']=hashlib.sha256(file.read_bytes()).hexdigest()
    observed={name:fingerprints(Path(release.code_dir),paths) for name,release in releases.items()}
    def install(contracts):
        policy.write_text(json.dumps({'version':2,'critical_paths':paths,'reviews':[
            {'fingerprints':observed[name],'forward_and_rollback_data_compatible':True,
             'schema_contract':contract,'evidence':'paired actual compiled A/B round2 fixture'}
            for name,contract in contracts.items()]}),encoding='utf-8')
    def outcome(previous,candidate):
        try:
            # Only POSIX root ownership is replaced; fingerprint and contract checks execute unchanged.
            with patch('deploy.linux_backend.trusted'):
                backend._check_reviewed_fingerprints(releases[previous],releases[candidate])
            return 'allowed'
        except Rejected as error:
            return error.code
    install({'old':'materials-v1','a':'materials-v1'})
    phase_a={'old_to_a':outcome('old','a'),'a_to_old':outcome('a','old'),'old_to_b':outcome('old','b')}
    assert phase_a=={'old_to_a':'allowed','a_to_old':'allowed','old_to_b':'compatibility_review_required'},phase_a
    install({'old':'materials-v1','a':'materials-v2','b':'materials-v2'})
    phase_b={pair:outcome(*pair.split('_to_')) for pair in ['a_to_b','b_to_a','old_to_b','old_to_a']}
    assert phase_b=={'a_to_b':'allowed','b_to_a':'allowed','old_to_b':'persistence_contract_changed','old_to_a':'persistence_contract_changed'},phase_b
    report={'scope':'offline actual source fingerprints; POSIX ownership mocked; no installed policy modified',
            'revisions':revisions,'critical_paths_addition':paths,'material_fingerprints':observed,'phase_a':phase_a,'phase_b':phase_b,
            'deployment_prerequisite':'Merge this path into the complete trusted path set, recalculate every reviewed fingerprint, verify A actually running before contract rotation. This fixture is not a replacement production policy.'}
    Path(__file__).with_suffix('.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))
