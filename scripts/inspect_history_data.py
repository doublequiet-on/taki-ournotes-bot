# L3
# Input: Explicit local history copy and optional UTC epoch-ms projection cutoff.
# Output: Read-only inventory, space accounting and proposed identity-row counts.
# Pos: Scripts / task07 history maintenance; see docs/HISTORY_DATA_CONTRACT.md.
# Effects: Reads local files only; never cleans, checkpoints, migrates or activates.
"""Dry-run a single declared history replica; this does not discover all copies."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ournotes_bot.sources.history_retention import inspect_history, inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--before-ms', type=int)
    args = parser.parse_args()
    print(json.dumps({'inventory': inventory({'declared-history': args.source}),
                      'plan': inspect_history(args.source, before_ms=args.before_ms)}, ensure_ascii=True))


if __name__ == '__main__':
    main()
