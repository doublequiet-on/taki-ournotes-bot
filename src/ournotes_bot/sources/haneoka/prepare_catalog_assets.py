# L3
# Input: Validated captured catalog and an explicit persistent image-cache directory.
# Output: Complete verified artwork cache and per-release integrity manifest.
# Pos: Sources / Haneoka; deployment preparation before the service is switched.
# Effects: Anonymous allowlisted downloads, bounded workers and atomic cache writes.
"""Prepare catalog artwork without loading credentials or starting the bot."""
import hashlib
import io
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from PIL import Image
from .catalog import validate
from .catalog_assets import is_catalog_asset, download_asset, asset_url
from .info_catalog import convert_info
from .ranking_art import cards_for
from ..errors import ChartDataError


def catalog_urls(saved, now):
    records, _ = validate(saved, now)
    info = convert_info(saved['documents'], saved['identities']['jp'], '')
    urls = set()
    def visit(value):
        if isinstance(value,str):
            if is_catalog_asset(value):urls.add(value)
        elif isinstance(value,dict):
            for child in value.values():visit(child)
        elif isinstance(value,(tuple,list)):
            for child in value:visit(child)
    for group in records:
        for record in group:visit(asdict(record))
    visit(asdict(info))
    for server in ('jp','tw'):
        visit(cards_for(saved,server))
    # Regional band/event art is also used by the four-server rankings.
    for server,doc in saved['documents'].items():
        release=saved['identities'][server]['releaseId']
        def paths(value):
            if isinstance(value,str) and value.startswith('/assets/'):
                try:urls.add(asset_url(value,release))
                except ValueError:pass  # Non-image paths are outside this cache.
            elif isinstance(value,dict):
                for child in value.values():paths(child)
            elif isinstance(value,(tuple,list)):
                for child in value:paths(child)
        for name in ('bands','events'):paths(doc.get(name,{}))
    return tuple(sorted(urls))


def _check(raw):
    if not raw or len(raw)>6_000_000:raise ValueError('Artwork byte budget')
    with Image.open(io.BytesIO(raw)) as image:
        if image.width*image.height>20_000_000:raise ValueError('Artwork pixel budget')
        image.load()
    return hashlib.sha256(raw).hexdigest()


def _atomic(path,raw):
    fd,name=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(raw)
        os.replace(name,path)
    finally:Path(name).unlink(missing_ok=True)


def prepare(saved,cache_dir,now,*,download=download_asset,workers=6):
    cache=Path(cache_dir);cache.mkdir(parents=True,exist_ok=True)
    manifest=cache/'prepared-artwork-v1.json'
    prior={}
    if manifest.is_file():
        try:prior=json.loads(manifest.read_text('utf-8')).get('assets',{})
        except (OSError,ValueError):pass
    urls=catalog_urls(saved,now)
    def one(url):
        path=cache/(hashlib.sha256(url.encode()).hexdigest()+'.png')
        if path.is_file() and path.stat().st_size<=6_000_000:
            try:
                raw=path.read_bytes();digest=_check(raw)
                # Adopt existing validated application cache; subsequent preparations
                # check the saved content hash as well as decoding the source image.
                if url not in prior or prior[url]['sha256']==digest:
                    return url,{'sha256':digest,'bytes':len(raw),'file':path.name}
            except (OSError,ValueError,KeyError):pass
        for attempt in range(2):
            try:
                raw=download(url,timeout=45);digest=_check(raw)
                break
            except (OSError,ChartDataError):
                if attempt:raise
        _atomic(path,raw)
        return url,{'sha256':digest,'bytes':len(raw),'file':path.name}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        assets=dict(pool.map(one,urls))
    report={'schema':1,'identities':saved['identities'],'assets':assets}
    _atomic(manifest,json.dumps(report,ensure_ascii=False,sort_keys=True).encode('utf-8'))
    return report
