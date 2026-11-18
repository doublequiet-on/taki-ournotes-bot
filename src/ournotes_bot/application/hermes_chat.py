"""Run one isolated Hermes step for a Taki-owned, authorizable model request."""

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field, replace
from hmac import compare_digest
import json
import logging
import os
from pathlib import Path
import re
from shutil import rmtree
import sqlite3
from tempfile import mkdtemp
from time import monotonic
from uuid import uuid4

from aiohttp import web

from ..domain.knowledge import KnowledgeError
from ..domain.model_calls import ModelError
from ..domain.story import StoryError
from .backend_protocol import ProtocolError, require
from .hermes_standby import StandbyLane


logger = logging.getLogger(__name__)
WORKER = Path(__file__).resolve().with_name('hermes_worker.py')
SCRATCH = re.compile(r'run-[a-f0-9]{32}-[a-z0-9_]{8}')


def text_context(body):
    history, instructions = [], []
    for item in body.get('input', body.get('messages', [])):
        role, content = item.get('role'), item.get('content')
        if isinstance(content, list):
            content = '\n'.join(part['text'] for part in content if isinstance(part.get('text'), str))
        if not isinstance(content, str) or not content:
            continue
        if role in ('developer', 'system'):
            instructions.append(content)
        elif role in ('user', 'assistant'):
            history.append({'role': role, 'content': content})
    while history and history[-1]['role'] != 'user':
        history.pop()
    message = history.pop()['content'] if history else 'Continue the authorized Taki model step.'
    return dict(input=message, history=history, instructions='\n\n'.join(instructions))


def worker_environment(directory):
    system = ('SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'PROGRAMFILES',
              'PROGRAMFILES(X86)', 'PROGRAMDATA', 'SYSTEMDRIVE', 'PROCESSOR_ARCHITECTURE')
    env = {key: os.environ[key] for key in system if key in os.environ}
    for name in ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'XDG_CACHE_HOME',
                 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME', 'TMP', 'TEMP', 'TMPDIR'):
        env[name] = str(directory)
    env.update(HERMES_HOME=str(directory / 'hermes'), NO_PROXY='*', PYTHONUTF8='1')
    return env


@dataclass
class Run:
    work: object
    validate: object
    stopped: asyncio.Event = field(default_factory=asyncio.Event)
    cleaned: asyncio.Event = field(default_factory=asyncio.Event)
    error: Exception | None = None
    provider: asyncio.Task | None = None


class StepWorker:
    def __init__(self, python, directory, key):
        self.python, self.directory, self.key = python, directory, key
        self.runner = None
        self.process = None
        self.assignment = None

    @property
    def alive(self):
        return self.process is not None and self.process.returncode is None

    async def respond(self, request):
        if not compare_digest(request.headers.get('Authorization', ''), 'Bearer ' + self.key):
            return web.Response(status=401)
        if self.assignment is None:
            return web.Response(status=409)
        run, model, receipt_key, dispatch = self.assignment
        payload = await request.json()
        if (not isinstance(payload, dict) or payload.get('model') != model
                or payload.get('tools') or payload.get('functions') or payload.get('stream')):
            return web.Response(status=403)
        if run.provider is not None or run.work.ended.is_set() or run.stopped.is_set():
            return web.Response(status=409)
        run.provider = asyncio.create_task(dispatch())
        try:
            result = await asyncio.shield(run.provider)
        except (ProtocolError, ModelError, KnowledgeError, StoryError, asyncio.TimeoutError):
            return web.Response(status=403)
        usage = result.usage
        receipt = json.dumps({'key': receipt_key,
                              'text': result.text if result.status == 'ok' and not result.tool_calls else None},
                             ensure_ascii=False)
        return web.json_response({
            'id': 'chatcmpl-' + receipt_key, 'object': 'chat.completion', 'created': 1, 'model': model,
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': receipt}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': usage.input_total or 0, 'completion_tokens': usage.output_total or 0,
                      'total_tokens': (usage.input_total or 0)+(usage.output_total or 0)},
        })

    async def start(self, boot):
        app = web.Application(client_max_size=8 * 1024 * 1024)
        app.router.add_post('/v1/chat/completions', self.respond)
        self.runner = web.AppRunner(app, access_log=None, shutdown_timeout=.05)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        with (self.directory / 'worker.log').open('wb') as log:
            spawn = asyncio.create_task(asyncio.create_subprocess_exec(
                str(self.python), '-B', '-I', str(WORKER), cwd=self.directory,
                stdin=asyncio.subprocess.PIPE, stdout=log, stderr=log, env=worker_environment(self.directory)))
            try:
                self.process = await asyncio.shield(spawn)
            except asyncio.CancelledError:
                self.process = await spawn
                raise
        await self.write(dict(boot, port=self.runner.addresses[0][1], key=self.key))

    async def write(self, data):
        self.process.stdin.write(json.dumps(data, ensure_ascii=False).encode() + b'\n')
        await self.process.stdin.drain()

    async def wait(self, run, deadline):
        process = asyncio.create_task(self.process.wait())
        tasks = [process, asyncio.create_task(run.work.ended.wait()), asyncio.create_task(run.stopped.wait())]
        try:
            done, _ = await asyncio.wait(tasks, timeout=max(0, deadline-monotonic()),
                                         return_when=asyncio.FIRST_COMPLETED)
            if not done:
                raise asyncio.TimeoutError()
            if process in done and self.process.returncode == 0:
                return json.loads((self.directory / 'receipt.json').read_text(encoding='utf-8'))
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self):
        try:
            if self.process is not None:
                if self.process.returncode is None:
                    with suppress(ProcessLookupError):
                        self.process.kill()
                self.process.stdin.close()
                await self.process.wait()
        finally:
            if self.runner is not None:
                await self.runner.cleanup()


