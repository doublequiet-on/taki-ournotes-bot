"""Root-owned, read-only host collector. Only output/cache files are writable.

Install separately beside the reviewed controller. Never runs check/recover/resume,
starts services, reads environment credentials or imports application payloads.
"""
from __future__ import annotations
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

UNITS = ('taki.service','taki-gateway.service','taki-updater.timer','taki-updater.service','taki-updater-recover.service')
OBJECTS = ('code_environment','business_data','console_settings','trusted_configuration','isolated_restore','offsite_copy')
SHA = re.compile(r'[a-f0-9]{40}')


def commit(value):
    return value if isinstance(value,str) and SHA.fullmatch(value) else None


def safe_code(value):
    return value if isinstance(value,str) and re.fullmatch(r'[a-zA-Z0-9_.:-]{1,100}',value) else None


def project_controller(row):
    if row.get('status')=='uninitialized':
        return {'availability':'unavailable','reason_code':'controller_uninitialized'}
    transaction = row.get('transaction') or {}
    target = commit(row.get('observed_target'))
    retry = row.get('download_retries',{}).get(target,{})
    result = {'current_sha':commit((row.get('current') or {}).get('commit')),
              'previous_sha':commit((row.get('previous') or {}).get('commit')),
              'observed_target':target,'paused':bool(row.get('paused')),'paused_reason':safe_code(row.get('paused')),
              'phase':safe_code(transaction.get('phase')) if transaction else 'idle','preparation_step':safe_code(transaction.get('preparation_step')),
              'retry_after':retry.get('after') if type(retry.get('after')) in (int,float) else None,
              'blocked_reason':safe_code((row.get('failed',{}).get(target) or {}).get('reason')),
              'download':None}
    download = row.get('download')
    if isinstance(download,dict):
        result['download'] = {k:download.get(k) for k in ('stage','route','received_bytes','expected_bytes','at')}
    return result


def process_release(cwd):
    for prefix in ('/opt/taki-combinations/','/home/ubuntu/taki/releases/'):
        if cwd.startswith(prefix):
            return commit(cwd[len(prefix):].split('/',1)[0])
    return None


def trusted_json(path, limit=131072):
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_mode & 0o022 or info.st_size>limit:
        raise ValueError('untrusted_snapshot_input')
    value=json.loads(path.read_text('utf-8'))
    if not isinstance(value,dict):raise ValueError('snapshot_input_shape')
    return value


def coverage(manifests, backup_root, receipt=None):
    rows={name:{'object':name,'archive_exists':None,'checksum_verified':None,'restore_verified':None,
                'evidence_at':None,'reason_code':'evidence_not_collected'} for name in OBJECTS}
    for manifest in manifests:
        for kind,key in (('code_environment','archive'),('business_data','data_archive')):
            name=manifest.get(key)
            if manifest.get('owner')!='taki-linux-updater-v1' or not isinstance(name,str) or Path(name).name!=name or not name.endswith('.tar'):
                continue
            file=backup_root/name
            if not file.is_symlink() and file.is_file():
                rows[kind].update(archive_exists=True,reason_code='archive_exists_not_verified')
    # Receipts are separately produced by approved backup/restore procedures.
    # They are evidence, not a claim that this collector performed verification.
    if receipt and receipt.get('schema')==1 and isinstance(receipt.get('objects'),dict):
        for name in OBJECTS:
            proof=receipt['objects'].get(name)
            if not isinstance(proof,dict):continue
            if not isinstance(proof.get('artifact_sha256'),str) or not re.fullmatch('[a-f0-9]{64}',proof['artifact_sha256']):continue
            if type(proof.get('verified_at')) not in (int,float) or not 0<proof['verified_at']<=time.time()+300:continue
            rows[name].update(checksum_verified=True,evidence_at=proof['verified_at'],
                restore_verified=proof.get('restore_verified') if type(proof.get('restore_verified')) is bool else None,reason_code='trusted_receipt_only',
                artifact_sha256=proof['artifact_sha256'])
    return list(rows.values())


def directory_size(root, deadline):
    total,complete=0,True
    pending=[root]
    while pending:
        if time.monotonic()>=deadline:return {'bytes':total,'complete':False}
        folder=pending.pop()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    if time.monotonic()>=deadline:return {'bytes':total,'complete':False}
                    if entry.is_symlink():continue
                    if entry.is_dir(follow_symlinks=False):pending.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False):total+=entry.stat(follow_symlinks=False).st_size
        except OSError:
            complete=False
    return {'bytes':total,'complete':complete}


