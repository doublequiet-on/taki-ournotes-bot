# L3: Process ownership of configured business directories before initialization/writes.
import os
from pathlib import Path
from ..config import runtime_data_dir


class DataOwner:
    def __init__(self, settings):
        paths = {settings.cache_file.parent.resolve(), runtime_data_dir().resolve()}
        for field in ('ai_quota_file', 'ai_metrics_file', 'cutoff_history_file', 'moenotes_open_history_file'):
            value = getattr(settings, field)
            if value:
                paths.add(Path(value).resolve().parent)
        self.paths = sorted(paths, key=str)
        self.locks = []
        try:
            for directory in self.paths:
                directory.mkdir(parents=True, exist_ok=True)
                handle = (directory / '.taki-business.lock').open('a+b')
                self.locks.append(handle)
                if os.name == 'nt':
                    import msvcrt
                    if handle.tell() == 0:
                        handle.write(b'0'); handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.close()
            raise

    def close(self):
        for handle in reversed(self.locks):
            handle.close()
        self.locks.clear()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
