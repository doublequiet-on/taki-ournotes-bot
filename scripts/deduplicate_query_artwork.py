"""Share equal prepared PNGs without changing any URL path. Run as the cache owner."""
import argparse
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from ournotes_bot.sources.haneoka.catalog_assets import cache_lock


def identity(st):
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns,
            st.st_uid, st.st_gid, st.st_mode)


def read_image(path):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | getattr(os, 'O_NOATIME', 0))
    with os.fdopen(fd, 'rb') as stream:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or not 0 < st.st_size <= 6_000_000:
            return None
        raw = stream.read(st.st_size+1)
        if len(raw) == st.st_size and identity(st) == identity(os.fstat(fd)):
            return st, raw


def compact(cache, apply):
    if cache.resolve() != cache or cache.name != 'haneoka-asset-cache':
        raise ValueError('Expected the canonical prepared artwork directory')
    representatives = {}
    count = bytes_saved = changed = 0
    with (tempfile.TemporaryDirectory(dir=cache) if apply else nullcontext()) as tmp:
        staged = Path(tmp)/'linked' if apply else None
        for path in sorted(cache.iterdir()):
            if not re.fullmatch(r'[a-f0-9]{64}\.png', path.name) or path.is_symlink():
                continue
            try:
                value = read_image(path)
                if value is None:
                    continue
                st, raw = value
                key = (hashlib.sha256(raw).digest(), st.st_mode, st.st_uid, st.st_gid)
                source = representatives.setdefault(key, path)
                if source == path:
                    continue
                # Holding the directory lock only for the current pair lets new
                # downloads publish between files. Readers never need this lock.
                with cache_lock(cache, exclusive=True):
                    old = path.lstat()
                    if identity(old) != identity(st):
                        changed += 1
                        continue
                    src = source.lstat()
                    if src.st_ino == old.st_ino:
                        continue
                    if stat.S_ISLNK(src.st_mode) or (src.st_mode,src.st_uid,src.st_gid) != key[1:]:
                        continue
                    original = read_image(source)
                    if original is None or original[1] != raw:
                        changed += 1
                        continue
                    if os.listxattr(source) or os.listxattr(path):
                        continue
                    if apply:
                        os.link(source, staged, follow_symlinks=False)
                        os.replace(staged, path)
                    count += 1
                    if old.st_nlink == 1:
                        bytes_saved += old.st_blocks*512
            except FileNotFoundError:
                changed += 1
            finally:
                if staged is not None:
                    staged.unlink(missing_ok=True)
    return {'applied':apply,'linked_paths':count,'allocated_bytes':bytes_saved,'changed_paths':changed}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cache',type=Path)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    print(json.dumps(compact(args.cache,args.apply)))
