# L3: Persist the observed JP activity and atomically replace JPEGs keyed by schedule.
from contextlib import closing
from dataclasses import asdict, dataclass
import hashlib
import json
import logging
from pathlib import Path
import re
import sqlite3
from tempfile import NamedTemporaryFile
import time

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActivityPeriod:
    event_id: str
    title: str
    start: int
    end: int

    def __post_init__(self):
        if (not isinstance(self.event_id, str) or not re.fullmatch(r'[1-9][0-9]{0,18}', self.event_id)
                or not isinstance(self.title, str) or not 0 < len(self.title) <= 300
                or type(self.start) is not int or type(self.end) is not int
                or not 0 < self.start < self.end < 10**14):
            raise ValueError('invalid activity period')

    @property
    def image_name(self):
        return f'jp-{self.event_id}-{self.start}-{self.end}.jpg'

    def active(self, now):
        return self.start / 1000 <= now <= self.end / 1000


def write_atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, prefix=path.name+'.', suffix='.tmp', delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(content)
            file.close()
            temporary.replace(path)
        finally:
            file.close()
            temporary.unlink(missing_ok=True)


class ActivityInfographicStore:
    image_limit = 3_000_000

    def __init__(self, directory):
        self.directory = Path(directory)
        self.manifest = self.directory / 'current.v1.json'

    def period(self):
        try:
            with self.manifest.open('rb') as source:
                data = source.read(4097)
            if len(data) > 4096:
                raise ValueError('oversize activity manifest')
            return ActivityPeriod(**json.loads(data))
        except FileNotFoundError:
            return None

    def observe(self, period):
        write_atomic(self.manifest, json.dumps(asdict(period), ensure_ascii=False).encode())

    def image(self, period):
        return self._read_image(period.image_name)

    def _read_image(self, name):
        try:
            with (self.directory / name).open('rb') as source:
                data = source.read(self.image_limit + 1)
        except FileNotFoundError:
            return None
        if len(data) <= self.image_limit and data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9'):
            return data
        return None

    def ready(self, period):
        try:
            path = self.directory / period.image_name
            if not 4 <= path.stat().st_size <= self.image_limit:
                return False
            with path.open('rb') as source:
                start = source.read(2)
                source.seek(-2, 2)
                return start == b'\xff\xd8' and source.read(2) == b'\xff\xd9'
        except FileNotFoundError:
            return False

    def publish(self, period, image):
        if not 4 <= len(image) <= self.image_limit or not image.startswith(b'\xff\xd8') or not image.endswith(b'\xff\xd9'):
            raise ValueError('invalid activity image')
        write_atomic(self.directory / period.image_name, image)

    def lookup(self, *, now=None):
        text, image, status, _ = self.lookup_with_name(now=now)
        return text, image, status

    def lookup_with_name(self, *, now=None):
        period = self.period()
        if period is None:
            return '本期一图流准备中。', None, 'pending', None
        now = time.time() if now is None else now
        image = self.image(period)
        if image is None:
            return ('本期一图流准备中。' if period.active(now) else '暂无可用的活动一图流。'), None, 'pending', None
        status = '尚未开始' if now < period.start / 1000 else '进行中' if period.active(now) else '已结束'
        return f'日服 · 《{period.title}》 · {status}', image, 'success', period.image_name

    @property
    def receipts_path(self):
        return self.directory / 'group-image-receipts.v1.sqlite3'

    def remember_delivery(self, scope, digest, name, data):
        # Only called for an acknowledged infographic. Replace refreshes its rowid
        # so the bounded index retains the most recently delivered group/image pairs.
        try:
            with closing(sqlite3.connect(self.receipts_path, timeout=0)) as db, db:
                db.execute("""CREATE TABLE IF NOT EXISTS receipts(
                    scope TEXT, digest TEXT, name TEXT, sha256 TEXT, PRIMARY KEY(scope,digest))""")
                db.execute('INSERT OR REPLACE INTO receipts VALUES(?,?,?,?)',
                           (hashlib.sha256(scope.encode()).hexdigest(), digest, name, hashlib.sha256(data).hexdigest()))
                db.execute('DELETE FROM receipts WHERE rowid IN (SELECT rowid FROM receipts ORDER BY rowid DESC LIMIT -1 OFFSET 256)')
        except (OSError, sqlite3.Error) as exc:
            logger.warning('Infographic delivery index write failed: %s', type(exc).__name__)

    def delivered_image(self, scope, digest):
        try:
            if not self.receipts_path.is_file():
                return None
            with closing(sqlite3.connect(self.receipts_path.absolute().as_uri() + '?mode=ro', uri=True, timeout=0)) as db:
                row = db.execute('SELECT name, sha256 FROM receipts WHERE scope=? AND digest=?',
                                 (hashlib.sha256(scope.encode()).hexdigest(), digest)).fetchone()
            if not row or not isinstance(row[0], str) or not re.fullmatch(r'jp-[1-9][0-9]{0,18}-[0-9]{1,14}-[0-9]{1,14}\.jpg', row[0]):
                return None
            if (self.directory / row[0]).is_symlink():
                return None
            data = self._read_image(row[0])
            if data and hashlib.sha256(data).hexdigest() == row[1]:
                return data
        except (OSError, sqlite3.Error) as exc:
            logger.warning('Infographic delivery index read failed: %s', type(exc).__name__)
        return None
