"""Reviewed launcher template, installed by the administrator; never auto-installs."""
import argparse
import json
import os
from pathlib import Path
import re


def launch(base, role):
    base=base.resolve(strict=True)
    code=(base/'current').resolve(strict=True)
    permit=json.loads(Path('/run/taki-updater/allowed.json').read_text())
    if permit['boot_id']!=Path('/proc/sys/kernel/random/boot_id').read_text().strip() or Path(permit['release']['code_dir'])!=code:
        raise RuntimeError('missing current boot start permit')
    commit=permit['release']['commit']
    if not re.fullmatch('[a-f0-9]{40}',commit): raise RuntimeError('release identity')
    combo=Path('/opt/taki-combinations')/commit
    if json.loads((combo/'combination.json').read_text())['commit']!=commit: raise RuntimeError('combination identity')
    os.chdir(code)
    os.environ['PYTHONPATH']=str(code/'src')
    os.environ['OURNOTES_RELEASE_SOURCE']=str(code)
    os.environ['OURNOTES_DEPLOYMENT_STATE']='/run/taki-updater/notice.json'
    if role=='backend':
        # The controller prepares artwork under this managed base. An inherited
        # legacy EnvironmentFile must not send queries to a second, cold cache.
        os.environ['OURNOTES_RUNTIME_DATA_DIR']=str(base/'data')
        os.environ.pop('QQ_APP_SECRET',None)
        python=Path(permit['release']['venv_dir'])/'bin/python'
        args=[str(python),'-B','-m','ournotes_bot.main','backend','--instance-id',os.environ['TAKI_INSTANCE_ID'],
              '--app-id',os.environ['QQ_APP_ID'],'--state-dir',str(base/'data/delivery'), '--port',os.environ.get('TAKI_BACKEND_PORT','8765')]
    else:
        for name in ('AI_API_KEY','MOENOTES_OPEN_SECRET','BDON_OPENPLATFORM'): os.environ.pop(name,None)
        args=[str(combo/'bin/node'),str(combo/'gateway/run.cjs')]
    os.execv(args[0],args)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base',type=Path,required=True);p.add_argument('role',choices=['backend','gateway'])
    a=p.parse_args();launch(a.base,a.role)