class StandbyWorker(StepWorker):
    def __init__(self, owner):
        super().__init__(owner.python, owner.directory(uuid4().hex), uuid4().hex)
        self.owner = owner

    async def close(self):
        try:
            await super().close()
        finally:
            self.owner.remove(self.directory)


class HermesChat:
    def __init__(self, python, scratch, pilot_group='', *, compact=False):
        self.python, self.scratch = python, scratch
        self.pilot_group = pilot_group
        self.dialogue = StandbyLane(self.prewarm, 2, compact=compact)
        self.proactive = StandbyLane(self.prewarm, 1, compact=compact)
        self.active = {}
        self.owner = None
        self.cleanup_failed = False
        if not (python and scratch and python.is_absolute() and scratch.is_absolute() and python.is_file()):
            return
        owner = None
        try:
            scratch.mkdir(mode=0o700, parents=True, exist_ok=True)
            if scratch.is_symlink() or (os.name != 'nt' and
                    (scratch.stat().st_mode & 0o077 or scratch.stat().st_uid != os.getuid())):
                raise OSError('Hermes runtime directory must be private')
            lock = scratch / '.owner.sqlite3'
            if lock.is_symlink():
                raise OSError('Invalid Hermes owner file')
            owner = sqlite3.connect(lock, timeout=0)
            owner.execute('BEGIN EXCLUSIVE')
            for entry in scratch.iterdir():
                if SCRATCH.fullmatch(entry.name):
                    rmtree(entry)
            self.owner = owner
        except (OSError, sqlite3.Error):
            if owner is not None:
                owner.close()
            logger.warning('Hermes scratch setup failed')

    @property
    def available(self):
        return self.owner is not None and not self.cleanup_failed and self.python.is_file()

    def close(self):
        if self.owner is not None:
            self.owner.close()
            self.owner = None

    def start_pilot(self):
        if self.available:
            self.dialogue.start()
            self.proactive.start()

    async def stop_pilot(self):
        await asyncio.gather(self.dialogue.stop(), self.proactive.stop())

    def directory(self, key):
        return Path(mkdtemp(dir=self.scratch, prefix=f'run-{key}-'))

    def remove(self, directory):
        try:
            rmtree(directory)
        except OSError:
            self.cleanup_failed = True
            logger.error('Hermes scratch cleanup failed')

    async def prewarm(self):
        worker = StandbyWorker(self)
        try:
            await worker.start({'standby': True})
            deadline = monotonic()+30
            while not (worker.directory / 'ready').is_file():
                if not worker.alive:
                    raise OSError('Hermes standby exited')
                if monotonic()>=deadline:
                    raise asyncio.TimeoutError()
                await asyncio.sleep(.025)
            return worker
        except BaseException:
            await worker.close()
            raise

    async def await_cleanup(self, *, scope=None):
        stopped = []
        for run in tuple(self.active.values()):
            if scope is not None:
                if run.work.request.scope == scope and run.work.ended.is_set():
                    stopped.append(run)
                continue
            try:
                run.validate()
            except (ProtocolError, KnowledgeError, StoryError, ModelError) as exc:
                run.error = exc
                run.stopped.set()
                stopped.append(run)
        await asyncio.gather(*(run.cleaned.wait() for run in stopped))
        require(not self.cleanup_failed, 'chat_hermes_cleanup_failed', 503)

    async def request(self, work, profile, body, deadline, validate, dispatch):
        require(self.available, 'chat_hermes_unavailable', 503)
        request_key = uuid4().hex
        run = Run(work, validate)
        directory = None
        self.active[work.delivery] = run
        returned = None
        failure = None
        try:
            if getattr(work,'hermes_pilot',False):
                returned = await self.pilot_worker(run, request_key, profile, body, deadline, dispatch)
            else:
                directory = self.directory(request_key)
                returned = await self.worker(run, directory, request_key, profile, body, deadline, dispatch)
        except ProtocolError as exc:
            if exc.code=='chat_hermes_busy':
                require(not self.cleanup_failed, 'chat_hermes_cleanup_failed', 503)
                validate()
            raise
        except (OSError, ValueError, asyncio.TimeoutError) as exc:
            failure = ProtocolError('chat_hermes_timeout' if isinstance(exc, asyncio.TimeoutError)
                                    else 'chat_hermes_unavailable', 503)
        finally:
            if directory is not None:
                self.remove(directory)
            run.cleaned.set()
            self.active.pop(work.delivery, None)
        if run.provider is None:
            validate()
            if run.error:
                raise run.error
            raise failure or ProtocolError('chat_hermes_unavailable', 503)
        result = await asyncio.shield(run.provider)
        text = result.text if result.status == 'ok' and not result.tool_calls else None
        if self.cleanup_failed or run.error or failure or returned != dict(key=request_key, text=text):
            code = 'chat_hermes_cleanup_failed' if self.cleanup_failed else 'chat_hermes_receipt_invalid'
            # Preserve provider usage and actions after an actual dispatch, even
            # if the worker was stopped or the receipt became unusable.
            return replace(result, status='error', error_code=code, text=None, tool_calls=(), output_items=())
        return replace(result, text=returned['text']) if text is not None else result

    async def pilot_worker(self, run, key, profile, body, deadline, dispatch):
        work = run.work
        proactive = work.proactive is not None and not work.continuation
        lane = self.proactive if proactive else self.dialogue
        named = work.proactive is None
        limit = min(deadline, monotonic()+2) if named else deadline
        # A compact lane refills only after releasing the decision worker.
        # Give an admitted follow-on step its remaining execution window.
        wait = not proactive or (lane.compact and work.hermes_provider_started)
        work.hermes_claim_started = monotonic()
        claim = asyncio.create_task(lane.claim(limit,priority=int(not named),wait=wait))
        stopped = [asyncio.create_task(wait) for wait in (work.ended.wait(), run.stopped.wait())]
        worker = None
        try:
            await asyncio.wait([claim,*stopped], return_when=asyncio.FIRST_COMPLETED)
            if work.ended.is_set() or run.stopped.is_set():
                return None
            worker = claim.result()
            work.hermes_claim_finished = monotonic()
            require(not self.cleanup_failed, 'chat_hermes_cleanup_failed', 503)
            run.validate()
            worker.assignment = (run, profile.model, key, dispatch)
            await worker.write(dict(text_context(body), model=profile.model))
            return await worker.wait(run, deadline)
        finally:
            for task in (claim,*stopped):
                task.cancel()
            await asyncio.gather(claim,*stopped,return_exceptions=True)
            if worker is None and not claim.cancelled() and claim.exception() is None:
                worker = claim.result()
            if worker is not None:
                await lane.release(worker)

    async def worker(self, run, directory, key, profile, body, deadline, dispatch):
        worker = StepWorker(self.python, directory, key)
        worker.assignment = (run, profile.model, key, dispatch)
        try:
            await worker.start(dict(text_context(body), model=profile.model))
            return await worker.wait(run, deadline)
        finally:
            await worker.close()
