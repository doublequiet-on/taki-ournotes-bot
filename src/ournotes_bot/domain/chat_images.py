# L3: Platform-independent, immutable image inputs; no URL fetching or persistence.
from dataclasses import dataclass, field
from typing import Mapping


MAX_ATTACHMENTS = 3
MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_TOTAL_BYTES = 12 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
MAX_IMAGE_SIDE = 8192
IMAGE_TTL_SECONDS = 300
IMAGE_MIMES = frozenset(('image/png', 'image/jpeg', 'image/webp', 'image/gif'))


class ImageError(ValueError):
    """Finite public error code; never includes signed URLs or source bytes."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _identifier(value: object) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= 256
            and value.strip() == value and not any(ord(char) < 32 or ord(char) == 127 for char in value))


def _optional_positive(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or value <= 0:
        raise ImageError('image_invalid_metadata')
    return value


@dataclass(frozen=True)
class Attachment:
    id: str
    url: str = field(repr=False)
    content_type: str
    size: int | None = None
    width: int | None = None
    height: int | None = None

    def __post_init__(self):
        if (not _identifier(self.id) or not isinstance(self.url, str)
                or not 0 < len(self.url) <= 8192 or self.url.strip() != self.url
                or any(ord(char) <= 32 or ord(char) == 127 for char in self.url)):
            raise ImageError('image_invalid_metadata')
        if not isinstance(self.content_type, str) or self.content_type not in IMAGE_MIMES:
            raise ImageError('image_unsupported_type')
        for value in (self.size, self.width, self.height):
            _optional_positive(value)
        if self.size is not None and self.size > MAX_IMAGE_BYTES:
            raise ImageError('image_too_large')
        if any(value is not None and value > MAX_IMAGE_SIDE for value in (self.width, self.height)):
            raise ImageError('image_too_large')
        if self.width is not None and self.height is not None and self.width * self.height > MAX_IMAGE_PIXELS:
            raise ImageError('image_too_large')

    @classmethod
    def from_wire(cls, value: object) -> 'Attachment':
        if not isinstance(value, Mapping) or set(value) - {'id', 'url', 'content_type', 'size', 'width', 'height'}:
            raise ImageError('image_invalid_metadata')
        if not {'id', 'url', 'content_type'} <= set(value):
            raise ImageError('image_invalid_metadata')
        return cls(value['id'], value['url'], value['content_type'],
                   _optional_positive(value.get('size')), _optional_positive(value.get('width')),
                   _optional_positive(value.get('height')))


def parse_attachments(value: object) -> tuple[Attachment, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or len(value) > MAX_ATTACHMENTS:
        raise ImageError('image_count_exceeded')
    result = tuple(item if isinstance(item, Attachment) else Attachment.from_wire(item) for item in value)
    if len({item.id for item in result}) != len(result):
        raise ImageError('image_invalid_metadata')
    if sum(item.size or 0 for item in result) > MAX_TOTAL_BYTES:
        raise ImageError('image_too_large')
    return result


@dataclass(frozen=True)
class LoadedImage:
    attachment_id: str
    mime: str
    width: int
    height: int
    data: bytes = field(repr=False)

    def __post_init__(self):
        if (not _identifier(self.attachment_id) or not isinstance(self.mime, str) or self.mime not in IMAGE_MIMES
                or type(self.width) is not int or type(self.height) is not int
                or min(self.width, self.height) <= 0 or max(self.width, self.height) > MAX_IMAGE_SIDE
                or self.width * self.height > MAX_IMAGE_PIXELS
                or not isinstance(self.data, bytes) or not 0 < len(self.data) <= MAX_IMAGE_BYTES):
            raise ImageError('image_invalid_metadata')


@dataclass(frozen=True)
class ImageReference:
    message_id: str
    speaker_id: str
    timestamp_ms: int
    attachments: tuple[Attachment, ...] = field(repr=False)
    images: tuple[LoadedImage, ...] = field(default=(), repr=False)

    def __post_init__(self):
        if not _identifier(self.message_id) or not _identifier(self.speaker_id) or type(self.timestamp_ms) is not int:
            raise ImageError('image_invalid_metadata')
