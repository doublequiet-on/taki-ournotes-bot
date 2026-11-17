"""Entry point executed by the pinned Hermes interpreter in a disposable home."""

import json
import os
from pathlib import Path
from queue import Queue
import sys
from threading import Thread
from weakref import WeakSet


def enforce_loopback(port):
    sockets = WeakSet()

    def check(event, args):
        if event == 'socket.bind':
            sock, address = args
            if os.name != 'nt' or address != ('127.0.0.1', 0):
                raise PermissionError('Hermes worker listeners are disabled')
            # Windows asyncio creates its wakeup socket pair using private
            # loopback sockets. Only ports bound by this worker are accepted.
            sockets.add(sock)
        elif event == 'socket.connect':
            address = args[1]
            if (not isinstance(address, tuple) or address[:2] != ('127.0.0.1', port)) and not any(
                    sock.fileno() >= 0 and address == sock.getsockname() for sock in sockets):
                raise PermissionError('Hermes worker network access is limited to the Taki relay')
        elif event == 'socket.getaddrinfo':
            if args[:2] != ('127.0.0.1', port):
                raise PermissionError('Hermes worker DNS access is disabled')
        elif event in ('subprocess.Popen', 'os.system', 'os.exec', 'os.posix_spawn'):
            raise PermissionError('Hermes worker subprocesses are disabled')

    sys.addaudithook(check)


def exit_on_parent_close():
    os.read(0, 1)
    os._exit(1)


def main():
    boot = json.loads(sys.stdin.buffer.readline())
    port = boot['port']
    enforce_loopback(port)
    assignment = None
    if boot.get('standby'):
        assignment = Queue(maxsize=1)

        def read_assignment():
            try:
                assignment.put(json.loads(sys.stdin.buffer.readline()))
            except (OSError, ValueError):
                os._exit(1)
            exit_on_parent_close()

        Thread(target=read_assignment, daemon=True).start()
    else:
        Thread(target=exit_on_parent_close, daemon=True).start()
    profile = Path(os.environ['HERMES_HOME'])
    profile.mkdir(mode=0o700)
    (profile / 'config.yaml').write_text(json.dumps({
        'model': {'streaming': False},
        'compression': {'enabled': False},
        'memory': {'memory_enabled': False, 'user_profile_enabled': False},
        'auxiliary': {'title_generation': {'enabled': False, 'model_upgrade_enabled': False}},
        'fallback_providers': [],
        'models_dev': {'url': f'http://127.0.0.1:{port}/unused-model-catalog'},
        'agent': {'api_max_retries': 1, 'auto_recovery_cycles': 0},
    }), encoding='utf-8')
    from run_agent import AIAgent

    if assignment is None:
        request = boot
    else:
        Path('ready').touch()
        request = assignment.get()

    agent = AIAgent(
        base_url=f'http://127.0.0.1:{port}/v1', api_key=boot['key'], provider='custom',
        api_mode='chat_completions', model=request['model'], enabled_toolsets=[], max_iterations=2,
        quiet_mode=True, skip_context_files=True, load_soul_identity=False, skip_memory=True,
        skip_background_review=True, session_db=None, fallback_model={}, cwd=str(Path.cwd()),
        ephemeral_system_prompt=request['instructions'],
    )
    result = agent.run_conversation(request['input'], conversation_history=request['history'])
    response = json.loads(result['final_response'])
    (Path.cwd() / 'receipt.json').write_text(json.dumps(response, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()
