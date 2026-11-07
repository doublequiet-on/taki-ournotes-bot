"""Trusted, post-barrier backend entry for task10's inherited ChildSession.

No environment grant or HTTP activation endpoint. The measured host entry owns
settings and calls this only after the two-role wrapper has released its barrier.
"""
import asyncio
from aiohttp import web
from .backend import RECOVERY_BACKEND
from .backend_bootstrap import build_backend
from .runtime_tasks import drain_task


async def serve_recovery_backend(session, args, settings):
    binding = session.binding
    if (binding.get('version') != 1 or binding.get('role') != 'backend'
            or binding.get('instance') != args.instance_id):
        raise ValueError('recovery_entry_binding_mismatch')
    session.assert_current()
    app = build_backend(args, settings, assert_recovery_current=session.assert_current)
    backend = app[RECOVERY_BACKEND]
    runner = web.AppRunner(app, access_log=None)

    async def stop():
        backend.revoke_recovery()
        if backend._recovery_close is None:
            # setup may fail before aiohttp installs the lifecycle callbacks.
            await backend.close()
            await runner.cleanup()
            return
        facts = await backend.quiesce_recovery()
        try:
            phase = session.state()
            if phase != 'quiesce':
                raise RuntimeError('recovery_stop_not_ordered')
            phase = session.report('quiesced', facts)
            while phase == 'drain':
                await asyncio.sleep(.05)
                phase = session.state()
            if phase != 'close':
                raise RuntimeError('recovery_stop_not_ordered')
            closed = await backend.close_recovery()
            session.report('closed', closed)
        finally:
            # A lost controller still requires local writer ownership to drain.
            # No receipt is manufactured on this path; the host must retain its
            # isolation/lock until independent stop evidence becomes available.
            await backend.close_recovery()
            await runner.cleanup()

    try:
        await runner.setup()
        try:
            session.assert_current()
        except Exception:
            if session.state() != 'quiesce':
                raise
            return
        await web.TCPSite(runner, '127.0.0.1', args.port).start()
        while not backend.recovery_revoked:
            try:
                session.assert_current()
            except Exception:
                if session.state() != 'quiesce':
                    raise
                break
            await asyncio.sleep(.05)
    finally:
        backend.revoke_recovery()
        await drain_task(asyncio.create_task(stop(), name='recovery-entry-stop'))
