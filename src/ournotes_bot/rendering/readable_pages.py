# L3
# Input: Ordered measured rows, a subset painter and optional candidate preparation scope.
# Output: Complete encoded image parts at the requested font/image scale.
# Pos: Rendering / Core; measured single-image-first layout.
# Effects: Runs caller-owned preparation scopes and Pillow encoding; no filtering or sending.
"""Pack complete rows before drawing; never shrink text to fit the encoder."""
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Callable, ContextManager

from PIL import Image

from .image_output import MAX_IMAGE_EDGE, MAX_IMAGE_PIXELS, ImageBudgetError, encode_image


@dataclass(frozen=True)
class ImagePart:
    image: bytes
    start: int
    stop: int


def render_parts(heights: tuple[int, ...], width: int, fixed_height: int,
                 paint: Callable[[int, int, int], Image.Image], *, max_height: int | None = None,
                 page_scope: Callable[[int, int], ContextManager[None]] | None = None) -> tuple[ImagePart, ...]:
    """Largest fitting ordered prefix, with byte-budget checks before publication.

    Row positions refer to the captured input, not record IDs. The painter owns
    no shared output file. Encoding takes ownership of each temporary canvas.
    Byte size is not assumed monotonic: reductions are tested in descending row
    count so no larger candidate inside the pixel budget is silently skipped.
    An optional scope owns one maximal candidate's assets across encoding retries;
    it exits before the next page starts. Painters close canvases they fail to return.
    """
    if not heights:
        return ()
    if width <= 0 or width > MAX_IMAGE_EDGE or fixed_height < 0 or any(h <= 0 for h in heights):
        raise ValueError("invalid measured layout")
    limit = min(MAX_IMAGE_EDGE, MAX_IMAGE_PIXELS // width)
    if max_height is not None:
        # A single complete content block may exceed the preferred page height.
        limit = min(limit, max(max_height, fixed_height + max(heights)))
    parts, start = [], 0
    while start < len(heights):
        stop, height = start, fixed_height
        while stop < len(heights) and height + heights[stop] <= limit:
            height += heights[stop]
            stop += 1
        if stop == start:
            raise ImageBudgetError("One complete row exceeds the readable image budget")
        with page_scope(start, stop) if page_scope else nullcontext():
            while stop > start:
                # Preparation/drawing errors are not byte-budget retries. Once
                # paint succeeds, the encoder owns its independent canvas.
                image = paint(start, stop, height)
                try:
                    raw = encode_image(image, close_input=True, preserve_size=True)
                except ImageBudgetError:
                    stop -= 1
                    height -= heights[stop]
                else:
                    parts.append(ImagePart(raw, start, stop))
                    start = stop
                    break
            else:
                raise ImageBudgetError("One complete row exceeds the readable byte budget")
    return tuple(parts)
