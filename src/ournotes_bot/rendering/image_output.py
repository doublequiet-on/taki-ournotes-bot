# L3
# Input: 内部 Pillow Image.Image。
# Output: encode_image 返回预算内 JPEG bytes；无法满足预算时抛 ValueError。
# Pos: Rendering / Core 的统一整图编码器；见 L2-2-Core.md。
# Effects/Dependencies: CPU／内存缩放与编码，使用 logger ournotes_bot.image_output；不落盘或上传图片。

"""Transport-independent image encoding budget for every query renderer.

These are Taki's conservative upload budgets, not claims about QQ hard limits.
Keep the whole image and prefer JPEG quality reduction before further resizing.
"""
import io
import logging
import math

from PIL import Image

MAX_IMAGE_BYTES = 1_500_000  # base64 <= 2 MB before small JSON envelope
MAX_IMAGE_EDGE = 8192
MAX_IMAGE_PIXELS = 12_000_000
logger = logging.getLogger("ournotes_bot.image_output")


def encode_image(image: Image.Image) -> bytes:
    if image.mode != "RGB":
        base = Image.new("RGB", image.size, "white")
        base.paste(image, mask=image.getchannel("A") if "A" in image.getbands() else None)
        image = base
    original = image.size
    factor = min(1, MAX_IMAGE_EDGE / max(image.size), math.sqrt(MAX_IMAGE_PIXELS / (image.width * image.height)))
    if factor < 1:
        image = image.resize((max(1, int(image.width * factor)), max(1, int(image.height * factor))), Image.Resampling.LANCZOS)
    for attempt in range(8):
        for quality, subsampling in ((95, 0), (88, 0), (82, 1), (76, 1)):
            out = io.BytesIO()
            try:
                image.save(out, format="JPEG", quality=quality, subsampling=subsampling, optimize=True)
            except OSError:
                # Some libjpeg builds under-allocate the optimized buffer for
                # high-entropy 4:4:4 images. Retry encoding only, never a send.
                out = io.BytesIO()
                image.save(out, format="JPEG", quality=quality, subsampling=subsampling, optimize=False)
            blob = out.getvalue()
            if len(blob) <= MAX_IMAGE_BYTES:
                logger.debug("图片编码 %sx%s -> %sx%s quality=%d bytes=%d", *original, *image.size, quality, len(blob))
                return blob
        # Scaling is uniform; never crop content, drop table rows or split sends.
        ratio = min(.85, math.sqrt(MAX_IMAGE_BYTES / len(blob)) * .95)
        image = image.resize((max(1, int(image.width * ratio)), max(1, int(image.height * ratio))), Image.Resampling.LANCZOS)
    raise ValueError("Image could not fit the output budget")
