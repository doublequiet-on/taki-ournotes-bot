"""Fetch the reviewed sample assets and build the rarity frames. Requires Pillow."""
from bisect import bisect_left
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import Request, urlopen

from PIL import Image

ROOT = Path(__file__).resolve().parent


def save(target, data):
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=target.parent, delete=False) as tmp:
        tmp.write(data)
    Path(tmp.name).replace(target)


def pixels(im):
    return sha256(im.convert("RGBA").tobytes()).hexdigest()


def fetch(target, spec):
    def matches(data):
        if "sha256" in spec:
            return sha256(data).hexdigest() == spec["sha256"]
        with Image.open(BytesIO(data)) as im:
            return list(im.size) == spec["size"] and pixels(im) == spec["pixels"]

    if target.is_file() and matches(target.read_bytes()):
        return
    req = Request(spec["url"], headers={"User-Agent": "Taki member card design preview"})
    with urlopen(req, timeout=60) as response:
        data = response.read(48_000_001)
    if len(data) > 48_000_000:
        raise ValueError(f"Asset exceeds preview budget: {target.name}")
    if spec.get("trim"):
        with Image.open(BytesIO(data)) as im:
            buf = BytesIO()
            im.crop(im.convert("RGBA").getbbox()).save(buf, "PNG")
            data = buf.getvalue()
    if not matches(data):
        raise ValueError(f"Unexpected content for {target.name} from {spec['url']}")
    save(target, data)


def sliced(image, size):
    half = image.width // 2

    def spans(length):
        return [(0,half,0,half), (half,half+1,half,length-half), (half,2*half,length-half,length)]

    layer = Image.new("RGBA",size)
    for x0,x1,l,r in spans(size[0]):
        for y0,y1,t,b in spans(size[1]):
            patch = image.crop((x0,y0,x1,y1)).resize((r-l,b-t),Image.Resampling.NEAREST)
            layer.paste(patch,(l,t))
    return layer


def frames(cache, gradients):
    w,h = 224,294
    with Image.open(cache/"FrameSquare_6px.png") as src:
        mask = sliced(src.convert("RGBA"), (w,h)).getchannel("A")
    with Image.open(cache/"FormationSupportCardOutline.png") as src:
        outline = sliced(src.convert("RGBA"), (w+4,h+4))
    for rarity, stops in gradients.items():
        positions = [s[0] for s in stops]
        ramp = Image.new("RGB",(1,h))
        for y in range(h):
            t = 1-y/(h-1)
            b = min(bisect_left(positions,t),len(stops)-1)
            a = max(0,b-1)
            f = min(1,max(0,(t-positions[a])/(positions[b]-positions[a]))) if a!=b else 0
            ramp.putpixel((0,y),tuple(round(255*u+(255*v-255*u)*f) for u,v in zip(stops[a][1],stops[b][1])))
        face = ramp.resize((w,h))
        face.putalpha(mask)
        result = Image.new("RGBA",outline.size)
        result.alpha_composite(face,(2,2))
        result.alpha_composite(outline)
        buf = BytesIO()
        result.save(buf,"PNG")
        save(cache/f"frame-{rarity}.png",buf.getvalue())


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=ROOT/".cache")
    args = parser.parse_args()
    manifest = json.loads((ROOT/"examples/media.json").read_bytes())
    jobs = [(args.cache/name, spec) for name,spec in manifest["images"].items()]
    jobs += [(args.cache/"fonts"/name,spec) for name,spec in manifest["fonts"].items()]
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(fetch,path,spec) for path,spec in jobs]
        for future in futures:
            future.result()
    frames(args.cache,manifest["frames"])
    print(f"{len(jobs)} verified assets and {len(manifest['frames'])} frames in {args.cache}")


if __name__ == "__main__":
    main()
