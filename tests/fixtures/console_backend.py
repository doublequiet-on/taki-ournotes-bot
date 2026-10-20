"""Isolated Console browser fixture. No QQ, model or external source access."""
import asyncio
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from aiohttp import web
from ournotes_bot.application.backend import Backend, create_app
from ournotes_bot.application.backend_protocol import PROTOCOL, CAPABILITY, digest
from ournotes_bot.application.replies import PreparedReply
from ournotes_bot.config import Settings

os.environ.update(QQ_APP_ID='',QQ_APP_SECRET='',AI_API_KEY='')
def offline(event,args):
    local = (event == 'socket.connect' and isinstance(args[1], tuple) and args[1][0] == '127.0.0.1')
    if event in ('socket.connect','socket.sendto','socket.getaddrinfo') and not local:
        raise AssertionError('fixture forbids outbound network')
sys.addaudithook(offline)
root=Path(sys.argv[1])/f'run-{os.getpid()}'; root.mkdir(parents=True,exist_ok=True)
repository=SimpleNamespace(capture_catalog=lambda:SimpleNamespace(cache_state='stale',songs=[],last_successful_sync_at=None))
parser=SimpleNamespace(metrics_snapshot=lambda:{'available':False,'reason_code':'metrics_not_collected'})
backend=Backend(repository,parser,Settings('','','fixture',root/'cache.json',6),instance_id='fixture',app_id='app',ledger_path=root/'ledger.sqlite3')
backend.gate.prepare=AsyncMock(return_value=PreparedReply('测试夹具，非生产数据',business_status='success'))
async def seed(app):
    backend.ready=True
    await backend.register('fixture-gateway')
    now=time.time()-1
    await backend.submit(dict(protocol=PROTOCOL,request_id=digest(['fixture','app','qq','group','fixture-group','fixture-user','message']),
        instance_id='fixture',app_id='app',platform='qq',kind='group',conversation_id='fixture-group',user_id='fixture-user',
        message_id='message',timestamp_ms=int(now*1000),body='/帮助',capability=CAPABILITY,gateway_epoch='fixture-gateway',ingress_seq=1))
app=create_app(backend,'fixture-backend-token-000000000000')
app.on_startup.append(seed)
web.run_app(app,host='127.0.0.1',port=18765,print=None)
