"""Trusted-controller policy freeze/restore. Never rolls back business data.

The journal belongs in the controller state directory, outside app-writable state.
All callers must stop both processes before mutating configuration.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

FIELDS = {'queriesEnabled','privateEnabled','groupsEnabled','groupAllowlist','notificationsEnabled'}
DEFAULT = dict(queriesEnabled=True,privateEnabled=True,groupsEnabled=True,groupAllowlist=[],notificationsEnabled=True)
# Reviewed 1.0 runtimes with the original five-field query/notice enforcement.
# Unknown old runtimes remain stopped; a protocol number alone cannot prove enforcement.
LEGACY_FREEZE_COMMITS = frozenset({
    'b3d7c4267f8dd304ff0ce59dde2d9e3ff287c35a',
    '5e96af27680e5d7cbdbfab01de8cd21171c45d66',
    'd11561747d2aee8aebb6b94bed54594be7105f4a',
})


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read(path, limit=131072):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_size > limit:
        raise ValueError('policy_guard_file')
    return path.read_bytes(), info


def settings(data):
    value = json.loads(data)
    if not isinstance(value, dict) or set(value) != FIELDS or any(type(value[k]) is not bool for k in FIELDS-{'groupAllowlist'}):
        raise ValueError('policy_guard_settings')
    ids = value['groupAllowlist']
    import re
    if not isinstance(ids,list) or len(ids)>128 or any(not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',v) for v in ids) or len(set(ids)) != len(ids):
        raise ValueError('policy_guard_settings')
    return value


def atomic(path, data, attributes=None):
    path = Path(path)
    if path.exists() and path.is_symlink():
        raise ValueError('policy_guard_symlink')
    temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    descriptor = os.open(temporary, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
    try:
        if attributes:
            if hasattr(os, 'fchown'):
                os.fchown(descriptor, attributes['uid'], attributes['gid'])
            os.fchmod(descriptor, attributes['mode']) if hasattr(os,'fchmod') else os.chmod(temporary,attributes['mode'])
        with os.fdopen(descriptor,'wb') as stream:
            descriptor = None
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary,path)
        if os.name != 'nt':
            directory = os.open(path.parent,os.O_RDONLY)
            try: os.fsync(directory)
            finally: os.close(directory)
    finally:
        if descriptor is not None: os.close(descriptor)
        temporary.unlink(missing_ok=True)


def save_journal(path, record):
    atomic(path,(json.dumps(record,sort_keys=True)+'\n').encode())


def transition(directory, journal, supported):
    """Return frozen/restored/current/conflict; any unsafe old start raises."""
    directory,journal = Path(directory),Path(journal)
    if directory.resolve() != directory.absolute():
        raise ValueError('policy_guard_directory')
    policy,config = directory/'group-policies.v1.json',directory/'settings.json'
    if journal.exists():
        raw,_ = read(journal,393216)
        record = json.loads(raw)
        if not isinstance(record,dict) or record.get('schema') != 1 or record.get('directory') != str(directory):
            raise ValueError('policy_guard_journal')
    else:
        record = None
    if supported:
        if not record or record['phase'] == 'restored':
            return {'state':'current','supported':True,'directory':str(directory)}
        current,_ = read(config)
        if not policy.exists() or digest(read(policy)[0]) != record['policy_sha256']:
            return {'state':'conflict','supported':True,'directory':str(directory),'reason':'policy_changed'}
        original = base64.b64decode(record['original'],validate=True)
        if digest(original) != record['original_sha256']:
            raise ValueError('policy_guard_journal_digest')
        if digest(current) == record['frozen_sha256']:
            atomic(config,original,record['attributes'])
        elif digest(current) != record['original_sha256']:
            return {'state':'conflict','supported':True,'directory':str(directory),'reason':'settings_changed'}
        record['phase']='restored';save_journal(journal,record)
        return {'state':'restored','supported':True,'directory':str(directory)}
    if not policy.exists():
        if record and record['phase'] != 'restored':
            raise ValueError('policy_guard_policy_missing')
        return {'state':'current','supported':False,'directory':str(directory)}
    policy_data,policy_info = read(policy)
    try: original,info = read(config)
    except FileNotFoundError:
        original=(json.dumps(DEFAULT)+'\n').encode();info=policy_info
    value=settings(original)
    if record and record['phase'] != 'restored':
        if digest(policy_data) != record['policy_sha256']:
            raise ValueError('policy_guard_policy_changed')
        if digest(original) == record['frozen_sha256']:
            return {'state':'frozen','supported':False,'directory':str(directory),'settings_sha256':record['frozen_sha256']}
        if record['phase'] != 'prepared' or digest(original) != record['original_sha256']:
            raise ValueError('policy_guard_settings_changed')
        frozen=base64.b64decode(record['frozen'],validate=True)
    else:
        frozen=(json.dumps({**value,'queriesEnabled':False,'notificationsEnabled':False},indent=2)+'\n').encode()
        record={'schema':1,'phase':'prepared','directory':str(directory),
            'original':base64.b64encode(original).decode(),'original_sha256':digest(original),
            'frozen':base64.b64encode(frozen).decode(),'frozen_sha256':digest(frozen),
            'policy':base64.b64encode(policy_data).decode(),'policy_sha256':digest(policy_data),
            'attributes':{'uid':info.st_uid,'gid':info.st_gid,'mode':stat.S_IMODE(info.st_mode)}}
        journal.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        save_journal(journal,record)
    atomic(config,frozen,record['attributes'])
    if digest(read(config)[0]) != record['frozen_sha256']:
        raise ValueError('policy_guard_freeze_unconfirmed')
    record['phase']='frozen';save_journal(journal,record)
    return {'state':'frozen','supported':False,'directory':str(directory),'settings_sha256':record['frozen_sha256']}


def verify_start(directory, permit, supported, commit=None):
    """Read-only launcher check, including systemd's independent process restart."""
    directory = Path(directory)
    if not isinstance(permit,dict) or permit.get('directory') != str(directory) or permit.get('supported') is not supported:
        raise ValueError('policy_guard_permit')
    if not supported and (directory/'group-policies.v1.json').exists():
        if commit is not None and (commit not in LEGACY_FREEZE_COMMITS or permit.get('legacy_commit') != commit):
            raise ValueError('policy_guard_legacy_unverified')
        data,_ = read(directory/'settings.json')
        value=settings(data)
        if value['queriesEnabled'] or value['notificationsEnabled'] or digest(data) != permit.get('settings_sha256'):
            raise ValueError('policy_guard_not_frozen')
