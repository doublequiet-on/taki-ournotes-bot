"""Reviewable trusted-action protocol core; no installer, shell, or default executor.

A separately installed root service supplies the fixed deployment adapter. It must
use the existing updater's CI/combination/compatibility, backup and recovery gates.
The application only receives the public protocol. Tests inject a non-operating adapter.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid
try:
    from .linux_updater import FileLock
except ImportError:
    from linux_updater import FileLock


def require(ok, code):
    if not ok: raise ValueError(code)


def live_guard(proof):
    return (isinstance(proof,dict) and set(proof)=={'gates','maintenance','inflight','frozen'}
            and proof['gates'] is True and proof['maintenance'] is True and proof['frozen'] is False
            and type(proof['inflight']) is int and proof['inflight']==0)


class TrustedActions:
    def __init__(self, state, instance, adapter, *, clock=time.time):
        self.path=Path(state);self.instance=instance;self.adapter=adapter;self.clock=clock
        self.lock=threading.RLock();self.plans={};self.entries=[];self.worker=None
        self.owner=FileLock(self.path.with_suffix('.lock'));self.owner.__enter__()
        try: self.restore()
        except BaseException:
            self.owner.__exit__();raise

    def restore(self):
        if self.path.exists():
            require(not self.path.is_symlink() and self.path.stat().st_size<=1_000_000,'action_state_invalid')
            value=json.loads(self.path.read_text('utf-8'))
            require(value['schema']==1 and value['instance_id']==self.instance and len(value['entries'])<=200,'action_state_invalid')
            self.entries=value['entries']
            for entry in self.entries:
                if entry['state'] in ('intent','running'): entry['state']='unknown'
            self.save()  # Crash uncertainty is durable; never replay a process operation.

    def close(self):
        if self.worker: self.worker.join()
        self.owner.__exit__()

    def save(self):
        temporary=self.path.with_suffix('.tmp')
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as stream:
                json.dump({'schema':1,'instance_id':self.instance,'entries':self.entries},stream,separators=(',',':'))
                stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,self.path)
            if os.name!='nt':
                fd=os.open(self.path.parent,os.O_RDONLY)
                try: os.fsync(fd)
                finally: os.close(fd)
        finally: temporary.unlink(missing_ok=True)

    def status(self):
        with self.lock:
            return json.loads(json.dumps({'schema':'taki-trusted-actions/1','instance_id':self.instance,
                    'plans':[dict(p) for p in self.plans.values() if p['expires_at']>self.clock()],
                    'items':[{k:e[k] for k in ('operation_id','state','health')} for e in self.entries[-25:]][::-1]}))

    def prepare(self, value):
        require(isinstance(value,dict) and set(value)=={'action','instance_id'} and value['instance_id']==self.instance
                and value['action'] in ('restart','update'),'action_shape')
        with self.lock:
            require(not any(e['state'] in ('intent','running','unknown') for e in self.entries),'action_unresolved')
            # No target, command, URL or arbitrary config comes from the caller.
            proposal=self.adapter.propose(value['action'])
            require(set(proposal)=={'from_sha','target_sha','config_digest','backend_digest','gates','before_pids'},'action_proposal')
            require(all(isinstance(proposal[k],str) and re.fullmatch('[a-f0-9]{40}',proposal[k]) for k in ('from_sha','target_sha')),'action_version')
            require(all(isinstance(proposal[k],str) and re.fullmatch('[a-f0-9]{64}',proposal[k]) for k in ('config_digest','backend_digest')),'action_digest')
            require(isinstance(proposal['gates'],dict) and set(proposal['gates'])=={'ci','compatibility','combination'}
                    and all(v is True for v in proposal['gates'].values()),'action_gates')
            require(isinstance(proposal['before_pids'],dict) and set(proposal['before_pids'])=={'backend','gateway'}
                    and all(type(v) is int and v>=0 for v in proposal['before_pids'].values()),'action_identity')
            require(value['action']!='restart' or proposal['target_sha']==proposal['from_sha'],'action_restart_version')
            plan={**proposal,'plan_id':uuid.uuid4().hex,'instance_id':self.instance,'action':value['action'],
                  'expires_at':self.clock()+120,'inflight_policy':'drain'}
            self.plans[value['action']]=json.loads(json.dumps(plan))
            return self.status()

    def execute(self, value):
        require(isinstance(value,dict) and set(value)=={'operation_id','plan','confirmation'},'action_shape')
        operation=value['operation_id'];plan=value['plan']
        require(isinstance(operation,str) and re.fullmatch('[a-f0-9]{32}',operation),'action_identity')
        digest=hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        with self.lock:
            previous=next((e for e in self.entries if e['operation_id']==operation),None)
            if previous:
                require(previous['digest']==digest,'action_id_conflict');return self.status()
            require(isinstance(plan,dict) and plan==self.plans.get(plan.get('action')) and plan['expires_at']>self.clock(),'action_plan_expired')
            require(value['confirmation']==f"{plan['action']}:{self.instance}:{plan['target_sha']}",'action_confirmation')
            require(not any(e['state'] in ('intent','running','unknown') for e in self.entries),'action_unresolved')
            # Trusted adapter rechecks live configuration, target and frozen-recovery state;
            # maintenance must be persistent and all query/model/notice/ops work drained.
            proof=self.adapter.validate(plan)
            require(live_guard(proof),'action_live_guard')
            require(len(self.entries)<200,'action_state_capacity')
            entry={'operation_id':operation,'digest':digest,'plan':plan,'state':'intent','health':None}
            self.entries.append(entry);self.save()
            self.worker=threading.Thread(target=self.run,args=(entry,),daemon=True)
            self.worker.start()
            return self.status()

    def run(self, entry):
        with self.lock:
            entry['state']='running';self.save()
        try:
            plan=entry['plan']
            require(live_guard(self.adapter.validate(plan)),'action_live_guard')
            # This method is implemented by the separately reviewed fixed deployment adapter.
            # It must retain updater transactions/backups/freeze protection and never resume a timer.
            self.adapter.execute(plan['action'],plan['target_sha'],entry['operation_id'])
            health=self.adapter.health()
            valid=(isinstance(health,dict) and health.get('ready') is True
                   and health.get('release_sha')==plan['target_sha'] and health.get('backend_digest')==plan['backend_digest']
                   and all(health.get(role+'_pid')!=pid for role,pid in plan['before_pids'].items())
                   and all(type(health.get(k)) is int and health[k]>0 for k in ('backend_pid','gateway_pid')))
            with self.lock:
                entry['state']='succeeded' if valid else 'unknown'
                entry['health']={k:health[k] for k in ('ready','release_sha','backend_digest','backend_pid','gateway_pid')} if valid else None
                self.save()
        except Exception:
            # An exception cannot prove the side effect did not happen. Read-only reconciliation
            # by the trusted deployment adapter/operator is required; this core never retries.
            with self.lock:
                entry['state']='unknown';entry['health']=None;self.save()


def create_server(controller, *, app_uid, app_gid):
    """Explicit Linux root-service wiring; the deployment adapter is supplied by its installer.

    There is deliberately no application bootstrap/auto-install path. The service owner
    must close this server and the controller, and remove its own socket on shutdown.
    """
    import http.server
    import socket
    import socketserver
    import stat
    import struct
    require(os.name=='posix' and hasattr(socket,'SO_PEERCRED') and os.geteuid()==0,'action_service_privilege')
    require(type(app_uid) is int and app_uid>0 and type(app_gid) is int and app_gid>0,'action_service_identity')
    endpoint=Path('/run/taki-actions/control.sock')
    for path in (endpoint.parent,controller.path.parent):
        info=path.lstat()
        require(stat.S_ISDIR(info.st_mode) and not path.is_symlink() and info.st_uid==0
                and not info.st_mode&0o022,'action_service_directory')
    require(not endpoint.exists() and not endpoint.is_symlink(),'action_service_socket_exists')

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self,*args): pass  # Never log request contents or arbitrary paths.

        def handle_one_request(self):
            self.connection.settimeout(2)
            super().handle_one_request()

        def dispatch(self):
            try:
                _,uid,_=struct.unpack('3i',self.connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
                require(uid in (0,app_uid),'action_peer')
                require(not self.headers.get('Transfer-Encoding'),'action_shape')
                length=int(self.headers.get('Content-Length','0'))
                require(0<=length<=8192,'action_size')
                value=json.loads(self.rfile.read(length)) if length else None
                if self.command=='GET' and self.path=='/v1/actions' and value is None: result=controller.status()
                elif self.command=='POST' and self.path=='/v1/actions/prepare': result=controller.prepare(value)
                elif self.command=='POST' and self.path=='/v1/actions/execute': result=controller.execute(value)
                else: raise ValueError('action_route')
                raw=json.dumps(result,separators=(',',':')).encode();status=200
            except (ValueError,TypeError,KeyError,OSError):
                raw=b'{"error":"action_rejected"}';status=409
            self.send_response(status);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store')
            self.end_headers();self.wfile.write(raw)

        do_GET=dispatch
        do_POST=dispatch

    server=socketserver.UnixStreamServer(str(endpoint),Handler)
    os.chown(endpoint,0,app_gid);os.chmod(endpoint,0o660)
    return server
