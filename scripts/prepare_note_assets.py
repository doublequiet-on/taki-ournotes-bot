"""Prepare fixed note artwork before serving queries; never starts QQ."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ournotes_bot.sources.errors import ChartDataError
from ournotes_bot.sources.haneoka.game_assets import prepare_note_assets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-dir', type=Path, required=True,
                        help='Explicit runtime game-assets directory')
    parser.add_argument('--source-dir', type=Path,
                        help='Import a hash-named cache without network access')
    args = parser.parse_args()
    try:
        for path in prepare_note_assets(args.cache_dir, args.source_dir):
            print(f'OK {path.name} {path.stat().st_size} bytes')
    except (OSError, ChartDataError) as exc:
        parser.exit(1, f'Note artwork preparation failed: {exc}\n')


if __name__ == '__main__':
    main()
