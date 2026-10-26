# L3
# Input: Explicit local backup and output paths; no application configuration.
# Output: Verified compressed copy, legacy copy, or diagnostic JSON.
# Pos: Scripts / cutoff history maintenance; see L2.md.
# Effects: Local files only, no service operations or implicit replacement.
"""Compress or export an offline history backup without touching the input."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ournotes_bot.sources.history_archive import diagnose, export_old, migrate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('compress', 'export', 'check'))
    parser.add_argument('source', type=Path)
    parser.add_argument('target', nargs='?', type=Path)
    args = parser.parse_args()
    if args.action == 'check':
        if args.target is not None:
            parser.error('check takes no output path')
        result = diagnose(args.source)
    else:
        if args.target is None:
            parser.error('a separate output path is required')
        result = (migrate if args.action == 'compress' else export_old)(args.source, args.target)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    main()
