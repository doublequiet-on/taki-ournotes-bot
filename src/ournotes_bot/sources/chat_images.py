# L3: Bounded QQ image download and short-lived scoped image cache.
# Effects: HTTPS only to verified QQ CDN; transient bytes/URLs stay in memory.
import asyncio
import ipaddress
import math
import socket
import time
import warnings
from collections import OrderedDict
from io import BytesIO
from urllib.parse import urlsplit

import aiohttp
from PIL import Image, UnidentifiedImageError

from ..domain.chat_images import (
    Attachment, ImageError, ImageReference, LoadedImage, IMAGE_MIMES, IMAGE_TTL_SECONDS,
    MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS, MAX_IMAGE_SIDE, MAX_TOTAL_BYTES, parse_attachments,
)


QQ_IMAGE_HOSTS = frozenset(('multimedia.nt.qq.com.cn',))
_FORMATS = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp', 'GIF': 'image/gif'}


def validate_image_url(url: str) -> str:
    """Only the observed official QQ attachment origin/path, never arbitrary URLs."""
    if not isinstance(url, str):
        raise ImageError('image_untrusted_source')
    try:
        parsed = urlsplit(url)
        if (not isinstance(url, str) or len(url) > 8192 or any(ord(c) <= 32 or ord(c) == 127 for c in url)
                or parsed.scheme != 'https' or parsed.hostname not in QQ_IMAGE_HOSTS
                or parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443)
                or parsed.fragment or parsed.path != '/download' or not parsed.query
                or parsed.netloc.lower() not in QQ_IMAGE_HOSTS | {host + ':443' for host in QQ_IMAGE_HOSTS}):
            raise ImageError('image_untrusted_source')
    except (ValueError, TypeError) as exc:
        if isinstance(exc, ImageError):
            raise
        raise ImageError('image_untrusted_source') from None
    return url


class PublicQQResolver(aiohttp.abc.AbstractResolver):
    """Validate the exact DNS results TCPConnector connects to, without a preflight race."""

    def __init__(self, resolver=None):
        self._resolver = resolver or aiohttp.resolver.ThreadedResolver()

    async def resolve(self, host, port=0, family=socket.AF_INET):
        if host not in QQ_IMAGE_HOSTS:
            raise ImageError('image_untrusted_source')
        addresses = await self._resolver.resolve(host, port, family)
        if not addresses:
            raise ImageError('image_not_available')
        for address in addresses:
            try:
                ip = ipaddress.ip_address(address['host'])
            except (ValueError, KeyError, TypeError):
                raise ImageError('image_untrusted_source') from None
            # Reject an entire mixed answer rather than selecting a convenient public IP.
            if not ip.is_global or ip.is_multicast or ip.is_unspecified:
                raise ImageError('image_untrusted_source')
        return addresses

    async def close(self):
        await self._resolver.close()


def decode_image(attachment: Attachment, data: bytes, response_type: str) -> LoadedImage:
    """Check the decoded format, dimensions, MIME and full stream before model use."""
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ImageError('image_too_large' if data else 'image_invalid_content')
    if response_type.partition(';')[0].strip().lower() != attachment.content_type:
        raise ImageError('image_type_mismatch')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                mime = _FORMATS.get(image.format)
                width, height = image.size
                if mime not in IMAGE_MIMES or mime != attachment.content_type:
                    raise ImageError('image_type_mismatch')
                if (width <= 0 or height <= 0 or max(width, height) > MAX_IMAGE_SIDE
                        or width * height > MAX_IMAGE_PIXELS):
                    raise ImageError('image_too_large')
                if getattr(image, 'is_animated', False):
                    raise ImageError('image_unsupported_animation')
                if ((attachment.width is not None and attachment.width != width)
                        or (attachment.height is not None and attachment.height != height)
                        or (attachment.size is not None and attachment.size != len(data))):
                    raise ImageError('image_metadata_mismatch')
                image.verify()
            # verify() checks structure; load() also detects truncated pixel streams.
            with Image.open(BytesIO(data)) as image:
                image.load()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ImageError('image_too_large') from None
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        if isinstance(exc, ImageError):
            raise
        raise ImageError('image_invalid_content') from None
    return LoadedImage(attachment.id, mime, width, height, data)


def _live_timestamp(timestamp_ms: int, now: float):
    if (type(timestamp_ms) is not int or not math.isfinite(now)
            or timestamp_ms > (now + 30) * 1000 or timestamp_ms <= (now - IMAGE_TTL_SECONDS) * 1000):
        raise ImageError('image_not_available')


