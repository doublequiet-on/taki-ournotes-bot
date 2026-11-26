"""Prepare verified Haneoka source and artwork for the offline member list."""
from bisect import bisect_left
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import Request, urlopen

from PIL import Image

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".cache"


def fetch(entry):
    target, url, digest = entry
    if target.is_file() and sha256(target.read_bytes()).hexdigest() == digest:
        return
    with urlopen(Request(url, headers={"User-Agent": "Taki member list preview"}), timeout=45) as res:
        data = res.read()
    if sha256(data).hexdigest() != digest:
        raise ValueError(f"Content differs from the reviewed sample: {url}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=target.parent, delete=False) as tmp:
        tmp.write(data)
    Path(tmp.name).replace(target)


def stretch_sprite(image, size):
    half = image.width // 2

    def spans(length):
        return [(0, half, 0, half), (half, half + 1, half, length - half),
                (half, 2 * half, length - half, length)]

    layer = Image.new("RGBA", size)
    for x0, x1, left, right in spans(size[0]):
        for y0, y1, top, bottom in spans(size[1]):
            patch = image.crop((x0, y0, x1, y1))
            layer.paste(patch.resize((right - left, bottom - top), Image.Resampling.NEAREST), (left, top))
    return layer


def prepare_frames(media, gradients):
    width, height = 224, 294
    with Image.open(media / "FrameSquare_6px.png") as image:
        mask = stretch_sprite(image.convert("RGBA"), (width, height)).getchannel("A")
    with Image.open(media / "FormationSupportCardOutline.png") as image:
        outline = stretch_sprite(image.convert("RGBA"), (width + 4, height + 4))
    for rarity, stops in gradients.items():
        positions = [position for position, _ in stops]
        ramp = Image.new("RGB", (1, height))
        for y in range(height):
            pos = 1 - y / (height - 1)
            hi = min(bisect_left(positions, pos), len(stops) - 1)
            lo = max(0, hi - 1)
            mix = min(1, max(0, (pos - positions[lo]) / (positions[hi] - positions[lo]))) if hi != lo else 0
            ramp.putpixel((0, y), tuple(round(255*a + (255*b - 255*a)*mix)
                                        for a, b in zip(stops[lo][1], stops[hi][1])))
        face = ramp.resize((width, height))
        face.putalpha(mask)
        frame = Image.new("RGBA", outline.size)
        frame.alpha_composite(face, (2, 2))
        frame.alpha_composite(outline)
        frame.save(media / f"frame-{rarity}.png")


def main():
    upstream = json.loads((ROOT / "examples/haneoka.json").read_bytes())
    catalog = json.loads((ROOT / "examples/media.json").read_bytes())
    source = f"https://raw.githubusercontent.com/haneoka-gakuen/haneoka/{upstream['commit']}"
    jobs = [(CACHE / "haneoka" / name, f"{source}/{name}", digest)
            for name, digest in upstream["files"].items()]
    media = CACHE / "media"
    for name, spec in catalog["images"].items():
        url = f"https://haneoka.org{spec['path']}?release={catalog['release']}"
        jobs.append((media / name, url, spec["sha256"]))
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(fetch, jobs))
    prepare_frames(media, catalog["gradients"])
    print(f"Verified {len(jobs)} files and built {len(catalog['gradients'])} rarity frames.")


if __name__ == "__main__":
    main()