def collect(controller, base, state_dir, output_dir):
    now=time.time()
    command=['/usr/bin/systemctl','show',*UNITS,'--no-pager',
             '--property=Id,ActiveState,SubState,MainPID,InvocationID,UnitFileState,NextElapseUSecRealtime,LastTriggerUSec']
    response=subprocess.run(command,capture_output=True,text=True,timeout=5,check=False)
    if len(response.stdout)>65536:raise ValueError('service_snapshot_size')
    services={}
    for block in response.stdout.split('\n\n'):
        values=dict(line.split('=',1) for line in block.splitlines() if '=' in line)
        ident=values.pop('Id',None)
        if ident in UNITS:services[ident]=values
    processes={}
    for name,unit in (('backend','taki.service'),('gateway','taki-gateway.service')):
        pid=services.get(unit,{}).get('MainPID','0')
        release=None
        if re.fullmatch('[1-9][0-9]*',pid):
            try:release=process_release(os.readlink('/proc/'+pid+'/cwd'))
            except OSError:pass
        processes[name]={'pid':int(pid) if pid.isdigit() else None,'cwd_release_sha':release,
                         'evidence':'proc_cwd_not_module_attestation'}
    disks={}
    for name,path in (('releases',base/'releases'),('backups',state_dir/'backups')):
        try:
            disk=os.statvfs(path);free=disk.f_bavail*disk.f_frsize
            disks[name]={'free_bytes':free,'minimum_update_bytes':4*1024**3,'meets_minimum':free>=4*1024**3,'observed_at':now}
        except OSError:
            disks[name]={'free_bytes':None,'meets_minimum':None,'reason_code':'directory_not_available','observed_at':now}
    sizes_file=output_dir/'sizes-v1.json'
    try:sizes=trusted_json(sizes_file)
    except (OSError,ValueError):sizes={}
    if not sizes or now-sizes.get('observed_at',0)>=600:
        deadline=time.monotonic()+2
        paths={'versions':base/'releases','bundles':state_dir/'bundle-cache','business_data':base/'data',
               'assets':base/'data/haneoka-asset-cache','backups':state_dir/'backups'}
        sizes={'observed_at':now,'directories':{name:directory_size(path,deadline) for name,path in paths.items()}}
        atomic_output(sizes_file,sizes)
    backup_root=state_dir/'backups';manifests=[]
    for file in sorted(backup_root.glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:8]:
        try:manifests.append(trusted_json(file))
        except (OSError,ValueError):pass
    try:receipt=trusted_json(state_dir/'ops-recovery-proof.v1.json')
    except (OSError,ValueError):receipt=None
    return {'schema':'taki-host-ops-v1','observed_at':now,'controller':project_controller(controller),
            'services':services,'processes':processes,
            'disk':disks,
            'sizes':sizes,'backup_coverage':coverage(manifests,backup_root,receipt)}


def atomic_output(path,value):
    temporary=path.with_suffix('.tmp')
    with temporary.open('w',encoding='utf-8') as stream:
        json.dump(value,stream);stream.flush();os.fsync(stream.fileno())
    os.chmod(temporary,0o640)
    os.replace(temporary,path)


def main():
    sys.dont_write_bytecode=True
    if sys.platform!='linux' or os.geteuid()!=0:raise ValueError('collector_requires_root_linux')
    control=Path('/usr/local/lib/taki-updater')
    if Path(__file__).resolve().parent!=control:raise ValueError('collector_not_installed')
    for folder in (control,control.parent,control.parent.parent):
        info=folder.lstat()
        if info.st_uid!=0 or info.st_mode&0o022 or not stat.S_ISDIR(info.st_mode):raise ValueError('untrusted_controller_directory')
    for name in ('ops_snapshot.py','linux_backend.py','linux_updater.py','offline_bundle.py'):
        info=(control/name).lstat()
        if info.st_uid!=0 or info.st_mode&0o022 or not stat.S_ISREG(info.st_mode):raise ValueError('untrusted_collector')
    sys.path.insert(0,str(control))
    from linux_backend import trusted,validate_config,status_document
    from linux_updater import Engine
    config=trusted_json(Path('/etc/taki-updater.json'));validate_config(config)
    trusted(Path(config['state_dir']),directory=True)
    output=Path('/run/taki-ops');info=output.lstat()
    if info.st_uid!=0 or info.st_mode&0o022 or not stat.S_ISDIR(info.st_mode):raise ValueError('untrusted_output_directory')
    row=collect(status_document(Engine(config['state_dir'],backend=None)),Path(config['base']),Path(config['state_dir']),output)
    atomic_output(output/'host-v1.json',row)


if __name__=='__main__':
    main()
