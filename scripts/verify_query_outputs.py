"""Capture actual reply images/timings using a public catalog and isolated cache.

No QQ account, upload or send occurs. A first run with an empty runtime includes
network downloads; --offline measures existing disk assets and warm rendering.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--command', action='append')
    args = parser.parse_args()
    os.environ.update(QQ_APP_ID='', QQ_APP_SECRET='', AI_API_KEY='')
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
    from PIL import Image
    from ournotes_bot import config, visuals
    from ournotes_bot.data import SongRepository
    from ournotes_bot.platforms.qq import qq
    from ournotes_bot.sources.haneoka import chart_data
    from ournotes_bot.sources.haneoka.catalog import CatalogRepository

    snapshot = json.loads(args.snapshot.read_text('utf-8'))['snapshot']
    args.output.mkdir(parents=True, exist_ok=True)
    runtime = args.runtime.resolve()
    repository = SongRepository('', args.output / 'catalog.json', data_source='haneoka', chart_source='haneoka')
    def no_refresh(*_):
        raise OSError('verification uses the captured public catalog')
    repository._catalog_repository = CatalogRepository(args.snapshot, clock=lambda: snapshot['fetched_at'] + 1, fetch=no_refresh)
    repository.load()
    if args.offline:
        def block(event, values):
            # Windows asyncio also connects its local wakeup socket.
            if event == 'socket.connect' and values[1][0] not in ('127.0.0.1', '::1'):
                raise OSError('verification network disabled')
            if event == 'socket.getaddrinfo' and values[0] not in ('localhost', '127.0.0.1', '::1'):
                raise OSError('verification network disabled')
        sys.addaudithook(block)
    report = {'offline': args.offline, 'runtime_files_before': len(list(runtime.rglob('*'))) if runtime.exists() else 0,
              'identities': snapshot['identities'], 'results': []}
    commands = args.command or ['/查曲 odd dice', '/查谱面 odd dice']
    with patch.object(config, 'runtime_data_dir', return_value=runtime), patch.object(visuals, 'runtime_data_dir', return_value=runtime), patch.object(chart_data, 'runtime_data_dir', return_value=runtime):
        for index, command in enumerate(commands, 1):
            for run in ('first', 'warm'):
                start = time.perf_counter()
                reply = qq._prepare_reply(command, repository, None)
                elapsed = time.perf_counter() - start
                if reply is None:
                    raise ValueError('unrecognized verification command: ' + command)
                pages = reply.pages or (reply,)
                files = []
                for part, page in enumerate(pages, 1):
                    if not page.image:
                        continue
                    path = args.output / f'{index}-{run}-{part}.jpg'
                    path.write_bytes(page.image)
                    with Image.open(io.BytesIO(page.image)) as image:
                        size = image.size
                    files.append({'path': str(path.resolve()), 'size': size, 'bytes': len(page.image),
                                  'sha256': hashlib.sha256(page.image).hexdigest()})
                row = {'command': command, 'run': run, 'seconds': round(elapsed, 3), 'images': files,
                       'text': reply.text or pages[0].text}
                report['results'].append(row)
                (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                print(f'{command} / {run}: {elapsed:.3f}s, {len(files)} images', flush=True)
    if any(not row['images'] for row in report['results']):
        raise SystemExit('Some commands produced no image; inspect report.json and the captured text.')


if __name__ == '__main__':
    main()
