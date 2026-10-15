"""Prepare and verify public query artwork before switching a release; no QQ."""
import argparse
import time
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ournotes_bot.sources.haneoka.catalog import CatalogRepository
from ournotes_bot.sources.haneoka.prepare_catalog_assets import prepare
from ournotes_bot.sources.haneoka.game_assets import MANIFEST,prepare_note_assets


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-dir',type=Path,required=True)
    parser.add_argument('--catalog',type=Path,required=True)
    parser.add_argument('--snapshot',action='store_true',help='Use the specified verified catalog without refresh')
    args=parser.parse_args()
    for name in MANIFEST:
        prepare_note_assets(args.cache_dir/'game-assets',names=(name,))
    repository=CatalogRepository(args.catalog)
    if args.snapshot:repository.saved=repository._read()
    else:
        repository.load(refresh=True)
        if repository.state not in ('fresh','cached'):raise RuntimeError('Catalog preparation did not obtain a fresh snapshot')
    report=prepare(repository.saved,args.cache_dir/'haneoka-asset-cache',time.time())
    print(f"Prepared {len(report['assets'])} catalog images and {len(MANIFEST)} game assets")


if __name__=='__main__':main()
