# L3: Persist the observed JP activity and one atomically replaced JPEG per activity.
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
import time


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
        return f'jp-{self.event_id}-{self.start}.jpg'

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
        try:
            with (self.directory / period.image_name).open('rb') as source:
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
        period = self.period()
        if period is None:
            return '本期一图流准备中。', None, 'pending'
        now = time.time() if now is None else now
        image = self.image(period)
        if image is None:
            return ('本期一图流准备中。' if period.active(now) else '暂无可用的活动一图流。'), None, 'pending'
        status = '尚未开始' if now < period.start / 1000 else '进行中' if period.active(now) else '已结束'
        return f'日服 · 《{period.title}》 · {status}', image, 'success'