class ImageLoader:
    def __init__(self, *, wall_clock=time.time, monotonic=time.monotonic):
        self._wall_clock = wall_clock
        self._monotonic = monotonic

    async def load_many(self, attachments, *, timestamp_ms: int, deadline: float) -> tuple[LoadedImage, ...]:
        attachments = parse_attachments(attachments)
        _live_timestamp(timestamp_ms, self._wall_clock())
        for attachment in attachments:
            validate_image_url(attachment.url)
        if not attachments:
            return ()
        remaining = min(10.0, deadline - self._monotonic(),
                        timestamp_ms / 1000 + IMAGE_TTL_SECONDS - self._wall_clock())
        if not math.isfinite(remaining) or remaining <= 0:
            raise ImageError('image_not_available')
        image_deadline = self._monotonic() + remaining
        resolver = PublicQQResolver()
        connector = aiohttp.TCPConnector(resolver=resolver, use_dns_cache=False, limit=1)
        try:
            async with aiohttp.ClientSession(connector=connector, trust_env=False, auto_decompress=False,
                                             timeout=aiohttp.ClientTimeout(total=remaining),
                                             headers={'Accept': ', '.join(sorted(IMAGE_MIMES))}) as session:
                images, total = [], 0
                for attachment in attachments:
                    # Each request also inherits the total turn deadline, not a new ten seconds.
                    _live_timestamp(timestamp_ms, self._wall_clock())
                    timeout = min(image_deadline - self._monotonic(), deadline - self._monotonic(),
                                  timestamp_ms / 1000 + IMAGE_TTL_SECONDS - self._wall_clock())
                    if timeout <= 0:
                        raise ImageError('image_not_available')
                    async with session.get(attachment.url, allow_redirects=False,
                                           timeout=aiohttp.ClientTimeout(total=timeout)) as response:
                        if response.status != 200:
                            raise ImageError('image_not_available')
                        if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                            raise ImageError('image_invalid_content')
                        if response.content_length is not None and response.content_length > MAX_IMAGE_BYTES:
                            raise ImageError('image_too_large')
                        data = bytearray()
                        async for chunk in response.content.iter_chunked(64 * 1024):
                            total += len(chunk)
                            if len(data) + len(chunk) > MAX_IMAGE_BYTES or total > MAX_TOTAL_BYTES:
                                raise ImageError('image_too_large')
                            data.extend(chunk)
                        images.append(decode_image(attachment, bytes(data), response.headers.get('Content-Type', '')))
                        _live_timestamp(timestamp_ms, self._wall_clock())
                        if self._monotonic() >= image_deadline:
                            raise ImageError('image_not_available')
                return tuple(images)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            # Transport exceptions may include the signed URL. Do not propagate their text/cause.
            raise ImageError('image_not_available') from None
        finally:
            await resolver.close()


class ImageCache:
    """Five-minute, bounded memory only; callers supply the full instance/app/chat scope."""

    def __init__(self, *, wall_clock=time.time, monotonic=time.monotonic,
                 max_entries=256, max_bytes=32 * 1024 * 1024):
        if type(max_entries) is not int or max_entries <= 0 or type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError('invalid image cache limits')
        self._clock, self._max_entries, self._max_bytes = wall_clock, max_entries, max_bytes
        self._monotonic = monotonic
        self._entries = OrderedDict()
        self._expires = {}

    def _expire(self):
        now = self._clock()
        for key, entry in list(self._entries.items()):
            if (entry.timestamp_ms <= (now - IMAGE_TTL_SECONDS) * 1000
                    or self._monotonic() >= self._expires[key]):
                del self._entries[key]
                del self._expires[key]

    def remember(self, scope: str, message_id: str, speaker_id: str, timestamp_ms: int, attachments, images=()):
        if not isinstance(scope, str) or not scope:
            raise ImageError('image_invalid_metadata')
        _live_timestamp(timestamp_ms, self._clock())
        attachments = parse_attachments(attachments)
        for attachment in attachments:
            validate_image_url(attachment.url)
        images = tuple(images)
        if (len(images) not in (0, len(attachments)) or any(not isinstance(item, LoadedImage) for item in images)
                or (images and tuple(item.attachment_id for item in images) != tuple(item.id for item in attachments))):
            raise ImageError('image_invalid_metadata')
        if sum(len(item.data) for item in images) > min(MAX_TOTAL_BYTES, self._max_bytes):
            raise ImageError('image_too_large')
        self._expire()
        key = (scope, message_id)
        entry = ImageReference(message_id, speaker_id, timestamp_ms, attachments, images)
        previous = self._entries.get(key)
        if previous is not None and (previous.speaker_id != speaker_id or previous.timestamp_ms != timestamp_ms
                                     or previous.attachments != attachments):
            raise ImageError('image_identity_mismatch')
        self._entries[key] = entry
        # Re-observing the same event cannot extend retention; clock rollback cannot keep it alive.
        self._expires[key] = min(self._expires.get(key, float('inf')),
                                 self._monotonic() + min(IMAGE_TTL_SECONDS,
                                     timestamp_ms / 1000 + IMAGE_TTL_SECONDS - self._clock()))
        self._entries.move_to_end(key)
        while (len(self._entries) > self._max_entries
               or sum(len(image.data) for item in self._entries.values() for image in item.images) > self._max_bytes):
            evicted, _ = self._entries.popitem(last=False)
            del self._expires[evicted]

    def get(self, scope: str, message_id: str) -> ImageReference:
        self._expire()
        entry = self._entries.get((scope, message_id))
        if entry is None:
            raise ImageError('image_not_available')
        return entry

    def forget(self, scope: str, message_id: str):
        self._entries.pop((scope, message_id), None)
        self._expires.pop((scope, message_id), None)
