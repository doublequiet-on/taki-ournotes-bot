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
    manifest=json.loads((combo/'combination.json').read_text())
    if manifest['commit']!=commit: raise RuntimeError('combination identity')
    try:
        from .group_policy_guard import verify_start
    except ImportError:
        from group_policy_guard import verify_start
    directory=base/'console-state'
    if os.environ.get('TAKI_CONSOLE_STATE_DIR',str(directory)) != str(directory):
        raise RuntimeError('policy console state mismatch')
    os.environ['TAKI_CONSOLE_STATE_DIR']=str(directory)
    supported=manifest.get('capabilities') in (['group_policies_v1'],['group_policies_v1','query_chat_contract_v1'])
    if not supported and (directory/'group-policies.v1.json').exists() and os.environ.get('TAKI_CONSOLE_ENABLED') != '1':
        raise RuntimeError('legacy policy freeze requires enabled management')
    verify_start(directory,permit.get('policy_guard'),supported,commit)
    os.environ['TAKI_POLICY_RECOVERY_STATE']=permit['policy_guard']['state']
    os.environ['TAKI_POLICY_RECOVERY_REASON']=permit['policy_guard'].get('reason','')
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
        for name in ('AI_API_KEY','OURNOTES_QUERY_AI_API_KEY','OURNOTES_AI_PROFILE_FILE','MOENOTES_OPEN_SECRET','BDON_OPENPLATFORM'): os.environ.pop(name,None)
        args=[str(combo/'bin/node'),str(combo/'gateway/run.cjs')]
    os.execv(args[0],args)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base',type=Path,required=True);p.add_argument('role',choices=['backend','gateway'])
    a=p.parse_args();launch(a.base,a.role)
